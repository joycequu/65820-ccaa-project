import json
import matplotlib.pyplot as plt
import argparse
import numpy as np
import os

def load_results(base_dir, exp_name, algo):
    """
    Loads the JSON result for a specific experiment and algorithm 
    from a specific directory.
    """
    # Construct path: base_dir/results_{exp_name}_{algo}.json
    filename = f"results_{exp_name}_{algo}.json"
    full_path = os.path.join(base_dir, filename)
    
    if not os.path.exists(full_path):
        print(f"WARNING: Could not find {full_path}")
        return None
    
    try:
        with open(full_path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"ERROR reading {full_path}: {e}")
        return None

def plot_timeseries(ax, data_cubic, data_bbr, key, label, ylabel):
    """Generic function to plot time series for both algos."""
    has_data = False
    
    # Plot CUBIC (Blue)
    if data_cubic and key in data_cubic and data_cubic[key]:
        times = [x[0] for x in data_cubic[key]]
        vals  = [x[1] for x in data_cubic[key]]
        if "bitrate" in key:
            ax.step(times, vals, where='post', label="CUBIC", color='blue', linestyle='-')
        else:
            ax.plot(times, vals, label="CUBIC", color='blue', alpha=0.8)
        has_data = True

    # Plot BBR (Red)
    if data_bbr and key in data_bbr and data_bbr[key]:
        times = [x[0] for x in data_bbr[key]]
        vals  = [x[1] for x in data_bbr[key]]
        if "bitrate" in key:
            ax.step(times, vals, where='post', label="BBR", color='red', linestyle='--')
        else:
            ax.plot(times, vals, label="BBR", color='red', alpha=0.8, linestyle='--')
        has_data = True

    ax.set_ylabel(ylabel)
    ax.set_xlabel("Time (s)")
    ax.grid(True, linestyle='--', alpha=0.6)
    if has_data:
        ax.legend()
    else:
        ax.text(0.5, 0.5, "No Data", ha='center', transform=ax.transAxes)

def plot_fairness_grouped(ax, cubic_rates, bbr_rates):
    """Plots a grouped bar chart for per-stream throughput."""
    c_keys = list(cubic_rates.keys()) if cubic_rates else []
    b_keys = list(bbr_rates.keys()) if bbr_rates else []
    all_streams = sorted(list(set(c_keys + b_keys)), key=lambda x: int(x.split('_')[0]))
    
    if not all_streams:
        ax.text(0.5, 0.5, "No Fairness Data", ha='center')
        return

    x = np.arange(len(all_streams))
    width = 0.35

    c_vals = [cubic_rates.get(s, 0) for s in all_streams]
    b_vals = [bbr_rates.get(s, 0) for s in all_streams]

    ax.bar(x - width/2, c_vals, width, label='CUBIC', color='blue', alpha=0.7)
    ax.bar(x + width/2, b_vals, width, label='BBR', color='red', alpha=0.7)

    ax.set_ylabel("Avg Throughput (Mbps)")
    ax.set_title("Per-Stream Fairness Distribution")
    ax.set_xticks(x)
    ax.set_xticklabels(all_streams)
    ax.legend()
    ax.grid(True, axis='y', linestyle='--', alpha=0.6)

def visualize_comparison(exp_name, r_cubic, r_bbr, out_dir):
    fig, axs = plt.subplots(4, 1, figsize=(12, 20))
    fig.suptitle(f"Comparison: {exp_name} (CUBIC vs BBR)", fontsize=16)

    # 1. Throughput Timeseries
    plot_timeseries(axs[0], r_cubic, r_bbr, "throughput_timeseries", "Throughput", "Throughput (Mbps)")
    axs[0].set_title("Throughput Over Time")

    # 2. Bitrate Decisions
    plot_timeseries(axs[1], r_cubic, r_bbr, "bitrate_timeseries", "Bitrate", "Bitrate (Mbps)")
    axs[1].set_title("Selected Bitrate Over Time")
    axs[1].set_yticks([0.5, 1.0, 2.5, 5.0, 8.0]) 

    # 3. Buffer Health
    plot_timeseries(axs[2], r_cubic, r_bbr, "buffer_timeseries", "Buffer", "Buffer (sec)")
    axs[2].set_title("Buffer Health (Stall Risk)")
    axs[2].axhline(y=0, color='black', linewidth=1)

    # 4. Per-stream Fairness
    c_fair = r_cubic.get("per_stream_avg_throughput_mbps", {}) if r_cubic else {}
    b_fair = r_bbr.get("per_stream_avg_throughput_mbps", {}) if r_bbr else {}
    plot_fairness_grouped(axs[3], c_fair, b_fair)

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    
    # Save plot to the SAME directory as the data
    out_file = os.path.join(out_dir, f"plot_{exp_name}_comparison.png")
    fig.savefig(out_file)
    print(f"Saved comparison plot to {out_file}")
    plt.show()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="Experiment name (e.g. abr_doomscroll-2)")
    parser.add_argument("--dir", default=".", help="Directory containing result files")
    args = parser.parse_args()

    print(f"Loading results for {args.name} from {args.dir}...")
    cubic_res = load_results(args.dir, args.name, "cubic")
    bbr_res   = load_results(args.dir, args.name, "bbr")

    if not cubic_res and not bbr_res:
        print("Error: No result files found for either CUBIC or BBR in that directory.")
    else:
        visualize_comparison(args.name, cubic_res, bbr_res, args.dir)