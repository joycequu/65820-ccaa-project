import csv
import json
import argparse
import os

def load_stats_and_find_switches(stats_file):
    """
    Loads stats and identifies the EXACT time each new video_id appeared.
    Returns:
      data: Full stats list
      switches: List of {'video_id': str, 'epoch_time': float} representing start times.
    """
    data = []
    switches = []
    last_vid = None
    
    try:
        with open(stats_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    t = float(row["epoch_time"])
                    vid = row.get("video_id", "unknown")
                    
                    # Record the first time we see a new video ID
                    if vid != last_vid and vid:
                        switches.append({'video_id': vid, 'epoch_time': t})
                        last_vid = vid
                        
                    data.append({
                        "time": t,
                        "buffer": float(row["buffer_health_sec"]),
                        "video_id": vid
                    })
                except: continue
    except: return [], []
    
    data.sort(key=lambda x: x["time"])
    return data, switches

def get_buffer(stats, t):
    if not stats: return 0.0
    if t <= stats[0]["time"]: return stats[0]["buffer"]
    if t >= stats[-1]["time"]: return stats[-1]["buffer"]
    
    for i in range(len(stats)-1):
        if stats[i]["time"] <= t <= stats[i+1]["time"]:
            ratio = (t - stats[i]["time"]) / (stats[i+1]["time"] - stats[i]["time"])
            return stats[i]["buffer"] + ratio * (stats[i+1]["buffer"] - stats[i]["buffer"])
    return 0.0

def process_schedule(pcap_csv, stats_csv, output_json, min_mb=1.0):
    stats, video_switches = load_stats_and_find_switches(stats_csv)
    
    if not stats:
        print("No stats data found!")
        return

    # Load PCAP packets
    all_packets = []
    with open(pcap_csv, 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            try:
                all_packets.append({
                    'sid': row[0],
                    'ts': float(row[1]),
                    'len': int(row[2])
                })
            except: continue
            
    if not all_packets: return

    # Global Zero Time (First packet observed in PCAP)
    global_start_time = min(p['ts'] for p in all_packets)

    # Group by Flow
    flows = {}
    for p in all_packets:
        if p['sid'] not in flows: flows[p['sid']] = []
        flows[p['sid']].append(p)

    schedule = []
    
    # Sort flows by their START time to match them with video_switches order
    sorted_flow_ids = sorted(flows.keys(), key=lambda k: min(p['ts'] for p in flows[k]))

    for i, sid in enumerate(sorted_flow_ids):
        packets = flows[sid]
        packets.sort(key=lambda x: x['ts'])
        total_mb = sum(p['len'] for p in packets) / 1048576
        
        # Skipping logic: If it's too small, ignore it
        if total_mb < min_mb: continue

        # --- MATCH FLOW TO STATS VIDEO START TIME ---
        # 1. Force First Video to start at 0.0
        if i == 0:
             playback_start_rel = 0.0
        # 2. For subsequent videos, use the stats file timestamp
        elif i < len(video_switches):
            # Calculate relative start time: (VideoSwitchTime - PcapGlobalStart)
            playback_start_rel = max(0.0, video_switches[i]['epoch_time'] - global_start_time)
        # 3. Fallback
        else:
            playback_start_rel = schedule[-1]['playback_start_sec'] + 15.0 if schedule else 0.0

        events = []
        burst_sz = 0
        burst_start = packets[0]['ts']
        last_ts = packets[0]['ts']

        def flush_burst(start, end, size):
            buf_start = get_buffer(stats, start)
            buf_end = get_buffer(stats, end)
            real_time = max(0, end - start)
            
            vid_dur = (buf_end - buf_start) + real_time
            vid_dur = max(0.0, vid_dur)

            # --- PREFETCH DETECTION ---
            # Mark as background if size is large but video duration is tiny
            is_bg = False
            if size > 500000 and vid_dur < 0.5: 
                vid_dur = (size * 8) / 2500000 
                is_bg = True

            return {
                "timestamp_sec": round(start - global_start_time, 3), 
                "action": "download",
                "size_mb": round(size/1048576, 3),
                "video_duration_sec": round(vid_dur, 2),
                "is_background": is_bg
            }

        for p in packets:
            gap = p['ts'] - last_ts
            if gap > 0.5:
                events.append(flush_burst(burst_start, last_ts, burst_sz))
                burst_sz = p['len']
                burst_start = p['ts']
            else:
                burst_sz += p['len']
            last_ts = p['ts']
        
        if burst_sz > 0:
            events.append(flush_burst(burst_start, last_ts, burst_sz))

        if events:
            events.sort(key=lambda x: x['timestamp_sec'])
            schedule.append({
                "stream_id": sid,
                "playback_start_sec": round(playback_start_rel, 3), # <--- FORCE 0.0 FOR FIRST VIDEO
                "events": events
            })

    os.makedirs(os.path.dirname(output_json), exist_ok=True)
    with open(output_json, 'w') as f: json.dump(schedule, f, indent=2)
    print(f"Saved schedule to {output_json}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pcap_csv")
    parser.add_argument("stats_csv")
    parser.add_argument("output")
    args = parser.parse_args()
    process_schedule(args.pcap_csv, args.stats_csv, args.output)