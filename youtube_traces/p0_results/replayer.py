import argparse
import socket
import json
import time
import threading
import os
from collections import defaultdict

# --- CONFIGURATION ---
PORT = 5001
CHUNK_SIZE = 4096


def run_server(port: int) -> None:
    """
    Simple TCP server:
    1. Accept a connection
    2. Read a single line: "SIZE_IN_BYTES\\n"
    3. Send that many random bytes
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(128)

    while True:
        try:
            conn, addr = s.accept()
            t = threading.Thread(
                target=handle_server_connection, args=(conn,), daemon=True
            )
            t.start()
        except Exception:
            # Ignore transient accept errors
            continue


def handle_server_connection(conn: socket.socket) -> None:
    try:
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(1024)
            if not chunk:
                return
            data += chunk

        line, _ = data.split(b"\n", 1)
        bytes_to_send = int(line.decode().strip())

        sent = 0
        payload = os.urandom(CHUNK_SIZE)
        while sent < bytes_to_send:
            remaining = bytes_to_send - sent
            to_send = min(len(payload), remaining)
            conn.sendall(payload[:to_send])
            sent += to_send
    except Exception:
        # Ignore per-connection errors
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def run_client(schedule_file: str, server_ips, mode: str, bitrate_mbps: float) -> None:
    """
    Client side:
    - Reads a schedule JSON
    - Sorts streams by start time
    - Launches worker threads for streams
    - Records metrics including time-series throughput
    """
    with open(schedule_file, "r") as f:
        streams = json.load(f)

    # Sort by the first event start time
    streams.sort(key=lambda s: s["events"][0].get("_debug_start", 0.0))

    state = {
        "total_bytes": 0,
        "lock": threading.Lock(),
        "buffers": defaultdict(float),  # stream_id -> seconds buffered
        "stall_time": 0.0,
        "active_stream_id": None,
        "active_stream_start": -1.0,
        "throughputs": [],  # instantaneous throughput samples (Mbps)
        "throughput_timeseries": [],  # List of (timestamp, mbps)
        # per-stream stats for fairness
        "per_stream_bytes": defaultdict(int),
        "per_stream_first_ts": {},
        "per_stream_last_ts": {},
    }

    stop_playback = threading.Event()
    playback_thread = threading.Thread(
        target=playback_simulation, args=(state, stop_playback), daemon=True
    )
    playback_thread.start()

    start_time = time.time()
    threads = []

    for stream in streams:
        s_id = str(stream.get("stream_id", "0"))
        try:
            sid_int = int(s_id)
        except Exception:
            sid_int = 0

        # Round-robin mapping of stream to server IP
        server_ip = server_ips[sid_int % len(server_ips)]

        t = threading.Thread(
            target=handle_stream,
            args=(stream, s_id, server_ip, state, mode, bitrate_mbps),
        )
        threads.append(t)
        t.start()
        time.sleep(0.005)  # small stagger

    for t in threads:
        t.join()

    stop_playback.set()
    playback_thread.join(timeout=1.0)

    total_duration = time.time() - start_time
    if total_duration <= 0:
        total_duration = 1e-6

    # --- Aggregate metrics ---
    avg_tp = (state["total_bytes"] * 8) / (total_duration * 1_000_000)  # Mbps

    if len(state["throughputs"]) > 1:
        mean_tp = sum(state["throughputs"]) / len(state["throughputs"])
        var_tp = sum((x - mean_tp) ** 2 for x in state["throughputs"]) / len(
            state["throughputs"]
        )
        jitter = var_tp ** 0.5
    else:
        jitter = 0.0

    rebuffering_ratio = (
        state["stall_time"] / total_duration if total_duration > 0 else 0.0
    )

    # --- Process Timeseries ---
    # Normalize timestamps relative to experiment start
    raw_series = state["throughput_timeseries"]
    # Sort by timestamp to be safe
    raw_series.sort(key=lambda x: x[0])
    normalized_series = [
        (t - start_time, val) for (t, val) in raw_series
    ]

    # --- Per-stream avg throughput + fairness ---
    per_stream_rates_mbps = {}
    for sid, bytes_count in state["per_stream_bytes"].items():
        first_ts = state["per_stream_first_ts"].get(sid)
        last_ts = state["per_stream_last_ts"].get(sid)
        if first_ts is None or last_ts is None:
            continue
        duration = last_ts - first_ts
        if duration <= 0:
            continue
        rate_mbps = (bytes_count * 8) / (duration * 1_000_000)
        per_stream_rates_mbps[sid] = rate_mbps

    fairness_index = None
    if len(per_stream_rates_mbps) >= 2:
        rates = list(per_stream_rates_mbps.values())
        num = sum(rates) ** 2
        den = len(rates) * sum(r * r for r in rates)
        fairness_index = num / den if den > 0 else 0.0
    elif len(per_stream_rates_mbps) == 1:
        fairness_index = 1.0

    results = {
        "avg_throughput_mbps": avg_tp,
        "jitter_mbps": jitter,
        "rebuffering_ratio": rebuffering_ratio,
        "total_stalls": state["stall_time"],
        "fairness_index": fairness_index,
        "per_stream_avg_throughput_mbps": per_stream_rates_mbps,
        "throughput_timeseries": normalized_series,  # Added for plotting
    }

    print("JSON_RESULT:" + json.dumps(results))


def handle_stream(
    stream: dict,
    stream_id: str,
    server_ip: str,
    state: dict,
    mode: str,
    bitrate_mbps: float,
) -> None:
    events = stream.get("events", [])
    if not events:
        return

    first_start = events[0].get("_debug_start", 0.0)

    # Swipe logic
    with state["lock"]:
        if first_start > state["active_stream_start"]:
            state["active_stream_start"] = first_start
            state["active_stream_id"] = stream_id

    for ev in events:
        action = ev.get("action")

        if action == "sleep":
            time.sleep(ev.get("duration_sec", 0.0))

        elif action == "download":
            size_mb = ev.get("size_mb", 0.0)
            if size_mb <= 0:
                continue
            size_bytes = int(size_mb * 1024 * 1024)

            t0 = time.time()
            recvd = 0
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((server_ip, PORT))
                s.sendall(f"{size_bytes}\n".encode())

                while recvd < size_bytes:
                    data = s.recv(CHUNK_SIZE)
                    if not data:
                        break
                    recvd += len(data)
            except Exception:
                pass
            finally:
                try:
                    s.close()
                except Exception:
                    pass

            dt = time.time() - t0
            if dt <= 0:
                dt = 1e-6

            tp_mbps = (size_mb * 8) / dt
            video_time_gained = (size_mb * 8) / bitrate_mbps

            now = time.time()
            with state["lock"]:
                state["total_bytes"] += recvd
                state["throughputs"].append(tp_mbps)
                # Capture timeseries data: (timestamp, mbps)
                state["throughput_timeseries"].append((now, tp_mbps))

                if mode == "long":
                    state["buffers"]["0"] += video_time_gained
                else:
                    state["buffers"][stream_id] += video_time_gained

                state["per_stream_bytes"][stream_id] += recvd
                if stream_id not in state["per_stream_first_ts"]:
                    state["per_stream_first_ts"][stream_id] = now
                state["per_stream_last_ts"][stream_id] = now


def playback_simulation(state: dict, stop_event: threading.Event) -> None:
    last_check = time.time()

    while not stop_event.is_set():
        time.sleep(0.1)
        now = time.time()
        delta = now - last_check
        last_check = now

        with state["lock"]:
            active_id = state["active_stream_id"]
            target_id = active_id if active_id is not None else "0"

            current_buffer = state["buffers"].get(target_id, 0.0)

            if current_buffer > 0:
                new_buf = current_buffer - delta
                if new_buf < 0:
                    new_buf = 0.0
                state["buffers"][target_id] = new_buf
            else:
                if active_id is not None or len(state["buffers"]) > 0:
                    state["stall_time"] += delta


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True, choices=["server", "client"])
    parser.add_argument("--schedule", default="trace.json")
    parser.add_argument("--ips", default="10.0.0.2")
    parser.add_argument("--mode", default="short", choices=["long", "short"])
    parser.add_argument("--bitrate", type=float, default=1.2)

    args = parser.parse_args()

    if args.role == "server":
        run_server(PORT)
    else:
        ip_list = [ip.strip() for ip in args.ips.split(",") if ip.strip()]
        run_client(args.schedule, ip_list, args.mode, args.bitrate)