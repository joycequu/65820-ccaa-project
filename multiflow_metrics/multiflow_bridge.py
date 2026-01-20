#!/usr/bin/env python3
"""
multiflow_switch_container.py

Multi-flow fairness experiments using Docker containers with a switch container:
- 3 clients connected to a "client network"
- 1 switch container connects clients and server
- 1 server connected to "server network"
- Bottleneck bandwidth applied in switch container toward server
- Per-client RTT applied via netem on client containers
"""
import docker, subprocess, time, threading, json, csv, os
from statistics import mean

# ===== CONFIG =====
IMAGE_NAME = "tcp-sim-node"
SERVER_NAME = "tcp-server"
SWITCH_NAME = "tcp-switch"
CLIENT_PREFIX = "tcp-client-"
NUM_CLIENTS = 3

RESULTS_DIR = "multiflow_switch_results"
RESULTS_JSON = f"{RESULTS_DIR}/results.json"
RESULTS_CSV = f"{RESULTS_DIR}/results.csv"

BANDWIDTH_MBPS = 1000
BUFFER_PKTS = 1800
RTTS_MS = [10, 50, 200]
DOWNLOAD_TIMEOUT = 3600
STARTUP_SLEEP = 2.0

client = docker.from_env()

# ===== UTILS =====
def sh(cmd, check=False):
    print(f"[host] $ {cmd}")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.stdout: print(r.stdout.strip())
    if r.stderr: print(r.stderr.strip())
    if check and r.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{r.stderr}")
    return r

def run_container(name, image=IMAGE_NAME, cmd="sleep infinity", privileged=True, networks=None):
    try:
        c = client.containers.get(name)
        c.remove(force=True)
    except:
        pass
    c = client.containers.run(
        image, name=name, command=cmd, detach=True, privileged=privileged,
        cap_add=["NET_ADMIN"], network=networks[0] if networks else None
    )
    # connect to additional networks if provided
    if networks:
        for net in networks[1:]:
            client.networks.get(net).connect(c)
    time.sleep(0.5)
    c.reload()
    return c

def container_ip(container, network_name=None):
    container.reload()
    nets = container.attrs["NetworkSettings"]["Networks"]
    if network_name:
        return nets[network_name]["IPAddress"]
    return list(nets.values())[0]["IPAddress"]

def run_in_container(container, cmd, detach=False):
    print(f"[{container.name}] $ {cmd}")
    rc, out = container.exec_run(["/bin/bash", "-lc", cmd], demux=False, detach=detach)
    out_str = (out.decode() if isinstance(out, bytes) else str(out)) if out else ""
    return rc, out_str

def apply_client_netem(client_container, rtt_ms, buffer_pkts=BUFFER_PKTS):
    run_in_container(client_container, "tc qdisc del dev eth0 root || true")
    run_in_container(client_container, f"tc qdisc add dev eth0 root netem delay {rtt_ms}ms limit {buffer_pkts}")

def disable_offloads(container):
    run_in_container(container, "ethtool -K eth0 gro off gso off tso off || true")

def set_cca(container, alg):
    run_in_container(container, f"sysctl -w net.ipv4.tcp_congestion_control={alg}")

def start_clients_http(clients):
    for c in clients:
        rc, out = run_in_container(c, "pgrep nginx || true")
        if rc == 0 and out.strip(): continue
        run_in_container(c, "nohup nginx -g 'daemon off;' &>/tmp/nginx.log & || true")
        run_in_container(c, "cd /var/www/html && python3 -m http.server 80", detach=True)

def server_download_from_client(server, client_ip):
    cmd = f"curl -s -w '%{{time_total}},%{{size_download}}' -o /dev/null http://{client_ip}/testfile.bin --max-time {DOWNLOAD_TIMEOUT}"
    rc, out = run_in_container(server, cmd)
    out = (out or "").strip()
    if not out: return 0.0, None, None
    # print("out", out)
    try:
        t_str, s_str = out.split(",")
        t = float(t_str)
        s = float(s_str)
        thr_mbps = (s*8)/t/1_000_000.0 if t>0 else 0.0
        print("got values", t, s, thr_mbps)
        return thr_mbps, t, s
    except:
        return 0.0, None, None

def parallel_server_downloads(server, client_ips):
    results = [None]*len(client_ips)
    threads = []
    def worker(i, ip):
        results[i] = server_download_from_client(server, ip)
    for i, ip in enumerate(client_ips):
        t = threading.Thread(target=worker, args=(i, ip))
        t.start()
        threads.append(t)
    for t in threads: t.join()
    return results

def jains_fairness(values):
    if not values: return None
    s = sum(values)
    sq = sum(v*v for v in values)
    n = len(values)
    if sq==0: return 0.0
    return (s*s)/(n*sq)

def save_results(all_results):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(RESULTS_JSON, "w") as f: json.dump(all_results, f, indent=2)
    rows=[]
    for res in all_results:
        base={"scenario": res.get("scenario"), "fairness": res.get("fairness"), "bw_mbps": res.get("bw_mbps")}
        for p in res["per_flow"]:
            row = base.copy(); row.update(p); rows.append(row)
    if rows:
        keys=sorted(rows[0].keys())
        with open(RESULTS_CSV,"w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=keys); w.writeheader(); w.writerows(rows)
    print(f"[results] saved to {RESULTS_DIR}")

# ===== NETWORK SETUP USING SWITCH CONTAINER =====
def setup_switch_topology(server, clients):
    # 1. Create networks
    try: 
        client.networks.get("net_clients").remove(); 
    except: pass
    try: 
        client.networks.get("net_server").remove(); 
    except: pass
    # net_clients = client.networks.create("net_clients", driver="bridge", subnet="10.10.1.0/24")
    # net_server = client.networks.create("net_server", driver="bridge", subnet="10.10.2.0/24")
    ipam_clients = docker.types.IPAMConfig(pool_configs=[docker.types.IPAMPool(subnet="10.10.1.0/24")])
    net_clients = client.networks.create("net_clients", driver="bridge", ipam=ipam_clients)

    ipam_server = docker.types.IPAMConfig(pool_configs=[docker.types.IPAMPool(subnet="10.10.2.0/24")])
    net_server = client.networks.create("net_server", driver="bridge", ipam=ipam_server)

    for c in clients:
        net_clients.connect(c)
    net_server.connect(server)

    # 2. Run switch container attached to both networks
    switch = run_container(SWITCH_NAME, networks=["net_clients","net_server"])

    # Disconnect switch from all networks first
    for net_name, net_info in switch.attrs["NetworkSettings"]["Networks"].items():
        try:
            client.networks.get(net_name).disconnect(switch, force=True)
        except Exception:
            pass

    # Connect switch to networks (you already do this)
    net_clients.connect(switch)
    net_server.connect(switch)

    # Enable ip forwarding and allow forwarding in switch
    run_in_container(switch, "sysctl -w net.ipv4.ip_forward=1")
    run_in_container(switch, "iptables -P FORWARD ACCEPT || true")

    # NEW: Disable offloads on switch interfaces (they are eth0 and eth1)
    run_in_container(switch, "ethtool -K eth0 gro off gso off tso off || true")
    run_in_container(switch, "ethtool -K eth1 gro off gso off tso off || true")


    # Get switch IPs
    sw_cli_ip = container_ip(switch, "net_clients")
    sw_srv_ip = container_ip(switch, "net_server")

    # 1. ROUTING: Clients -> Server (via Switch)
    # We remove 'dev eth0' to let kernel pick the right interface
    for c in clients:
        run_in_container(c, f"ip route del 10.10.2.0/24 || true")
        run_in_container(c, f"ip route add 10.10.2.0/24 via {sw_cli_ip}")

    # 2. ROUTING: Server -> Clients (via Switch)
    run_in_container(server, f"ip route del 10.10.1.0/24 || true")
    run_in_container(server, f"ip route add 10.10.1.0/24 via {sw_srv_ip}")

    # 3. SWITCH CONFIGURATION
    # Enable Forwarding
    run_in_container(switch, "sysctl -w net.ipv4.ip_forward=1")
    run_in_container(switch, "iptables -P FORWARD ACCEPT || true")
    
    # Disable offloads on ALL switch interfaces (generic loop)
    # This ensures we catch eth0, eth1, eth2, whatever they are named
    run_in_container(switch, "bash -c 'for i in /sys/class/net/eth*; do ethtool -K $(basename $i) gro off gso off tso off; done'")

    # Apply Bottleneck on Switch interface facing the Server
    # We need to be sure which interface faces the server. 
    # Since we connected net_server SECOND, it is likely eth1, but let's be robust.
    # (If you disconnected default bridge, eth1 might actually be eth1 or eth0 depending on race conditions).
    # For safety in this specific script, assuming order was: 1. net_clients, 2. net_server
    # Interface facing server is eth1.
    
    run_in_container(switch, f"tc qdisc del dev eth1 root || true")
    run_in_container(switch, f"tc qdisc add dev eth1 root handle 1: htb default 1")
    run_in_container(switch, f"tc class add dev eth1 parent 1: classid 1:1 htb rate {BANDWIDTH_MBPS}mbit ceil {BANDWIDTH_MBPS}mbit")
    run_in_container(switch, f"tc qdisc add dev eth1 parent 1:1 handle 10: sfq")

    return net_clients, net_server, switch

def _container_pid(container):
    container.reload()
    return container.attrs["State"]["Pid"]

def check_connectivity(source_container, target_ip, port=80):
    cmd = (
        f"python3 -c \"import socket; "
        f"s = socket.create_connection(('{target_ip}', {port}), timeout=2); "
        f"print('CONNECTED'); s.close()\""
    )
    rc, out = run_in_container(source_container, cmd)
    return "CONNECTED" in out

# ===== MAIN =====
def main():
    server = None
    clients = []
    switch = None
    all_results = []

    try:
        # # Cleanup old containers
        for name in [SERVER_NAME, SWITCH_NAME]+[f"{CLIENT_PREFIX}{i}" for i in range(NUM_CLIENTS)]:
            try: client.containers.get(name).remove(force=True)
            except: pass

        # # Run server container (connect later after switch networks exist)
        # server = run_container(SERVER_NAME)

        # # Run clients
        # for i in range(NUM_CLIENTS):
        #     clients.append(run_container(f"{CLIENT_PREFIX}{i}"))

        # # NEW: Disconnect from default bridge network
        # default_bridge = client.networks.get("bridge")
        # default_bridge.disconnect(server, force=True)
        # for c in clients:
        #     default_bridge.disconnect(c, force=True)

        # time.sleep(1)
        # # Setup switch topology
        # net_clients, net_server, switch = setup_switch_topology(server, clients)

        # # Connect server to server network
        # # net_server.connect(server)

        # time.sleep(1)  # allow network convergence

        # client_ips = [container_ip(c, "net_clients") for c in clients]
        # server_ip = container_ip(server, "net_server")
        # print("server ip:", server_ip)
        # print("client ips:", client_ips)

        







        # 1. SETUP NETWORKS FIRST
        try: client.networks.get("net_clients").remove(); 
        except: pass
        try: client.networks.get("net_server").remove(); 
        except: pass

        ipam_clients = docker.types.IPAMConfig(pool_configs=[docker.types.IPAMPool(subnet="10.10.1.0/24")])
        net_clients = client.networks.create("net_clients", driver="bridge", ipam=ipam_clients)

        ipam_server = docker.types.IPAMConfig(pool_configs=[docker.types.IPAMPool(subnet="10.10.2.0/24")])
        net_server = client.networks.create("net_server", driver="bridge", ipam=ipam_server)

        # 2. RUN SWITCH (Connected to BOTH)
        # Note: eth0 will be net_clients, eth1 will be net_server
        switch = run_container(SWITCH_NAME, networks=["net_clients", "net_server"])
        
        # 3. RUN SERVER (Connected ONLY to net_server)
        # This prevents the default bridge from messing up the routing table
        server = run_container(SERVER_NAME, networks=["net_server"])
        
        # 4. RUN CLIENTS (Connected ONLY to net_clients)
        for i in range(NUM_CLIENTS):
            clients.append(run_container(f"{CLIENT_PREFIX}{i}", networks=["net_clients"]))

        time.sleep(1) # Let networks settle

        # 5. CONFIGURE ROUTING (Modified setup_switch_topology logic inline or called)
        # We can simplify setup_switch_topology since connections are done.
        
        sw_cli_ip = container_ip(switch, "net_clients")
        sw_srv_ip = container_ip(switch, "net_server")
        print(f"Switch IPs: Client-Side={sw_cli_ip}, Server-Side={sw_srv_ip}")

        # Switch Forwarding
        run_in_container(switch, "sysctl -w net.ipv4.ip_forward=1")
        # Disable Reverse Path Filtering (Crucial for asymmetric routing!)
        run_in_container(switch, "sysctl -w net.ipv4.conf.all.rp_filter=0")
        run_in_container(switch, "sysctl -w net.ipv4.conf.default.rp_filter=0")
        run_in_container(switch, "sysctl -w net.ipv4.conf.eth0.rp_filter=0")
        run_in_container(switch, "sysctl -w net.ipv4.conf.eth1.rp_filter=0")
        # Disable Offloads
        run_in_container(switch, "ethtool -K eth0 gro off gso off tso off || true")
        run_in_container(switch, "ethtool -K eth1 gro off gso off tso off || true")

        # Client Routes
        for c in clients:
            # Route 10.10.2.0/24 (Server Net) -> Switch IP
            run_in_container(c, f"ip route add 10.10.2.0/24 via {sw_cli_ip}")
            # Disable Offloads
            run_in_container(c, "ethtool -K eth0 gro off gso off tso off || true")

        # Server Routes
        # Route 10.10.1.0/24 (Client Net) -> Switch IP
        run_in_container(server, f"ip route add 10.10.1.0/24 via {sw_srv_ip}")
        run_in_container(server, "ethtool -K eth0 gro off gso off tso off || true")

        # Bottleneck on Switch (eth1 faces the server network)
        run_in_container(switch, f"tc qdisc add dev eth1 root handle 1: htb default 1")
        run_in_container(switch, f"tc class add dev eth1 parent 1: classid 1:1 htb rate {BANDWIDTH_MBPS}mbit ceil {BANDWIDTH_MBPS}mbit")
        # run_in_container(switch, f"tc qdisc add dev eth1 parent 1:1 handle 10: sfq")
        
        # BUFFER_PKTS = 1800 # Use the same buffer size you defined for netem
        run_in_container(switch, f"tc qdisc add dev eth1 parent 1:1 handle 10: pfifo limit {BUFFER_PKTS}")

        # 6. START APPS & VERIFY
        start_clients_http(clients)
        time.sleep(2)
        
        print("\n--- CHECKING CONNECTIVITY ---")
        client_ips = [container_ip(c, "net_clients") for c in clients]
        if check_connectivity(server, client_ips[0], 80):
            print("SUCCESS: Server can reach Client!")
        else:
            print("FAILURE: Server CANNOT reach Client.")
            # Debugging info
            print("Server Routes:")
            print(run_in_container(server, "ip route")[1])
            print("Switch Routes:")
            print(run_in_container(switch, "ip route")[1])
            return







        # Run experiments
        for alg in ["cubic","bbr"]:
            for rtts in [[50]*NUM_CLIENTS, RTTS_MS[:NUM_CLIENTS]]:
                scenario_name = f"{alg}_{'_'.join(map(str,rtts))}"
                for c,r in zip(clients,rtts):
                    apply_client_netem(c, r)
                    disable_offloads(c)
                    set_cca(c, alg)
                set_cca(server, alg)
                disable_offloads(server)
                start_clients_http(clients)
                time.sleep(STARTUP_SLEEP)

                dl_results = parallel_server_downloads(server, client_ips)
                per_flow = []
                for idx,(thr,t,s) in enumerate(dl_results):
                    per_flow.append({"flow_id": idx, "client_ip": client_ips[idx], "cca": alg,
                                     "rtt_ms": rtts[idx], "large_throughput_mbps": thr,
                                     "large_fct_seconds": t, "size_bytes": s})
                all_results.append({"scenario": scenario_name,
                                    "per_flow": per_flow,
                                    "fairness": jains_fairness([p["large_throughput_mbps"] or 0 for p in per_flow]),
                                    "bw_mbps": BANDWIDTH_MBPS,
                                    "rtts_ms": rtts})
    finally:
        save_results(all_results)
        for c in clients + [server, switch] if switch else []:
            try: c.remove(force=True)
            except: pass
        try: net_clients.remove()
        except: pass
        try: net_server.remove()
        except: pass

if __name__=="__main__":
    main()
