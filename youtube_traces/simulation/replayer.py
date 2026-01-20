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

def run_server(port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(128)
    while True:
        try:
            conn, _ = s.accept()
            threading.Thread(target=handle_server_connection, args=(conn,), daemon=True).start()
        except: continue

def handle_server_connection(conn: socket.socket) -> None:
    try:
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(1024)
            if not chunk: return
            data += chunk
        bytes_to_send = int(data.split(b"\n", 1)[0].decode().strip())
        
        sent = 0
        payload = os.urandom(min(bytes_to_send, 1024 * 1024)) 
        while sent < bytes_to_send:
            to_send = min(len(payload), bytes_to_send - sent)
            conn.sendall(payload[:to_send])
            sent += to_send
    except: pass
    finally:
        try: conn.close()
        except: pass

def run_client(schedule_file: str, server_ips, mode: str) -> None:
    with open(schedule_file, "r") as f: streams = json.load(f)

    # 1. Sort streams to determine Play Order
    def get_sort_key(s):
        try: return int(s["stream_id"].split('_')[0])
        except: return 9999
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

    # 2. Shared State
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
        
        # New Metrics for Plotting
        "buffer_timeseries": [],   # [(time, buffer_level), ...]
        "bitrate_timeseries": [],  # [(time, bitrate), ...]
        
        "app_bandwidth_est": 2.5,
        "per_stream_bytes": defaultdict(int),
        "per_stream_first_ts": {},
        "per_stream_last_ts": {},
    }

    # 3. Start Playback
    stop_playback = threading.Event()
    playback_thread = threading.Thread(target=playback_simulation, args=(state, stop_playback), daemon=True)
    playback_thread.start()

    # 4. Launch Streams
    threads = []
    for stream in streams:
        sid = stream.get("stream_id", "0")
        try: idx = int(sid.split('_')[0]) 
        except: idx = 0
        server_ip = server_ips[idx % len(server_ips)]

        t = threading.Thread(target=handle_stream_logic, args=(stream, sid, server_ip, state))
        threads.append(t)
        t.start()
        time.sleep(0.005) 

    for t in threads: t.join()
    stop_playback.set()
    playback_thread.join(timeout=1.0)

    # --- Results Calculation (FIXED) ---
    
    # 1. Calculate Total Experiment Duration (renamed variable)
    total_duration = max(1e-6, time.time() - state["start_time"])
    avg_tp = (state["total_bytes"] * 8) / (total_duration * 1e6)
    
    jitter = 0.0
    if len(state["throughputs"]) > 1:
        mean = sum(state["throughputs"]) / len(state["throughputs"])
        var = sum((x - mean)**2 for x in state["throughputs"]) / len(state["throughputs"])
        jitter = var ** 0.5

    # Avg bitrate calculation from timeseries
    avg_qual = 0.0
    if state["bitrate_timeseries"]:
        avg_qual = sum(b for _, b in state["bitrate_timeseries"]) / len(state["bitrate_timeseries"])

    # 2. Per-Stream Rates with Ghost Filtering
    per_stream_rates = {}
    for sid, b_count in state["per_stream_bytes"].items():
        start_t = state["per_stream_first_ts"].get(sid, 0)
        last_t = state["per_stream_last_ts"].get(sid, 0)
        stream_dur = last_t - start_t  # Renamed local variable
        
        # FIX: Filter out streams < 100ms or < 1KB (Ghost streams)
        if stream_dur > 0.1 and b_count > 1000:
             per_stream_rates[sid] = (b_count * 8) / (stream_dur * 1e6)

    fairness = 1.0
    if len(per_stream_rates) >= 2:
        rates = list(per_stream_rates.values())
        num = sum(rates) ** 2
        den = len(rates) * sum(r*r for r in rates)
        fairness = num / den if den > 0 else 0

    norm_tp_series = [(t - state["start_time"], v) for (t, v) in sorted(state["throughput_timeseries"])]
    norm_buf_series = [(t, v) for (t, v) in sorted(state["buffer_timeseries"])]
    norm_bit_series = [(t, v) for (t, v) in sorted(state["bitrate_timeseries"])]

    results = {
        "avg_throughput_mbps": avg_tp,
        "avg_bitrate_selected_mbps": avg_qual,
        "jitter_mbps": jitter,
        "rebuffering_ratio": state["stall_time"] / total_duration, # Uses correct total_duration
        "total_stalls": state["stall_time"],
        "fairness_index": fairness,
        "per_stream_avg_throughput_mbps": per_stream_rates,
        "throughput_timeseries": norm_tp_series,
        "buffer_timeseries": norm_buf_series,
        "bitrate_timeseries": norm_bit_series
    }
    print("JSON_RESULT:" + json.dumps(results))

def playback_simulation(state, stop_event):
    last_real_time = time.time()
    
    while not stop_event.is_set():
        time.sleep(0.1)
        now = time.time()
        delta = now - last_real_time
        last_real_time = now
        sim_time = now - state["start_time"]
        
        with state["lock"]:
            # 1. Timeline Check
            current_idx = state["active_video_index"]
            if current_idx + 1 < len(state["stream_order"]):
                next_sid = state["stream_order"][current_idx + 1]
                start_time_ground_truth = state["swipe_map"].get(next_sid, 99999.0)
                
                if sim_time >= start_time_ground_truth:
                    current_idx += 1
                    state["active_video_index"] = current_idx
                    state["active_base_id"] = next_sid

            # 2. Drain Buffer
            active_id = state["active_base_id"]
            if state["buffers"][active_id] > 0:
                state["buffers"][active_id] = max(0.0, state["buffers"][active_id] - delta)
            else:
                state["stall_time"] += delta

            # 3. Record Buffer Health
            state["buffer_timeseries"].append((sim_time, state["buffers"][active_id]))

def get_abr_decision(state, is_active, base_id):
    est = state["app_bandwidth_est"]
    buf = state["buffers"][base_id]
    
    if is_active:
        target = est * 0.9
        if buf < 5.0: target = est * 0.5 
    else:
        active_id = state["active_base_id"]
        if state["buffers"][active_id] < PANIC_THRESHOLD: 
            return BITRATE_LADDER[0][0] 
        target = est * 0.5

    selected = BITRATE_LADDER[0][0]
    for b, _ in BITRATE_LADDER:
        if b <= target: selected = b
        else: break
    return selected

def handle_stream_logic(stream, full_sid, server_ip, state):
    base_id = full_sid.split('_')[0]
    try: my_order_idx = state["stream_order"].index(base_id)
    except: my_order_idx = -1

    chunks = [ev for ev in stream.get("events", []) if ev["action"] == "download"]
    
    for chunk in chunks:
        # --- 1. Gating / Waiting Loop ---
        while True:
            with state["lock"]:
                active_idx = state["active_video_index"]
                curr_buf = state["buffers"][base_id]
                is_active = (my_order_idx == active_idx)
            
            if my_order_idx < active_idx: return 
            if is_active:
                if curr_buf < MAX_BUFFER_ACTIVE: break
            elif my_order_idx == active_idx + 1:
                if chunk.get("is_background", False):
                    if curr_buf < PREFETCH_TARGET: break
                else: pass 
            
            time.sleep(0.1)

        # --- 2. Download ---
        with state["lock"]:
            is_active = (my_order_idx == state["active_video_index"])
            bitrate = get_abr_decision(state, is_active, base_id)
        
        duration = chunk["video_duration_sec"]
        size_bytes = int((duration * bitrate * 1e6) / 8)

        t0 = time.time()
        recvd = 0
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((server_ip, PORT))
            s.sendall(f"{size_bytes}\n".encode())
            
            while recvd < size_bytes:
                with state["lock"]:
                    if my_order_idx < state["active_video_index"]:
                        s.close()
                        return

                d = s.recv(CHUNK_SIZE)
                if not d: break
                recvd += len(d)
        except: pass
        finally: 
            try: s.close()
            except: pass

        # --- 3. Update State ---
        dt = max(1e-6, time.time() - t0)
        tp_mbps = (recvd * 8) / (dt * 1e6)
        
        with state["lock"]:
            if recvd > 0:
                sim_time = time.time() - state["start_time"]
                state["total_bytes"] += recvd
                state["throughputs"].append(tp_mbps)
                state["throughput_timeseries"].append((time.time(), tp_mbps))
                
                # Record Bitrate Decision Time
                state["bitrate_timeseries"].append((sim_time, bitrate))
                
                frac = recvd / max(1, size_bytes)
                state["buffers"][base_id] += (duration * frac)
                
                state["per_stream_bytes"][full_sid] += recvd
                if full_sid not in state["per_stream_first_ts"]: state["per_stream_first_ts"][full_sid] = time.time()
                state["per_stream_last_ts"][full_sid] = time.time()

                alpha = 0.25
                state["app_bandwidth_est"] = (1 - alpha) * state["app_bandwidth_est"] + (alpha * tp_mbps)

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