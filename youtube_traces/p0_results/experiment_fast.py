import json
import time
import argparse
from typing import List, Tuple
import csv
import os

from mininet.net import Mininet
from mininet.node import OVSBridge
from mininet.link import TCLink
from mininet.log import setLogLevel, info

# --- TOPOLOGY CONFIG ---

# Client-side bottleneck
BW_LIMIT_MBPS = 10          # 30 Mbps (Healthy 4G/WiFi)
CLIENT_DELAY = "10ms"       # 10ms one-way (Crisp fiber/5G latency)

# Base RTT pattern for servers in short-form (cycled as needed)
BASE_SERVER_DELAYS = ["5ms", "40ms", "80ms"]

LONGFORM_SERVER_DELAY = "5ms"

def append_to_csv(csv_path, scenario, algo, metrics):
    file_exists = os.path.isfile(csv_path)

    with open(csv_path, "a", newline="") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow([
                "scenario",
                "algorithm",
                "avg_throughput_mbps",
                "jitter_mbps",
                "rebuffering_ratio",
                "total_stalls",
                "fairness_index",
                "per_stream_avg_throughput_mbps"
            ])

        writer.writerow([
            scenario,
            algo,
            metrics.get("avg_throughput_mbps"),
            metrics.get("jitter_mbps"),
            metrics.get("rebuffering_ratio"),
            metrics.get("total_stalls"),
            metrics.get("fairness_index"),
            json.dumps(metrics.get("per_stream_avg_throughput_mbps", {}))
        ])


def save_throughput_timeseries(trace_path: str, algo: str, timeseries: list):
    """
    Save the throughput timeseries data to a CSV file.
    """
    if not timeseries:
        return

    # Create filename: throughput_<trace_name>_<algo>.csv
    trace_name = os.path.splitext(os.path.basename(trace_path))[0]
    filename = f"throughput_{trace_name}_{algo}_4.csv"
    
    info(f"*** Saving throughput time series to {filename}\n")
    
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time_sec", "throughput_mbps"])
        writer.writerows(timeseries)


def get_num_streams_from_trace(trace_file: str) -> int:
    try:
        with open(trace_file, "r") as f:
            data = json.load(f)
    except Exception as e:
        info(f"*** Failed to load trace {trace_file}: {e}\n")
        return 1

    max_sid = 0
    for stream in data:
        sid = stream.get("stream_id", 0)
        try:
            sid_int = int(sid)
        except Exception:
            continue
        if sid_int > max_sid:
            max_sid = sid_int

    return max_sid + 1 if max_sid >= 0 else 1


def build_topology(mode: str, trace_file: str) -> Tuple[Mininet, object, List[object], List[str]]:
    net = Mininet(controller=None, link=TCLink, switch=OVSBridge)
    s1 = net.addSwitch("s1")

    client = net.addHost("h1", ip="10.0.0.1")
    net.addLink(
        client,
        s1,
        bw=BW_LIMIT_MBPS,
        delay=CLIENT_DELAY,
        max_queue_size=100,
    )

    servers: List[object] = []
    server_ips: List[str] = []

    if mode == "long":
        num_servers = 1
    else:
        num_servers = get_num_streams_from_trace(trace_file)
        if num_servers < 1:
            num_servers = 1

    info(f"*** Building topology for mode={mode}, num_servers={num_servers}\n")

    for i in range(num_servers):
        ip = f"10.0.0.{i + 2}"
        srv = net.addHost(f"srv{i}", ip=ip)

        if mode == "long":
            delay = LONGFORM_SERVER_DELAY
        else:
            delay = BASE_SERVER_DELAYS[i % len(BASE_SERVER_DELAYS)]

        net.addLink(srv, s1, bw=1000, delay=delay)
        servers.append(srv)
        server_ips.append(ip)

    net.start()
    return net, client, servers, server_ips


def run_experiment(algo: str, trace_file: str, mode: str) -> dict:
    info(f"\n*** Running Experiment: algo={algo}, trace={trace_file}, mode={mode} ***\n")

    net, client, servers, server_ips = build_topology(mode, trace_file)

    for node in [client] + servers:
        node.cmd(f"sysctl -w net.ipv4.tcp_congestion_control={algo}")

    for srv in servers:
        srv.cmd("python3 replayer.py --role server &")
    time.sleep(2)

    ips_str = ",".join(server_ips)
    cmd = (
        f"python3 replayer.py "
        f"--role client "
        f"--schedule {trace_file} "
        f"--ips {ips_str} "
        f"--mode {mode} "
        f"--bitrate 2.5"
    )

    result_raw = client.cmd(cmd)

    metrics = {}
    for line in result_raw.splitlines():
        if line.startswith("JSON_RESULT:"):
            try:
                metrics = json.loads(line.replace("JSON_RESULT:", "", 1))
            except json.JSONDecodeError:
                metrics = {}
            break

    net.stop()
    return metrics


def main() -> None:
    setLogLevel("info")

    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", required=True, help="Path to schedule JSON file")
    parser.add_argument(
        "--type",
        required=True,
        choices=["long", "short"],
        help="Traffic type: long-form or short-form",
    )
    args = parser.parse_args()

    results = {}
    for algo in ["cubic", "bbr"]:
        results[algo] = run_experiment(algo, args.trace, args.type)
        
        # Save summary
        append_to_csv(
            csv_path="results_4.csv",
            scenario=f"{args.trace}:{args.type}",
            algo=algo,
            metrics=results[algo]
        )

        # Save throughput time series for plotting
        save_throughput_timeseries(
            trace_path=args.trace,
            algo=algo,
            timeseries=results[algo].get("throughput_timeseries", [])
        )


    print("\n" + "=" * 70)
    print(f"RESULTS: {args.trace} ({args.type.upper()})")
    print("=" * 70)
    header = f"{'Metric':<35} | {'CUBIC':>12} | {'BBR':>12}"
    print(header)
    print("-" * len(header))

    metric_keys = [
        "avg_throughput_mbps",
        "jitter_mbps",
        "rebuffering_ratio",
        "total_stalls",
        "fairness_index",
    ]

    if results.get("cubic") and results.get("bbr"):
        for k in metric_keys:
            vc = results["cubic"].get(k)
            vb = results["bbr"].get(k)
            if vc is None and vb is None:
                continue
            vc_str = f"{vc:.4f}" if isinstance(vc, (int, float)) else str(vc)
            vb_str = f"{vb:.4f}" if isinstance(vb, (int, float)) else str(vb)
            print(f"{k:<35} | {vc_str:>12} | {vb_str:>12}")
    else:
        print("Experiment failed to generate results for one or both algorithms.")

    print()

    sample_algo = "cubic"
    sample = results.get(sample_algo, {})
    per_stream = sample.get("per_stream_avg_throughput_mbps", {})
    if per_stream:
        print(f"Per-stream avg throughput (Mbps) for {sample_algo.upper()}:")
        for sid, rate in per_stream.items():
            print(f"  Stream {sid}: {rate:.4f} Mbps")


if __name__ == "__main__":
    main()