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
BITRATE_LADDER = [(0.5, "240p"), (1.0, "360p"), (2.5, "720p"), (5.0, "1080p"), (8.0, "1440p")]

# --- TUNING KNOBS ---
PREFETCH_TARGET = 5.0
PANIC_THRESHOLD = 5.0
MAX_BUFFER_ACTIVE = 20.0

# NEW: bandwidth estimation window size
TP_WINDOW_LEN = 10


# ============================================================
#          BUFFER-AWARE BANDWIDTH ESTIMATOR (NEW)
# ============================================================
def update_bandwidth_estimate(state, tp_mbps):
    """
    More realistic estimator:
    - EWMA throughput
    - Sliding window conservative floor
    - Buffer-aware optimism/pessimism
    - Stall-aware clamping
    """

    active_id = state["active_base_id"]
    buf_active = state["buffers"][active_id]

    # ---- 1. Maintain sliding throughput window ----
    tp_window = state["tp_window"]
    tp_window.append(tp_mbps)
    if len(tp_window) > TP_WINDOW_LEN:
        tp_window.pop(0)

    safe_floor = min(tp_window) if tp_window else tp_mbps

    # ---- 2. EWMA that depends on buffer health ----
    if buf_active < 3.0:
        alpha = 0.10   # cautious
    elif buf_active < 10.0:
        alpha = 0.20
    else:
        alpha = 0.30   # more responsive

    current_est = state["app_bandwidth_est"]
    ewma_est = (1 - alpha) * current_est + alpha * tp_mbps

    # ---- 3. Blend EWMA with conservative floor ----
    blended_est = 0.5 * ewma_est + 0.5 * safe_floor

    # ---- 4. Adjust for buffer risk ----
    if buf_active < 2.0:
        risk_factor = 0.7
    elif buf_active < 5.0:
        risk_factor = 0.85
    elif buf_active < 12.0:
        risk_factor = 1.0
    else:
        risk_factor = 1.1

    buffer_aware_est = blended_est * risk_factor

    # ---- 5. Clamp down if a new stall happened ----
    stall_time = state["stall_time"]
    if stall_time > state["last_stall_time"]:
        buffer_aware_est = min(buffer_aware_est, tp_mbps * 0.8)
        state["last_stall_time"] = stall_time

    # ---- 6. Final sanity clamp ----
    buffer_aware_est = max(0.1, min(buffer_aware_est, 100.0))

    # Save results
    state["app_bandwidth_est"] = buffer_aware_est
    state["tp_window"] = tp_window



# ============================================================
#                       SERVER LOGIC
# ============================================================
def run_server(port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(128)
    while True:
        try:
            conn, _ = s.accept()
            threading.Thread(target=handle_server_connection, args=(conn,), daemon=True).start()
        except:
            continue


def handle_server_connection(conn: socket.socket) -> None:
    try:
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(1024)
            if not chunk:
                return
            data += chunk
        bytes_to_send = int(data.split(b"\n", 1)[0].decode().strip())

        sent = 0
        payload = os.urandom(min(bytes_to_send, 1024 * 1024))
        while sent < bytes_to_send:
            to_send = min(len(payload), bytes_to_send - sent)
            conn.sendall(payload[:to_send])
            sent += to_send
    except:
        pass
    finally:
        try:
            conn.close()
        except:
            pass



# ============================================================
#                      CLIENT LOGIC
# ============================================================
def run_client(schedule_file: str, server_ips, mode: str) -> None:
    with open(schedule_file, "r") as f:
        streams = json.load(f)

    # --- 1: Sort streams by play order ---
    def get_sort_key(s):
        try:
            return int(s["stream_id"].split('_')[0])
        except:
            return 9999

    streams.sort(key=get_sort_key)

    stream_order = []
    swipe_map = {}
    seen = set()

    for s in streams:
        base_id = s["stream_id"].split('_')[0]
        if base_id not in seen:
            stream_order.append(base_id)
            seen.add(base_id)
            swipe_map[base_id] = s.get("playback_start_sec", 0.0)

    # --- 2: Shared State ---
    state = {
        "lock": threading.Lock(),
        "start_time": time.time(),
        "total_bytes": 0,

        "buffers": defaultdict(float),
        "stall_time": 0.0,

        "active_video_index": 0,
        "active_base_id": stream_order[0] if stream_order else "0",
        "stream_order": stream_order,
        "swipe_map": swipe_map,

        "throughputs": [],
        "throughput_timeseries": [],
        "buffer_timeseries": [],
        "bitrate_timeseries": [],

        # NEW fields for estimator
        "app_bandwidth_est": 2.5,
        "tp_window": [],
        "last_stall_time": 0.0,

        "per_stream_bytes": defaultdict(int),
        "per_stream_first_ts": {},
        "per_stream_last_ts": {},
    }

    # --- 3: Start playback simulation ---
    stop_playback = threading.Event()
    playback_thread = threading.Thread(target=playback_simulation, args=(state, stop_playback), daemon=True)
    playback_thread.start()

    # --- 4: Start per-stream workers ---
    threads = []
    for stream in streams:
        sid = stream.get("stream_id", "0")
        try:
            idx = int(sid.split('_')[0])
        except:
            idx = 0

        server_ip = server_ips[idx % len(server_ips)]

        t = threading.Thread(target=handle_stream_logic, args=(stream, sid, server_ip, state))
        threads.append(t)
        t.start()
        time.sleep(0.005)

    for t in threads:
        t.join()

    stop_playback.set()
    playback_thread.join(timeout=1.0)

    # --- Compute final metrics (unchanged) ---
    total_duration = max(1e-6, time.time() - state["start_time"])
    avg_tp = (state["total_bytes"] * 8) / (total_duration * 1e6)

    jitter = 0.0
    if len(state["throughputs"]) > 1:
        mean = sum(state["throughputs"]) / len(state["throughputs"])
        var = sum((x - mean) ** 2 for x in state["throughputs"]) / len(state["throughputs"])
        jitter = var ** 0.5

    avg_qual = 0.0
    if state["bitrate_timeseries"]:
        avg_qual = sum(b for _, b in state["bitrate_timeseries"]) / len(state["bitrate_timeseries"])

    # Per-stream throughput
    per_stream_rates = {}
    for sid, b_count in state["per_stream_bytes"].items():
        start_t = state["per_stream_first_ts"].get(sid, 0)
        last_t = state["per_stream_last_ts"].get(sid, 0)
        dur = last_t - start_t
        if dur > 0.1 and b_count > 1000:
            per_stream_rates[sid] = (b_count * 8) / (dur * 1e6)

    fairness = 1.0
    if len(per_stream_rates) >= 2:
        rates = list(per_stream_rates.values())
        num = sum(rates) ** 2
        den = len(rates) * sum(r * r for r in rates)
        fairness = num / den if den > 0 else 0

    results = {
        "avg_throughput_mbps": avg_tp,
        "avg_bitrate_selected_mbps": avg_qual,
        "jitter_mbps": jitter,
        "rebuffering_ratio": state["stall_time"] / total_duration,
        "total_stalls": state["stall_time"],
        "fairness_index": fairness,
        "per_stream_avg_throughput_mbps": per_stream_rates,
        "throughput_timeseries": [(t - state["start_time"], v) for (t, v) in state["throughput_timeseries"]],
        "buffer_timeseries": state["buffer_timeseries"],
        "bitrate_timeseries": state["bitrate_timeseries"],
    }

    print("JSON_RESULT:" + json.dumps(results))



# ============================================================
#                PLAYBACK SIMULATION LOGIC
# ============================================================
def playback_simulation(state, stop_event):
    last_real_time = time.time()

    while not stop_event.is_set():
        time.sleep(0.1)
        now = time.time()
        delta = now - last_real_time
        last_real_time = now

        sim_time = now - state["start_time"]

        with state["lock"]:
            idx = state["active_video_index"]
            if idx + 1 < len(state["stream_order"]):
                next_sid = state["stream_order"][idx + 1]
                start_time_real = state["swipe_map"].get(next_sid, 99999)

                if sim_time >= start_time_real:
                    state["active_video_index"] = idx + 1
                    state["active_base_id"] = next_sid

            active_id = state["active_base_id"]

            if state["buffers"][active_id] > 0:
                state["buffers"][active_id] = max(0.0, state["buffers"][active_id] - delta)
            else:
                state["stall_time"] += delta

            state["buffer_timeseries"].append((sim_time, state["buffers"][active_id]))



# ============================================================
#                     ABR DECISION LOGIC
# ============================================================
def get_abr_decision(state, is_active, base_id):
    est = state["app_bandwidth_est"]
    buf = state["buffers"][base_id]

    if is_active:
        target = est * 0.9
        if buf < 5.0:
            target = est * 0.5
    else:
        active_id = state["active_base_id"]
        if state["buffers"][active_id] < PANIC_THRESHOLD:
            return BITRATE_LADDER[0][0]
        target = est * 0.5

    selected = BITRATE_LADDER[0][0]
    for b, _ in BITRATE_LADDER:
        if b <= target:
            selected = b
        else:
            break
    return selected



# ============================================================
#                STREAM DOWNLOAD WORKER
# ============================================================
def handle_stream_logic(stream, full_sid, server_ip, state):
    base_id = full_sid.split('_')[0]

    try:
        my_idx = state["stream_order"].index(base_id)
    except:
        my_idx = -1

    chunks = [ev for ev in stream.get("events", []) if ev["action"] == "download"]

    for chunk in chunks:

        # ---------- 1. GATING LOOP ----------
        while True:
            with state["lock"]:

                active_idx = state["active_video_index"]
                curr_buf = state["buffers"][base_id]
                is_active = (my_idx == active_idx)

            if my_idx < active_idx:
                return

            if is_active:
                if curr_buf < MAX_BUFFER_ACTIVE:
                    break

            elif my_idx == active_idx + 1:
                if chunk.get("is_background", False):
                    if curr_buf < PREFETCH_TARGET:
                        break

            time.sleep(0.1)

        # ---------- 2. ABR DECISION ----------
        with state["lock"]:
            is_active = (my_idx == state["active_video_index"])
            bitrate = get_abr_decision(state, is_active, base_id)

        duration = chunk["video_duration_sec"]
        size_bytes = int((duration * bitrate * 1e6) / 8)

        # ---------- 3. DOWNLOAD ----------
        t0 = time.time()
        recvd = 0
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((server_ip, PORT))
            s.sendall(f"{size_bytes}\n".encode())

            while recvd < size_bytes:
                with state["lock"]:
                    if my_idx < state["active_video_index"]:
                        s.close()
                        return

                d = s.recv(CHUNK_SIZE)
                if not d:
                    break
                recvd += len(d)
        except:
            pass
        finally:
            try:
                s.close()
            except:
                pass

        # ---------- 4. UPDATE STATE ----------
        dt = max(1e-6, time.time() - t0)
        tp_mbps = (recvd * 8) / (dt * 1e6)

        with state["lock"]:

            if recvd > 0:
                sim_time = time.time() - state["start_time"]

                state["total_bytes"] += recvd
                state["throughputs"].append(tp_mbps)
                state["throughput_timeseries"].append((time.time(), tp_mbps))

                state["bitrate_timeseries"].append((sim_time, bitrate))

                frac = recvd / max(1, size_bytes)
                state["buffers"][base_id] += (duration * frac)

                state["per_stream_bytes"][full_sid] += recvd
                if full_sid not in state["per_stream_first_ts"]:
                    state["per_stream_first_ts"][full_sid] = time.time()
                state["per_stream_last_ts"][full_sid] = time.time()

                # NEW: buffer-aware estimator
                update_bandwidth_estimate(state, tp_mbps)



# ============================================================
#                    MAIN ENTRY POINT
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--schedule", default="trace.json")
    parser.add_argument("--ips", default="10.0.0.2")
    parser.add_argument("--mode", default="short")
    args = parser.parse_args()

    if args.role == "server":
        run_server(PORT)
    else:
        ip_list = [ip.strip() for ip in args.ips.split(",") if ip.strip()]
        run_client(args.schedule, ip_list, args.mode)
