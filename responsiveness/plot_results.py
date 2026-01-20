import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import os

# ===== CONFIG =====
CSV_FILE = "responsiveness_results/responsiveness_timeseries.csv"
OUTPUT_FILE = "responsiveness_results/responsiveness_plot.png"

# Experiment Timeline
T_THROTTLE_START = 15
T_THROTTLE_END = 30
BW_HIGH = 100
BW_LOW = 10

def plot_results():
    if not os.path.exists(CSV_FILE):
        print(f"Error: Could not find {CSV_FILE}")
        return

    # 1. Load Data
    df = pd.read_csv(CSV_FILE)
    
    # Sort by time to ensure lines draw correctly
    df = df.sort_values(by="Time")

    # Set style
    sns.set_theme(style="whitegrid")
    
    # Create a figure with 3 subplots sharing the X-axis
    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    
    # Colors
    colors = {"cubic": "tab:blue", "bbr": "tab:red"}
    
    # --- PLOT 1: THROUGHPUT ---
    ax_thr = axes[0]
    sns.lineplot(data=df, x="Time", y="Throughput_Mbps", hue="Algorithm", 
                 palette=colors, ax=ax_thr, linewidth=2)
    
    # Add Bandwidth Limit Reference Lines
    ax_thr.axhline(y=BW_HIGH, color='gray', linestyle='--', alpha=0.5, label="Link Capacity")
    # Draw the "Ideal" capacity profile
    ax_thr.step([0, T_THROTTLE_START, T_THROTTLE_END, 45], 
                [BW_HIGH, BW_LOW, BW_HIGH, BW_HIGH], 
                where='post', color='green', linestyle=':', alpha=0.6, linewidth=1.5, label="Available BW")

    ax_thr.set_ylabel("Throughput (Mbps)")
    ax_thr.set_title("Responsiveness: Throughput Reaction")
    ax_thr.legend(loc="lower right")

    # --- PLOT 2: RTT (Latency) ---
    ax_rtt = axes[1]
    sns.lineplot(data=df, x="Time", y="RTT_ms", hue="Algorithm", 
                 palette=colors, ax=ax_rtt, linewidth=2, legend=False)
    
    ax_rtt.set_ylabel("RTT (ms)")
    ax_rtt.set_title("Bufferbloat: RTT Spike during Congestion")
    
    # --- PLOT 3: RETRANSMITS ---
    ax_loss = axes[2]
    # We use a scatter/stem plot for retransmits because they are discrete events
    # But lineplot works fine if we just want to see the magnitude
    sns.lineplot(data=df, x="Time", y="Retransmits", hue="Algorithm", 
                 palette=colors, ax=ax_loss, linewidth=2, legend=False)
    
    ax_loss.set_ylabel("Retransmits (pkts)")
    ax_loss.set_xlabel("Time (s)")
    ax_loss.set_title("Efficiency: Retransmissions (Packet Loss)")

    # --- GLOBAL FORMATTING ---
    for ax in axes:
        # Add vertical lines for event triggers
        ax.axvline(x=T_THROTTLE_START, color='black', linestyle='-', alpha=0.3)
        ax.axvline(x=T_THROTTLE_END, color='black', linestyle='-', alpha=0.3)
        
        # Add text labels for phases
        y_lim = ax.get_ylim()
        # Place text slightly above the plot area or inside
        pass 

    # Add phase labels to top plot
    ymax = ax_thr.get_ylim()[1]
    ax_thr.text(T_THROTTLE_START/2, ymax*0.9, "100Mbps", ha='center', fontweight='bold', alpha=0.4)
    ax_thr.text((T_THROTTLE_START+T_THROTTLE_END)/2, ymax*0.9, "Throttle (10Mbps)", ha='center', fontweight='bold', color='darkred', alpha=0.4)
    ax_thr.text((T_THROTTLE_END+45)/2, ymax*0.9, "Recovery (100Mbps)", ha='center', fontweight='bold', color='green', alpha=0.4)

    plt.tight_layout()
    plt.savefig(OUTPUT_FILE, dpi=150)
    print(f"Plot saved to {OUTPUT_FILE}")
    plt.show()

if __name__ == "__main__":
    plot_results()