#!/usr/bin/env python3

"""
responsiveness_test.py

Single-flow Responsiveness Experiment:
1. Start Flow at 100 Mbps (T=0)
2. Throttle to 10 Mbps (T=15)
3. Release to 100 Mbps (T=30)
4. End at T=45

Metrics: Throughput (Mbps), Retransmits, RTT over time.
"""

import docker, time, json, csv, os, threading

# ===== CONFIG =====
IMAGE_NAME = "tcp-sim-node"
SERVER_NAME = "tcp-server"
CLIENT_PREFIX = "tcp-client-"
NETWORK_NAME = "net_responsiveness"
RESULTS_DIR = "responsiveness_results"
RESULTS_CSV = f"{RESULTS_DIR}/responsiveness_timeseries.csv"

# Experiment Settings
Total_Duration = 45
Throttle_Start = 15
Throttle_End = 30
High_BW = 100
Low_BW = 10
RTT_ms = 40  # Moderate latency
# Buffer_Pkts = 100 # small buffer
Buffer_Pkts = 3000 # large buffer

client = docker.from_env()

# ===== UTILS (Network & Container) =====

def run_container(name, cmd="sleep infinity", network_name=None, static_ip=None):
    try: client.containers.get(name).remove(force=True)
    except: pass

    # Create (Stopped)
    c = client.containers.create(
        IMAGE_NAME, name=name, command=cmd, detach=True, privileged=True,
        cap_add=["NET_ADMIN"]
    )
    
    # Connect Custom Network / Disconnect Bridge
    if network_name and static_ip:
        net = client.networks.get(network_name)
        net.connect(c, ipv4_address=static_ip)
        try: client.networks.get("bridge").disconnect(c)
        except: pass

    c.start()
    c.reload()
    return c

def run_cmd(container, cmd, detach=False):
    # Using sh -c to handle redirects/backgrounding properly
    rc, out = container.exec_run(f"sh -c '{cmd}'", detach=detach)
    return rc, (out.decode() if out else "")

def set_cca(container, alg):
    run_cmd(container, f"sysctl -w net.ipv4.tcp_congestion_control={alg}")

def check_connectivity(client_c, server_ip):
    print("Verifying connectivity...")
    rc, out = run_cmd(client_c, f"ping -c 3 -W 1 {server_ip}")
    if rc != 0:
        raise RuntimeError(f"Ping failed:\n{out}")
    print("Connectivity OK.")

# ===== TRAFFIC CONTROL (DYNAMIC) =====

def apply_initial_tc(container, bw_mbps, rtt_ms, buffer_pkts):
    """Sets up the initial HTB + Netem hierarchy."""
    # Root HTB
    run_cmd(container, "tc qdisc del dev eth0 root || true")
    run_cmd(container, "tc qdisc add dev eth0 root handle 1: htb default 1")
    
    # Class 1:1 (The Rate Limiter)
    run_cmd(container, f"tc class add dev eth0 parent 1: classid 1:1 htb rate {bw_mbps}mbit ceil {bw_mbps}mbit")
    
    # Netem (Delay + Buffer) attached to Class 1:1
    # limit = buffer size in packets
    run_cmd(container, f"tc qdisc add dev eth0 parent 1:1 handle 10: netem delay {rtt_ms}ms limit {buffer_pkts}")

def update_bandwidth(container, new_bw_mbps):
    """Dynamically changes the bandwidth of Class 1:1."""
    print(f"[{time.time():.2f}] !!! CHANGING LINK CAPACITY TO {new_bw_mbps} Mbps !!!")
    run_cmd(container, f"tc class change dev eth0 parent 1: classid 1:1 htb rate {new_bw_mbps}mbit ceil {new_bw_mbps}mbit")

# ===== EXPERIMENT LOGIC =====

def run_responsiveness_test(server, client_c, alg):
    print(f"\n=== Running Responsiveness Test: {alg} ===")
    
    # 1. Setup CCA & Initial TC (High BW)
    set_cca(client_c, alg)
    set_cca(server, alg)
    apply_initial_tc(client_c, High_BW, RTT_ms, Buffer_Pkts)

    # 2. Start iperf3 Server (Daemon)
    run_cmd(server, "pkill -f iperf3 || true")
    run_cmd(server, "iperf3 -s -D")
    
    # 3. Start iperf3 Client (Background, JSON output)
    # We write to a file inside the container, then read it later
    cmd = f"iperf3 -c 10.10.1.4 -t {Total_Duration} -i 1 -J > /tmp/iperf_results.json"
    run_cmd(client_c, cmd, detach=True)
    
    start_time = time.time()
    
    # 4. Timeline Execution
    # Phase 1: High Bandwidth (0 - 15s)
    time.sleep(Throttle_Start)
    
    # Phase 2: Throttle Down (15 - 30s)
    update_bandwidth(client_c, Low_BW)
    time.sleep(Throttle_End - Throttle_Start)
    
    # Phase 3: Ramp Up (30 - 45s)
    update_bandwidth(client_c, High_BW)
    
    # Wait for remaining time + buffer for file write
    remaining = Total_Duration - (time.time() - start_time)
    if remaining > 0:
        time.sleep(remaining + 2)

    # 5. Retrieve Data
    print("Retrieving data...")
    rc, json_str = run_cmd(client_c, "cat /tmp/iperf_results.json")
    
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        print("Error: Failed to parse iperf3 JSON. Container output:")
        print(json_str)
        return []

    # 6. Parse Intervals
    timeseries = []
    if "intervals" in data:
        for point in data["intervals"]:
            sum_data = point["streams"][0] # Assuming single stream
            
            # iperf3 time intervals
            t_start = float(sum_data["start"])
            t_end = float(sum_data["end"])
            
            # Metrics
            mbps = sum_data["bits_per_second"] / 1_000_000.0
            retrans = sum_data["retransmits"]
            rtt = sum_data.get("rtt", 0) / 1000.0 # ms if available (only in recent iperf3 versions)
            cwnd = sum_data.get("snd_cwnd", 0) / 1000.0 # KBytes
            
            timeseries.append({
                "Algorithm": alg,
                "Time": t_end,
                "Throughput_Mbps": mbps,
                "Retransmits": retrans,
                "Cwnd_KB": cwnd,
                "RTT_ms": rtt
            })
            
    return timeseries

def setup_topology():
    TARGET_SUBNET = "10.10.1.0/24"
    
    # 1. CLEANUP CONTAINERS FIRST
    # We must remove containers before we can remove the network they are attached to.
    print("Cleaning up old containers...")
    for name in [SERVER_NAME, f"{CLIENT_PREFIX}0"]:
        try: client.containers.get(name).remove(force=True)
        except: pass

    # 2. AGGRESSIVE NETWORK CLEANUP
    # Scan for any network using our subnet or our name
    print(f"Checking for networks conflicting with {TARGET_SUBNET}...")
    for n in client.networks.list():
        try:
            # Check by Subnet
            ipam = n.attrs.get("IPAM", {})
            if ipam and "Config" in ipam:
                for config in ipam["Config"]:
                    if config.get("Subnet") == TARGET_SUBNET:
                        print(f"Removing conflicting network (Subnet match): {n.name}")
                        n.remove()
                        break
            
            # Check by Name (if subnet didn't match but name does)
            if n.name == NETWORK_NAME:
                print(f"Removing conflicting network (Name match): {n.name}")
                n.remove()
        except Exception as e:
            # Sometimes standard networks cannot be removed, which is fine
            pass
    
    # 3. Create Network
    ipam = docker.types.IPAMConfig(pool_configs=[
        docker.types.IPAMPool(subnet=TARGET_SUBNET, gateway="10.10.1.1")
    ])
    net = client.networks.create(NETWORK_NAME, driver="bridge", ipam=ipam)
    
    # 4. Start Containers
    print("Starting containers...")
    server = run_container(SERVER_NAME, network_name=NETWORK_NAME, static_ip="10.10.1.4")
    client_c = run_container(f"{CLIENT_PREFIX}0", network_name=NETWORK_NAME, static_ip="10.10.1.3")
    
    # 5. Offloads
    run_cmd(server, "ethtool -K eth0 gro off gso off tso off || true")
    run_cmd(client_c, "ethtool -K eth0 gro off gso off tso off || true")
    
    # 6. Check Connectivity
    # check_connectivity(client_c, server, "10.10.1.4")
    
    return server, client_c, net

def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    server, client_c, net = None, None, None
    all_data = []

    try:
        server, client_c, net = setup_topology()

        for alg in ["cubic", "bbr"]:
            data = run_responsiveness_test(server, client_c, alg)
            all_data.extend(data)
            time.sleep(2) # Cooldown

    finally:
        # Save CSV
        if all_data:
            keys = all_data[0].keys()
            with open(RESULTS_CSV, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                w.writerows(all_data)
            print(f"\nSaved results to {RESULTS_CSV}")
        
        # Cleanup
        if client_c: client_c.remove(force=True)
        if server: server.remove(force=True)
        if net: net.remove()

if __name__ == "__main__":
    main()