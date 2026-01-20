import docker
import time
import json
import os
import statistics
import csv
import math
import threading
import re

# --- Configuration ---
IMAGE_NAME = "tcp-sim-node"
NETWORK_NAME = "sim-net"
SERVER_NAME = "tcp-server"
RESULTS_DIR = "1_single_flow_results"

# --- ABR Configuration ---
VIDEO_CHUNKS = 20  
QUALITY_SCORES = {'low': 1, 'medium': 2, 'high': 3}
BITRATE_LEVELS = {
    'low': 102400,    # 100 KB
    'medium': 204800, # 200 KB
    'high': 409600    # 400 KB
}
INITIAL_BUFFER_SECONDS = 5.0
TARGET_BUFFER_SECONDS = 15.0 

results_sensitivity = []

def ensure_infrastructure():
    client = docker.from_env()
    try:
        client.networks.get(NETWORK_NAME)
    except docker.errors.NotFound:
        print(f"Creating network {NETWORK_NAME}...")
        client.networks.create(NETWORK_NAME, driver="bridge")

    try:
        client.containers.get(SERVER_NAME).remove(force=True)
    except: pass

    print("Starting Server...")
    # 1. Start the container with 'sleep infinity' to keep it alive
    server = client.containers.run(
        IMAGE_NAME, name=SERVER_NAME, network=NETWORK_NAME, detach=True,
        cap_add=["NET_ADMIN"], privileged=True, 
        command="sleep infinity"
    )
    
    # 2. Start Nginx with detach=True so Python doesn't wait/hang
    server.exec_run("nginx", detach=True)
    
    disable_offloading(server)
    
    # Enable BBR Pacing
    print("  [Setup] Enabling TCP internal pacing...")
    server.exec_run("sysctl -w net.ipv4.tcp_internal_pacing=1")
    server.exec_run("sysctl -w net.ipv4.tcp_pacing_ss_ratio=200")
    server.exec_run("sysctl -w net.ipv4.tcp_pacing_ca_ratio=120")
    
    # Generate file in correct path
    print("  [Setup] Generating 1GB test file...")
    server.exec_run("mkdir -p /var/www/html")
    
    cmd = "dd if=/dev/zero of=/var/www/html/testfile.bin bs=1M count=1024"
    exit_code, out = server.exec_run(cmd)
    
    # VERIFICATION
    check_cmd = "ls -lh /var/www/html/testfile.bin"
    ec, output = server.exec_run(check_cmd)
    
    if ec != 0:
        print(f"  [CRITICAL ERROR] File generation failed! Output: {out.decode()}")
    else:
        print(f"  [Success] File created: {output.decode().strip()}")

    return client, server

def disable_offloading(container):
    """Disables TSO, GSO, and GRO to ensure accurate TCP packet behavior."""
    cmd = "ethtool -K eth0 tso off gso off gro off"
    container.exec_run(cmd)

def verify_cc(container, expected_alg):
    res = container.exec_run("sysctl net.ipv4.tcp_congestion_control")
    actual = res.output.decode().strip().split('=')[-1].strip()
    if actual != expected_alg:
        print(f"  [CRITICAL] Algorithm Mismatch! Wanted: {expected_alg}, Got: {actual}")

def get_bdp_buffer(bw_mbps, rtt_ms, multiplier=2.0):
    """Calculates buffer size based on Bandwidth-Delay Product."""
    # BDP in packets = (BW * RTT) / (1500 * 8)
    bdp_pkts = (bw_mbps * 1e6 * (rtt_ms / 1000.0)) / 12000.0
    return max(10, int(bdp_pkts * multiplier))

def apply_net_conditions(container, bw, rtt, loss, buffer_pkts, loss_corr=0):
    container.exec_run("tc qdisc del dev eth0 root")
    
    # Increase burst size for BBR
    burst_size = "500k" if bw >= 500 else "100k"
    
    # Root: HTB
    container.exec_run("tc qdisc add dev eth0 root handle 1: htb default 10")
    container.exec_run(f"tc class add dev eth0 parent 1: classid 1:10 htb rate {bw}mbit burst {burst_size}")
    
    # Leaf: Netem
    loss_cmd = ""
    if loss > 0:
        loss_cmd = f"loss {loss}% {loss_corr}%"
    
    cmd = f"tc qdisc add dev eth0 parent 1:10 handle 10: netem delay {rtt}ms {loss_cmd} limit {buffer_pkts}"
    container.exec_run(cmd)

def get_client(client_obj, name, alg):
    try:
        client_obj.containers.get(name).remove(force=True)
    except: pass
    
    c = client_obj.containers.run(
        IMAGE_NAME, name=name, network=NETWORK_NAME, detach=True,
        cap_add=["NET_ADMIN"], privileged=True, command="sleep infinity"
    )
    
    disable_offloading(c)
    c.exec_run("sysctl -w net.ipv4.tcp_internal_pacing=1")
    c.exec_run(f"sysctl -w net.ipv4.tcp_congestion_control={alg}")
    verify_cc(c, alg)
    return c

# --- Workloads ---

def workload_web_page(client):
    cmd = """
    bash -c 'for i in {1..5}; do
        curl -s --max-time 10 -w "%{time_starttransfer},%{time_total}\\n" -o /dev/null -r 0-51199 http://tcp-server/testfile.bin?q=$i
    done'
    """
    res = client.exec_run(cmd)
    ttfb_list, total_list = [], []
    try:
        output_lines = res.output.decode().split()
        for line in output_lines:
            if ',' in line:
                t_ttfb, t_total = line.split(',')
                ttfb_list.append(float(t_ttfb))
                total_list.append(float(t_total))
        return (statistics.mean(ttfb_list) if ttfb_list else 0.0), sum(total_list)
    except: return 0.0, 0.0

def workload_video_stream_persistent(client):
    inner_script = f"""
import http.client
import time
import statistics
import sys

# Config matches outer script
CHUNKS = {VIDEO_CHUNKS}
BITRATE_LEVELS = {{'low': 102400, 'medium': 204800, 'high': 409600}}
TARGET_BUFFER = 15.0

try:
    conn = http.client.HTTPConnection("tcp-server")
    current_quality = 'medium'
    buffer_sec = 5.0
    total_rebuf = 0.0
    total_play = 0.0
    throughputs = []

    for i in range(1, CHUNKS + 1):
        size = BITRATE_LEVELS[current_quality]
        t0 = time.time()
        headers = {{"Range": f"bytes=0-{{size}}"}}
        conn.request("GET", f"/testfile.bin?q={{i}}", headers=headers)
        resp = conn.getresponse()
        resp.read()
        t1 = time.time()
        
        dl_time = t1 - t0
        if dl_time <= 0: dl_time = 0.0001
        
        thr_bps = (size * 8) / dl_time
        throughputs.append(thr_bps)
        thr_mbps = thr_bps / 1e6
        
        buffer_sec += (2.0 - dl_time)
        total_play += dl_time
        
        if buffer_sec < 0:
            total_rebuf += abs(buffer_sec)
            total_play += abs(buffer_sec)
            buffer_sec = 0
            
        if thr_mbps > 2.5: current_quality = 'high'
        elif thr_mbps < 1.0: current_quality = 'low'
        elif buffer_sec > TARGET_BUFFER and current_quality != 'high': current_quality = 'high'

    conn.close()

    avg_thr = (sum(throughputs) / len(throughputs)) / 1e6 if throughputs else 0
    jitter = (statistics.stdev(throughputs) / 1e6) if len(throughputs) > 1 else 0
    rebuf_ratio = total_rebuf / total_play if total_play > 0 else 0

    print(f"{{avg_thr}},{{rebuf_ratio}},{{jitter}}")

except Exception as e:
    print("0.0,0.0,0.0")
"""
    temp_file = "abr_sim_temp.py"
    with open(temp_file, "w") as f:
        f.write(inner_script)
    
    try:
        os.system(f"docker cp {temp_file} {client.name}:/abr_sim.py")
        res = client.exec_run("python3 /abr_sim.py")
        output = res.output.decode().strip().split('\n')
        last_line = output[-1]
        t, r, j = map(float, last_line.split(','))
        if os.path.exists(temp_file): os.remove(temp_file)
        return t, j, r
    except Exception as e:
        print(f"  [Error] ABR Workload Failed: {e}")
        return 0.0, 0.0, 0.0

def workload_large_file(client, target_bw_mbps, duration_sec=15):
    ideal_size_bytes = (target_bw_mbps * 1e6 * duration_sec) / 8
    
    max_safe_size = 300 * 1024 * 1024
    target_size = int(max(5 * 1024 * 1024, min(ideal_size_bytes, max_safe_size)))
    
    timeout = min(300, int(duration_sec * 2.5))
    
    cmd = f"curl -s --fail --max-time {timeout} -w '%{{time_total}},%{{size_download}}' -o /dev/null -r 0-{target_size} http://tcp-server/testfile.bin"
    
    try:
        res = client.exec_run(cmd)
        output = res.output.decode().strip()
        
        if not output: 
            if res.exit_code != 0:
                print(f"    [DEBUG] Curl Failed (Exit {res.exit_code}).")
            return 0.0, float(timeout)

        t_str, s_str = output.split(',')
        fct, size = float(t_str), float(s_str)
        thr = (size * 8) / (fct * 1e6) if fct > 0 else 0
        return thr, fct
    except: return 0.0, 0.0

# --- Test Execution ---

def run_sensitivity_analysis(docker_client, server):
    print("\n=== I. Single-Flow Sensitivity Analysis ===")
    algorithms = ["cubic", "bbr"]
    
    BDP_MULTIPLIERS = [1.0, 2.0, 5.0] 
    
    def run_scenario(matrix_name, alg, bw, rtt, loss, loss_corr, buffer, mult_tag, duration=15):
        apply_net_conditions(server, bw, rtt, loss, int(buffer), loss_corr)
        server.exec_run(f"sysctl -w net.ipv4.tcp_congestion_control={alg}")
        verify_cc(server, alg)

        c = get_client(docker_client, "client_s1", alg)
        
        # --- NEW: RTT Monitoring Setup ---
        rtt_stats = {"avg": 0.0, "max": 0.0}
        
        def monitor_rtt():
            # Ping for slightly longer than duration. 
            # -i 0.2 = 5 pings/sec. 
            count = int(duration * 5) + 10 
            # We use hostname 'tcp-server' which is resolved by Docker DNS
            cmd = f"ping -i 0.2 -c {count} tcp-server" 
            try:
                res = c.exec_run(cmd)
                output = res.output.decode()
                # Parse standard ping output: "rtt min/avg/max/mdev = 0.048/0.048/0.048/0.000 ms"
                match = re.search(r"rtt min/avg/max/mdev = [\d\.]+/([\d\.]+)/([\d\.]+)/", output)
                if match:
                    rtt_stats["avg"] = float(match.group(1))
                    rtt_stats["max"] = float(match.group(2))
            except Exception as e:
                print(f"RTT Monitor failed: {e}")

        # Start monitoring in background
        rtt_thread = threading.Thread(target=monitor_rtt)
        rtt_thread.start()
        # ---------------------------------

        # Only run full suite if standard duration
        if duration == 15:
            vid_thr, vid_jit, vid_rebuf = workload_video_stream_persistent(c)
            web_ttfb, web_plt = workload_web_page(c)
        else:
            vid_thr, vid_jit, vid_rebuf = 0.0, 0.0, 0.0
            web_ttfb, web_plt = 0.0, 0.0

        large_thr, large_fct = workload_large_file(c, bw, duration_sec=duration)
        
        # Wait for RTT monitor to finish (or it finishes when ping count completes)
        # Note: If large_file finishes fast, this might hang for a few seconds 
        # until ping finishes. This is acceptable for test accuracy.
        rtt_thread.join()
        
        print(f"    [{alg.upper()}|{mult_tag}] VidThr:{vid_thr:.1f} | LargeFCT:{large_fct:.2f}s | RTT_Avg:{rtt_stats['avg']:.1f}ms")
        
        results_sensitivity.append({
            "matrix": matrix_name, "alg": alg, "bw_mbps": bw, "rtt_ms": rtt, "loss_pct": loss, "loss_corr": loss_corr, 
            "buffer_pkts": buffer, "bdp_multiplier": mult_tag,
            "video_throughput_mbps": vid_thr, "video_jitter_mbps": vid_jit, "video_rebuf_ratio": vid_rebuf,
            "web_avg_ttfb_s": web_ttfb, "web_plt_s": web_plt, "large_throughput_mbps": large_thr, "large_fct_s": large_fct,
            "loaded_rtt_avg_ms": rtt_stats["avg"],
            "loaded_rtt_max_ms": rtt_stats["max"]
        })
        c.remove(force=True)

    # # --- Matrix B: Loss Resilience (Extended Duration) ---
    # print("\n--- Matrix B: Loss Resilience (BW=100, RTT=50) ---")
    # loss_configs = [("None", 0, 0), ("0.1%", 0.1, 0), ("0.5%", 0.5, 0), ("1.0%", 1.0, 0), ("2.0%", 2.0, 0), ("Bursty_2%", 2.0, 25)]
    # for alg in algorithms:
    #     for mult in BDP_MULTIPLIERS:
    #         base_buf = get_bdp_buffer(100, 50, mult)
    #         for (name, loss, corr) in loss_configs:
    #             print(f"  Testing {alg.upper()} @ {name} Loss [Buffer={mult}xBDP]...")
    #             run_scenario(f"Loss_{name}", alg, 100, 50, loss, corr, base_buf, mult, duration=60)

    # --- Matrix C: Bandwidth Scaling ---
    print("\n--- Matrix C: Bandwidth Scaling (RTT=50, Loss=0) ---")
    
    # We fix the RTT and Loss to isolate Bandwidth as the variable
    rtt = 50
    loss = 0
    
    # We use a 2.0x BDP multiplier. 
    # This is the standard "Safe" buffer size that allows TCP to utilize the full link.
    # If we used a fixed size (like 100), it would work for 10Mbps but fail at 1000Mbps.
    # scaling_multiplier = 2.0 

    for alg in algorithms:
         for scaling_multiplier in BDP_MULTIPLIERS:

            for bw in [10, 100, 1000]:
                # 1. Calculate BDP dynamically for this specific bandwidth
                # Formula: (Bits/sec * Seconds) / Bits_per_packet
                # We assume 1500 byte packets -> 12,000 bits
                bdp_pkts = (bw * 1e6 * (rtt / 1000.0)) / 12000.0
                
                # 2. Set the buffer to 2x BDP
                target_buf = max(10, int(bdp_pkts * scaling_multiplier))
                
                print(f"  Testing {alg.upper()} @ {bw} Mbps [Buf={target_buf} pkts ({scaling_multiplier}x BDP)]...")
                
                # 3. FIX: Use a unique scenario name so files don't overwrite each other
                scenario_name = f"Bandwidth_Scaling_{bw}Mbps"
                
                # Run the scenario
                run_scenario(scenario_name, alg, bw, rtt, loss, 0, target_buf, scaling_multiplier)


    # --- Matrix D: Buffer Size ---
    print("\n--- Matrix D: Buffer Size (BW=100, RTT=50) ---")
    
    # UNCOMMENTED definitions to fix NameError
    bw, rtt = 100, 50
    # 12000 bits = 1500 bytes * 8 bits
    bdp_pkts = (bw * 1e6 * (rtt/1000.0)) / 12000.0
    matrix_d_mults = [0.1, 0.5, 1.0, 2.0, 4.0, 8.0] 
    
    for alg in algorithms:
        for mult in matrix_d_mults:
            buf = max(10, int(bdp_pkts * mult))
            print(f"  Testing {alg.upper()} @ {mult}x BDP ({buf} pkts)...")
            run_scenario(f"BufferSize_{mult}xBDP", alg, bw, rtt, 0, 0, buf, mult)

def main():
    if not os.path.exists(RESULTS_DIR): os.makedirs(RESULTS_DIR)
    client, server = ensure_infrastructure()
    try:
        run_sensitivity_analysis(client, server)
    finally:
        print("\nSaving Results...")
        with open(f"{RESULTS_DIR}/final_sensitivity_results.json", "w") as f:
            json.dump(results_sensitivity, f, indent=2)
        if results_sensitivity:
            with open(f"{RESULTS_DIR}/final_sensitivity_results.csv", "w", newline='') as f:
                writer = csv.DictWriter(f, fieldnames=results_sensitivity[0].keys())
                writer.writeheader()
                writer.writerows(results_sensitivity)
        server.stop()
        server.remove()
        print(f"Done.")

if __name__ == "__main__":
    main()