import json
import time
import argparse
from typing import List, Tuple
import csv
import os

from mininet.net import Mininet
from mininet.node import OVSBridge, OVSController
from mininet.link import TCLink
from mininet.log import setLogLevel, info

BOTTLENECK_CONFIG = {
    'bw': 20,
    'delay': '15ms',
    'loss': 0.5,
    'max_queue_size': 100,
    'use_htb': True
}
    
BASE_SERVER_DELAYS = ["5ms", "20ms", "40ms"]
LONGFORM_SERVER_DELAY = "5ms"

def append_to_csv(csv_path, scenario, algo, metrics):
    file_exists = os.path.isfile(csv_path)
    with open(csv_path, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["scenario", "algorithm", "avg_throughput_mbps", "avg_bitrate_selected_mbps", "jitter_mbps", "rebuffering_ratio", "total_stalls", "fairness_index", "per_stream_avg_throughput_mbps"])
        writer.writerow([
            scenario, algo,
            metrics.get("avg_throughput_mbps"),
            metrics.get("avg_bitrate_selected_mbps"),
            metrics.get("jitter_mbps"),
            metrics.get("rebuffering_ratio"),
            metrics.get("total_stalls"),
            metrics.get("fairness_index"),
            json.dumps(metrics.get("per_stream_avg_throughput_mbps", {}))
        ])

def save_full_json(trace_path, algo, metrics):
    """Saves the full JSON result including time-series for plotting."""
    trace_name = os.path.splitext(os.path.basename(trace_path))[0]
    filename = f"results_{trace_name}_{algo}.json"
    info(f"*** Saving full JSON results to {filename}\n")
    with open(filename, "w") as f:
        json.dump(metrics, f, indent=2)

def get_num_streams_from_trace(trace_file):
    try:
        with open(trace_file, "r") as f: data = json.load(f)
    except: return 1
    unique_ids = set()
    for stream in data:
        sid_str = str(stream.get("stream_id", "0"))
        unique_ids.add(sid_str.split('_')[0])
    return len(unique_ids)

def build_topology(mode, trace_file):
    # Initialize Mininet with TCLink to support bandwidth/delay/loss options
    net = Mininet(controller=OVSController, link=TCLink, switch=OVSBridge)
    
    s1 = net.addSwitch("s1")
    client = net.addHost("h1", ip="10.0.0.1")
    
    # --- APPLYING BOTTLENECK CONFIGURATION ---
    info(f"*** Setting Bottleneck: {BOTTLENECK_CONFIG} ***\n")
    net.addLink(client, s1, cls=TCLink, **BOTTLENECK_CONFIG)

    servers, server_ips = [], []
    num_servers = 1 if mode == "long" else max(1, get_num_streams_from_trace(trace_file))
    
    info(f"*** Building topology for mode={mode}, num_servers={num_servers}\n")
    for i in range(num_servers):
        ip = f"10.0.0.{i + 2}"
        srv = net.addHost(f"srv{i}", ip=ip)
        # Server links remain fast to ensure the bottleneck is at the client
        delay = LONGFORM_SERVER_DELAY if mode == "long" else BASE_SERVER_DELAYS[i % len(BASE_SERVER_DELAYS)]
        net.addLink(srv, s1, bw=1000, delay=delay, loss=0)
        servers.append(srv)
        server_ips.append(ip)

    net.start()
    return net, client, servers, server_ips

def run_experiment(algo, trace_file, mode):
    info(f"\n*** Running Experiment: algo={algo}, trace={trace_file}, mode={mode} ***\n")
    
    # 1. Build Network
    net, client, servers, server_ips = build_topology(mode, trace_file)

    # 2. Set Congestion Control Algorithm
    # We apply this to ALL nodes to be safe, though mainly client/server matters
    for node in [client] + servers:
        # Check if module is loaded (optional safety)
        if algo == "bbr":
            node.cmd("modprobe tcp_bbr")
        node.cmd(f"sysctl -w net.ipv4.tcp_congestion_control={algo}")

    # 3. Start Servers
    for srv in servers:
        srv.cmd("python3 replayer.py --role server &")
    time.sleep(2) # Allow servers to bind ports

    # 4. Start Client Replayer
    ips_str = ",".join(server_ips)
    cmd = (
        f"python3 replayer.py "
        f"--role client "
        f"--schedule {trace_file} "
        f"--ips {ips_str} "
        f"--mode {mode}"
    )

    result_raw = client.cmd(cmd)
    
    # 5. Parse Results
    metrics = {}
    for line in result_raw.splitlines():
        if line.startswith("JSON_RESULT:"):
            try: metrics = json.loads(line.replace("JSON_RESULT:", "", 1))
            except: pass
            break

    # 6. Cleanup
    net.stop()
    
    # Force cleanup of any lingering mininet processes
    os.system("mn -c > /dev/null 2>&1")
    
    return metrics

def main():
    setLogLevel("info")
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", required=True)
    parser.add_argument("--type", required=True, choices=["long", "short"])
    args = parser.parse_args()

    results = {}
    # Run for both algorithms
    for algo in ["cubic", "bbr"]:
        results[algo] = run_experiment(algo, args.trace, args.type)
        append_to_csv("results.csv", f"{args.trace}:{args.type}", algo, results[algo])
        save_full_json(args.trace, algo, results[algo])

    print(f"\nRESULTS: {args.trace} ({args.type.upper()})")
    header = f"{'Metric':<35} | {'CUBIC':>12} | {'BBR':>12}"
    print(header + "\n" + "-" * len(header))
    
    keys = ["avg_throughput_mbps", "avg_bitrate_selected_mbps", "jitter_mbps", "rebuffering_ratio", "total_stalls", "fairness_index"]
    
    if results.get("cubic") and results.get("bbr"):
        for k in keys:
            vc = results["cubic"].get(k, 0)
            vb = results["bbr"].get(k, 0)
            # Handle potential None types safely
            vc = vc if vc is not None else 0
            vb = vb if vb is not None else 0
            print(f"{k:<35} | {vc:>12.4f} | {vb:>12.4f}")
    else:
        print("Error: One or both experiments failed to produce results.")

if __name__ == "__main__":
    main()