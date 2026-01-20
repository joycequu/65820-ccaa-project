import subprocess
import os
import sys
import argparse

def get_quic_flows(input_pcap):
    """
    Scans PCAP for UDP/443 flows. Returns sorted list of (flow_tuple, bytes).
    """
    print(f"[Pass 1] Scanning flows in {input_pcap} ...")

    # TShark extraction
    cmd = [
        "tshark", "-r", input_pcap,
        "-T", "fields",
        "-e", "ip.src", "-e", "udp.srcport",
        "-e", "ip.dst", "-e", "udp.dstport",
        "-e", "frame.len",
        "-Y", "udp.port==443"  # Pre-filter for QUIC in TShark
    ]

    flow_usage = {}

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        for line in proc.stdout:
            parts = line.strip().split('\t')
            if len(parts) != 5: continue

            src, sport, dst, dport, flen = parts
            
            # Normalize flow key (sort IPs/ports to handle bidirectional)
            # Actually, standard Tuple sort is fine for unique ID
            key = tuple(sorted([(src, sport), (dst, dport)]))
            
            flow_usage[key] = flow_usage.get(key, 0) + int(flen)

        proc.wait()
    except FileNotFoundError:
        print("Error: tshark not found.")
        sys.exit(1)

    # Sort by size (Descending)
    sorted_flows = sorted(flow_usage.items(), key=lambda item: item[1], reverse=True)
    return sorted_flows

def build_filter(flows):
    clauses = []
    for (end1, end2), _ in flows:
        # end1 = (ip, port), end2 = (ip, port)
        c = f"(ip.src=={end1[0]} && udp.srcport=={end1[1]} && ip.dst=={end2[0]} && udp.dstport=={end2[1]})"
        c_rev = f"(ip.src=={end2[0]} && udp.srcport=={end2[1]} && ip.dst=={end1[0]} && udp.dstport=={end1[1]})"
        clauses.append(f"({c} || {c_rev})")
    return " || ".join(clauses)

def clean_pcap(input_pcap, experiment_name, top_k):
    if not os.path.exists(input_pcap):
        print(f"Error: {input_pcap} missing.")
        return

    # Output path
    os.makedirs("cleaned_pcap", exist_ok=True)
    out_path = f"cleaned_pcap/{experiment_name}.pcap"

    # 1. Get Flows
    all_flows = get_quic_flows(input_pcap)
    
    # 2. Keep Top K
    keep_flows = all_flows[:top_k]
    print(f"[Pass 1] Identified {len(all_flows)} QUIC flows. Keeping Top {len(keep_flows)}.")
    
    for i, (flow, size) in enumerate(keep_flows):
        print(f"   {i+1}. {flow} - {size/1e6:.2f} MB")

    if not keep_flows:
        print("No QUIC flows found.")
        return

    # 3. Export
    display_filter = build_filter(keep_flows)
    cmd = ["tshark", "-r", input_pcap, "-Y", display_filter, "-w", out_path]
    
    print(f"[Pass 2] Writing to {out_path}...")
    subprocess.run(cmd, check=True)
    print("Clean Complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pcap_file")
    parser.add_argument("experiment_name")
    parser.add_argument("--top_k", type=int, default=1)
    args = parser.parse_args()
    
    clean_pcap(args.pcap_file, args.experiment_name, args.top_k)