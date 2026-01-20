#!/usr/bin/env python3
"""
multiflow_sim.py

Multi-flow fairness test harness.

Requirements:
  - docker python SDK (pip install docker)
  - the same Docker image ("tcp-sim-node") built from your Dockerfile and available locally
  - run as a user that can control Docker (or root)

Outputs:
  - results_multiflow.json
  - results_multiflow.csv
"""

import docker
import threading
import time
import json
import csv
import math
import os
from statistics import mean, stdev

IMAGE_NAME = "tcp-sim-node"
NETWORK_NAME = "sim-net-multiflow"
SERVER_NAME = "tcp-server-mf"
RESULTS_DIR = "multiflow_results"
RESULTS_JSON = f"{RESULTS_DIR}/results_multiflow.json"
RESULTS_CSV = f"{RESULTS_DIR}/results_multiflow.csv"

# Workload parameters (tweakable)
VIDEO_CHUNKS = 30
BITRATE_LEVELS = {'low': 102400, 'medium': 204800, 'high': 409600}
QUALITY_SCORES = {'low':1, 'medium':2, 'high':3}
INITIAL_BUFFER_SECONDS = 5.0
TARGET_BUFFER_SECONDS = 15.0
LOW_THRESHOLD_MBPS = 1.0
HIGH_THRESHOLD_MBPS = 2.5
# LARGE_FILE_BYTES = 50 * 1024 * 1024  # 50 MB
LARGE_FILE_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB
BUFFER_PACKETS = 2000
BANDWIDTH = 100
RTTS = [10, 50, 200]

WEB_SMALL_FILES = 10
WEB_SMALL_SIZE = 51200  # bytes (~50KB)

# --- Helpers: Docker infra ---

def ensure_infrastructure():
    client = docker.from_env()
    # Create network if needed
    try:
        client.networks.get(NETWORK_NAME)
    except docker.errors.NotFound:
        client.networks.create(NETWORK_NAME, driver="bridge")
    # Remove prior server if exists
    try:
        client.containers.get(SERVER_NAME).remove(force=True)
    except:
        pass
    # Start server container
    server = client.containers.run(
        IMAGE_NAME, name=SERVER_NAME, network=NETWORK_NAME, detach=True,
        cap_add=["NET_ADMIN"], privileged=True, command="nginx"
    )
    # Ensure cubic default (can change later per-experiment)
    server.exec_run("sysctl -w net.ipv4.tcp_congestion_control=cubic")
    return client, server

def cleanup_infrastructure(client, server):
    try:
        server.stop(timeout=1)
    except: pass
    try:
        server.remove(force=True)
    except: pass
    # attempt to remove network (ignore failure)
    try:
        net = client.networks.get(NETWORK_NAME)
        net.remove()
    except:
        pass

def apply_net_conditions(container, bw=None, rtt=None, loss=0, buffer_pkts=1000, loss_corr=0):
    """
    Apply tc to the given container's eth0.
      - If bw is None: do not apply htb
      - rtt in ms
    """
    # Remove existing qdisc (ignore errors)
    try:
        container.exec_run("tc qdisc del dev eth0 root")
    except:
        pass

    # If bandwidth set, create htb root and class; else just netem
    if bw:
        container.exec_run("tc qdisc add dev eth0 root handle 1: htb default 10")
        container.exec_run(f"tc class add dev eth0 parent 1: classid 1:10 htb rate {bw}mbit")
        parent = "1:10"
    else:
        container.exec_run("tc qdisc add dev eth0 root handle 1: netem")
        parent = "1:"

    loss_cmd = ""
    if loss > 0:
        loss_cmd = f"loss {loss}% {loss_corr}%"

    rtt_cmd = f"delay {rtt}ms 0ms" if rtt is not None else ""
    limit_cmd = f" limit {buffer_pkts}" if buffer_pkts is not None else ""
    # Attach netem as child if bandwidth applied
    cmd = f"tc qdisc add dev eth0 parent {parent} handle 10: netem {rtt_cmd} {loss_cmd} {limit_cmd}"
    # collapsible double spaces are harmless
    container.exec_run(cmd)

def make_client(client_obj, name, alg="cubic"):
    """Start a client container and set congestion control."""
    try:
        client_obj.containers.get(name).remove(force=True)
    except:
        pass
    c = client_obj.containers.run(
        IMAGE_NAME, name=name, network=NETWORK_NAME, detach=True,
        cap_add=["NET_ADMIN"], privileged=True, command="sleep infinity"
    )
    c.exec_run(f"sysctl -w net.ipv4.tcp_congestion_control={alg}")
    return c

def set_server_cca(server, alg):
    server.exec_run(f"sysctl -w net.ipv4.tcp_congestion_control={alg}")
# --- Workloads (run inside a client container) ---

def workload_large_file_download(container):
    """Return: (throughput_mbps, fct_seconds, raw_time, raw_size)"""
    cmd = (
        "bash -lc "
        f"\"curl -s -w '%{{time_total}},%{{size_download}}' -o /dev/null -r 0-{LARGE_FILE_BYTES} http://{SERVER_NAME}/testfile.bin\""
    )
    res = container.exec_run(cmd, demux=False)
    out = res.output.decode().strip()
    try:
        t_str, s_str = out.split(",")
        t = float(t_str)
        s = float(s_str)
        if t <= 0 or s <= 0:
            return 0.0, None, t, s
        thr_mbps = (s * 8) / t / 1_000_000.0
        return thr_mbps, t, t, s
    except Exception:
        return 0.0, None, None, None

def workload_plt_and_ttfb(container):
    """
    Returns tuple: (plt_seconds, ttfb_seconds)
    PLT simulated as sequential downloads of small resources.
    """
    cmd = "bash -lc '"
    for i in range(1, WEB_SMALL_FILES+1):
        cmd += f"curl -s -w \"%{{time_total}}\\n\" -o /dev/null -r 0-{WEB_SMALL_SIZE} http://{SERVER_NAME}/testfile.bin?q={i}; "
    cmd += "'"
    res = container.exec_run(cmd)
    try:
        times = [float(x) for x in res.output.decode().split() if x.strip()]
        total = sum(times)
    except Exception:
        total = None

    # TTFB single quick call
    cmd2 = f"bash -lc 'curl -s -w \"%{{time_starttransfer}}\" -o /dev/null http://{SERVER_NAME}/testfile.bin'"
    res2 = container.exec_run(cmd2)
    try:
        ttfb = float(res2.output.decode().strip())
    except:
        ttfb = None

    return total, ttfb

def workload_video_abr(container):
    """
    Simulated ABR; returns:
    - avg_quality_score
    - avg_throughput_mbps
    - throughput_jitter_mbps
    - rebuffer_ratio (total_rebuffer_time / (playback_time_without_rebuffers))
    - total_playback_wall_seconds (includes download + rebuffer wait)
    """
    current_quality = 'medium'
    buffer_seconds = INITIAL_BUFFER_SECONDS
    total_wall_time = 0.0
    total_playback_seconds = VIDEO_CHUNKS * 2.0  # content play time (2s per chunk)
    total_rebuffer_time = 0.0

    chunk_throughputs_bps = []
    quality_counts = {q:0 for q in QUALITY_SCORES}

    for i in range(1, VIDEO_CHUNKS+1):
        chunk_size = BITRATE_LEVELS[current_quality]
        # curl request returns time_total and size_download
        cmd = f"bash -lc \"curl -s -w '%{{time_total}},%{{size_download}}' -o /dev/null -r 0-{chunk_size} http://{SERVER_NAME}/testfile.bin?q={i}\""
        res = container.exec_run(cmd)
        out = res.output.decode().strip()
        try:
            time_str, size_str = out.split(',')
            download_time = float(time_str)
            size_bytes = float(size_str)
            if download_time <= 0 or size_bytes <= 0:
                download_time = 5.0
                size_bytes = 0.0
        except Exception:
            download_time = 5.0
            size_bytes = 0.0

        if size_bytes > 0:
            tp_bps = (size_bytes * 8) / download_time
        else:
            tp_bps = 0.0

        chunk_throughputs_bps.append(tp_bps)
        quality_counts[current_quality] += 1

        # playback simulation
        playback_duration = 2.0
        buffer_seconds += playback_duration
        buffer_seconds -= download_time
        total_wall_time += download_time

        if buffer_seconds < 0:
            rebuf = abs(buffer_seconds)
            total_rebuffer_time += rebuf
            total_wall_time += rebuf
            buffer_seconds = 0.0

        # Simple ABR decision
        throughput_mbps = tp_bps / 1_000_000.0
        next_quality = current_quality
        if throughput_mbps > HIGH_THRESHOLD_MBPS:
            if current_quality == 'medium':
                next_quality = 'high'
            elif current_quality == 'low':
                next_quality = 'medium'
        elif throughput_mbps < LOW_THRESHOLD_MBPS:
            if current_quality == 'medium':
                next_quality = 'low'
            elif current_quality == 'high':
                next_quality = 'medium'
        elif buffer_seconds >= TARGET_BUFFER_SECONDS:
            next_quality = 'high'
        current_quality = next_quality

    # aggregates
    total_chunks = sum(quality_counts.values())
    total_score = sum(QUALITY_SCORES[q] * c for q,c in quality_counts.items())
    avg_quality_score = total_score / total_chunks if total_chunks > 0 else 0.0

    if len(chunk_throughputs_bps) > 0:
        avg_bps = sum(chunk_throughputs_bps) / len(chunk_throughputs_bps)
        avg_mbps = avg_bps / 1_000_000.0
        jitter_mbps = (stdev(chunk_throughputs_bps) / 1_000_000.0) if len(chunk_throughputs_bps) > 1 else 0.0
    else:
        avg_mbps = 0.0
        jitter_mbps = 0.0

    rebuffer_ratio = total_rebuffer_time / total_playback_seconds if total_playback_seconds > 0 else 0.0

    return {
        "avg_quality_score": avg_quality_score,
        "avg_throughput_mbps": avg_mbps,
        "throughput_jitter_mbps": jitter_mbps,
        "rebuffer_ratio": rebuffer_ratio,
        "total_wall_time": total_wall_time
    }

# --- Concurrency runner ---

def run_parallel_on_clients(func, clients):
    """
    func(container) -> result
    returns list of results in same order as clients
    """
    results = [None] * len(clients)
    threads = []
    def worker(i, c):
        results[i] = func(c)
    for i, c in enumerate(clients):
        t = threading.Thread(target=worker, args=(i, c))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return results

# --- Metrics utilities ---

def jains_fairness(values):
    """Jain's fairness index"""
    if not values:
        return None
    s = sum(values)
    sq = sum(v*v for v in values)
    n = len(values)
    if sq == 0:
        return 0.0
    return (s*s) / (n * sq)

# --- Experiment Scenarios ---

def scenario_three_flows_same_rtt_same_cca(client, server, alg, rtt=50, bw=200, buffer_pkts=5000):
    """
    Scenario A: 3 flows, same RTT, same CCA
    Returns per-flow throughput (large-file), plus other per-flow workload metrics.
    """
    # Apply bottleneck shaping on the SERVER for egress
    apply_net_conditions(server, bw=bw, rtt=rtt, loss=0, buffer_pkts=buffer_pkts)
    set_server_cca(server, alg)
    clients = []
    for i in range(3):
        c = make_client(client, f"mf_a_{alg}_{i}", alg=alg)
        # no extra per-client shaping for RTT here since server shapes egress
        clients.append(c)

    # run workloads in parallel: large file (for fairness), plus also collect video and plt concurrently
    # Large-file throughput for fairness
    large_results = run_parallel_on_clients(workload_large_file_download, clients)
    # video results (stability)
    video_results = run_parallel_on_clients(workload_video_abr, clients)
    # plt/ttfb
    web_results = run_parallel_on_clients(workload_plt_and_ttfb, clients)

    # collect metrics
    per_flow = []
    for idx, c in enumerate(clients):
        thr, fct, raw_t, raw_s = large_results[idx]
        plt_time, ttfb = web_results[idx]
        video = video_results[idx]
        per_flow.append({
            "flow_id": idx,
            "cca": alg,
            "rtt_ms": rtt,
            "large_throughput_mbps": thr,
            "large_fct_seconds": fct,
            "plt_seconds": plt_time,
            "ttfb_seconds": ttfb,
            "video": video
        })
        # cleanup client
        try:
            c.remove(force=True)
        except: pass

    print(per_flow)

    # fairness
    throughputs = [p["large_throughput_mbps"] for p in per_flow]
    fairness = jains_fairness([v for v in throughputs if v is not None])
    return {"scenario": f"3flows_sameRTT_{alg}", "per_flow": per_flow, "fairness": fairness, "bw_mbps": bw, "rtt_ms": rtt}

def scenario_three_flows_same_cca_diff_rtt(client, server, alg, bw=200, buffer_pkts=5000):
    """
    Scenario B: 3 flows same CCA, but different RTTs (per-client RTT applied).
    We apply per-client netem shaping so RTTs differ.
    """
    # No global server shaping (or still shape bandwidth on server)
    apply_net_conditions(server, bw=bw, rtt=None, loss=0, buffer_pkts=buffer_pkts)
    set_server_cca(server, alg)
    rtts = RTTS
    clients = []
    for i in range(3):
        c = make_client(client, f"mf_b_{alg}_{i}", alg=alg)
        # Apply shaping to each client to enforce RTT
        apply_net_conditions(c, bw=None, rtt=rtts[i], loss=0, buffer_pkts=buffer_pkts)
        clients.append(c)

    large_results = run_parallel_on_clients(workload_large_file_download, clients)
    video_results = run_parallel_on_clients(workload_video_abr, clients)
    web_results = run_parallel_on_clients(workload_plt_and_ttfb, clients)

    per_flow = []
    for idx, c in enumerate(clients):
        thr, fct, raw_t, raw_s = large_results[idx]
        plt_time, ttfb = web_results[idx]
        video = video_results[idx]
        per_flow.append({
            "flow_id": idx,
            "cca": alg,
            "rtt_ms": rtts[idx],
            "large_throughput_mbps": thr,
            "large_fct_seconds": fct,
            "plt_seconds": plt_time,
            "ttfb_seconds": ttfb,
            "video": video
        })
        try:
            c.remove(force=True)
        except: pass

    throughputs = [p["large_throughput_mbps"] for p in per_flow]
    fairness = jains_fairness([v for v in throughputs if v is not None])
    return {"scenario": f"3flows_diffRTT_{alg}", "per_flow": per_flow, "fairness": fairness, "bw_mbps": bw, "rtts_ms": rtts}

def scenario_one_cubic_one_bbr(client, server, bw=200, rtt=50, buffer_pkts=5000):
    """
    Scenario C: head-to-head one CUBIC vs one BBR
    """
    apply_net_conditions(server, bw=bw, rtt=rtt, loss=0, buffer_pkts=buffer_pkts)
    c1 = make_client(client, "mf_c_cubic", alg="cubic")
    c2 = make_client(client, "mf_c_bbr", alg="bbr")
    clients = [c1, c2]

    large_results = run_parallel_on_clients(workload_large_file_download, clients)
    video_results = run_parallel_on_clients(workload_video_abr, clients)
    web_results = run_parallel_on_clients(workload_plt_and_ttfb, clients)

    per_flow = []
    for idx, (c, label) in enumerate(zip(clients, ["cubic","bbr"])):
        thr, fct, raw_t, raw_s = large_results[idx]
        plt_time, ttfb = web_results[idx]
        video = video_results[idx]
        per_flow.append({
            "flow_id": idx,
            "cca": label,
            "rtt_ms": rtt,
            "large_throughput_mbps": thr,
            "large_fct_seconds": fct,
            "plt_seconds": plt_time,
            "ttfb_seconds": ttfb,
            "video": video
        })
        try:
            c.remove(force=True)
        except: pass

    throughputs = [p["large_throughput_mbps"] for p in per_flow]
    fairness = jains_fairness([v for v in throughputs if v is not None])
    return {"scenario": "cubic_vs_bbr", "per_flow": per_flow, "fairness": fairness, "bw_mbps": bw, "rtt_ms": rtt}

# --- Runner & results saving ---

def save_results(all_results):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(RESULTS_JSON, "w") as f:
        json.dump(all_results, f, indent=2)
    # Flatten CSV rows for easy viewing
    rows = []
    for res in all_results:
        base = {
            "scenario": res.get("scenario"),
            "fairness": res.get("fairness"),
            "bw_mbps": res.get("bw_mbps"),
        }
        for p in res["per_flow"]:
            row = base.copy()
            row.update({
                "flow_id": p["flow_id"],
                "cca": p["cca"],
                "rtt_ms": p.get("rtt_ms"),
                "large_throughput_mbps": p.get("large_throughput_mbps"),
                "large_fct_seconds": p.get("large_fct_seconds"),
                "plt_seconds": p.get("plt_seconds"),
                "ttfb_seconds": p.get("ttfb_seconds"),
                "video_avg_quality": p["video"].get("avg_quality_score"),
                "video_avg_throughput_mbps": p["video"].get("avg_throughput_mbps"),
                "video_jitter_mbps": p["video"].get("throughput_jitter_mbps"),
                "video_rebuffer_ratio": p["video"].get("rebuffer_ratio"),
            })
            rows.append(row)
    # save CSV
    if rows:
        keys = rows[0].keys()
        with open(RESULTS_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(rows)

def analyze_and_print(all_results):
    print("\n=== SUMMARY ANALYSIS ===")
    for r in all_results:
        scenario = r["scenario"]
        fairness = r["fairness"]
        throughputs = [p["large_throughput_mbps"] for p in r["per_flow"]]
        print(f"\nScenario: {scenario}")
        print(f"  Fairness (Jain): {fairness:.3f}" if fairness is not None else "  Fairness: N/A")
        print(f"  Per-flow throughputs: {[round(x,2) if x else None for x in throughputs]}")
        # Show video QoE aggregated
        q_scores = [p["video"]["avg_quality_score"] for p in r["per_flow"]]
        rebufs = [p["video"]["rebuffer_ratio"] for p in r["per_flow"]]
        print(f"  Video QoE avg quality (per flow): {q_scores}")
        print(f"  Video rebuffer ratio (per flow): {rebufs}")

def main():
    client, server = ensure_infrastructure()
    all_results = []
    try:
        # Scenario A: 3 flows same RTT/CCA, test both cubic and bbr
        for alg in ["cubic", "bbr"]:
            print(f"\nRunning scenario A (3 flows same RTT) for {alg}...")
            r = scenario_three_flows_same_rtt_same_cca(client, server, alg, rtt=50, bw=BANDWIDTH, buffer_pkts=BUFFER_PACKETS)
            all_results.append(r)
            time.sleep(2)

        # Scenario B: 3 flows same CCA different RTTs
        for alg in ["cubic", "bbr"]:
            print(f"\nRunning scenario B (3 flows diff RTT) for {alg}...")
            r = scenario_three_flows_same_cca_diff_rtt(client, server, alg, bw=BANDWIDTH, buffer_pkts=BUFFER_PACKETS)
            all_results.append(r)
            time.sleep(2)

        # Scenario C: 1 cubic vs 1 bbr
        print("\nRunning scenario C (cubic vs bbr)...")
        r = scenario_one_cubic_one_bbr(client, server, bw=BANDWIDTH, rtt=50, buffer_pkts=BUFFER_PACKETS)
        all_results.append(r)

    finally:
        print("\nSaving results...")
        save_results(all_results)
        analyze_and_print(all_results)
        cleanup_infrastructure(client, server)

if __name__ == "__main__":
    main()
