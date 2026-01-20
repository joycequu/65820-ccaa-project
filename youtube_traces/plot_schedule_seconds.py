import json
import argparse
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import numpy as np
import os

def plot_abr_seconds(json_file, output_file):
    print(f"Loading {json_file}...")
    with open(json_file, 'r') as f: streams = json.load(f)

    # Sort streams to ensure the legend and colors follow the video order
    def get_sort_key(s):
        try: return int(s["stream_id"].split('_')[0])
        except: return 9999
    streams.sort(key=get_sort_key)

    plt.figure(figsize=(16, 9))
    colors = cm.get_cmap('tab10')(np.linspace(0, 1, len(streams)))
    
    added_labels = set()
    max_y = 0

    # --- 1. PLOT CHUNKS ---
    for i, stream in enumerate(streams):
        sid = stream['stream_id']
        col = colors[i % 10]
        
        for ev in stream['events']:
            if ev['action'] == 'download':
                t = ev.get('timestamp_sec', 0)
                dur = ev.get('video_duration_sec', 0)
                is_bg = ev.get('is_background', False)
                
                if dur > max_y: max_y = dur

                # Styling
                hatch_pat = '///' if is_bg else None
                alpha_val = 0.5 if is_bg else 0.8
                edge_col = 'black'
                
                # Legend Label
                lbl = f"Video {sid}"
                if is_bg: lbl += " (Prefetch)"
                if lbl in added_labels: lbl = ""
                else: added_labels.add(lbl)

                plt.bar(t, dur, width=0.5, color=col, alpha=alpha_val, 
                        edgecolor=edge_col, hatch=hatch_pat, label=lbl)
                
                # Text Annotation
                if dur > 0.5:
                    txt = f"{dur:.1f}"
                    if is_bg: txt += "*"
                    plt.text(t, dur, txt, ha='center', va='bottom', fontsize=7)

    # --- 2. PLOT START LINES (GROUND TRUTH) ---
    for stream in streams:
        sid = stream['stream_id']
        t_start = stream.get("playback_start_sec", None)
        
        # CHANGED: Allow 0.0 to be plotted
        if t_start is not None:
            plt.axvline(x=t_start, color='red', linestyle='--', linewidth=2, alpha=0.7)
            
            # Label Placement
            plt.text(t_start, max_y * 1.05, f" Play V{sid}", color='red', 
                     rotation=90, va='bottom', ha='right', fontsize=9, fontweight='bold')

    plt.xlabel("Simulation Time (s)")
    plt.ylabel("Chunk Duration (s)")
    plt.title(f"Schedule Execution: {os.path.basename(json_file)}\n"
              f"(* = Prefetch Chunk | Red Line = Video Start from Logs)")
    
    plt.legend(loc='upper left', bbox_to_anchor=(1, 1), title="Stream Key")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    plt.savefig(output_file)
    print(f"Saved plot to {output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("json_file")
    parser.add_argument("output_file")
    args = parser.parse_args()
    plot_abr_seconds(args.json_file, args.output_file)