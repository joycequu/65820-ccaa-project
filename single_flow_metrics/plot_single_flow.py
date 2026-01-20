import json
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
import os
import math

# ----------------------------------------------------
# Configuration
# ----------------------------------------------------
RESULTS_DIR = "1_single_flow_results"
DATA_FILE = os.path.join(RESULTS_DIR, "final_sensitivity_results.json")

# GLOBAL COLORS: CUBIC = Blue, BBR = Orange
CUSTOM_PALETTE = {"cubic": "tab:blue", "bbr": "tab:orange"}

# (Category, JSON Key, Axis Label)
ALL_METRICS = [
    ("Video", "video_throughput_mbps", "Video T-Put (Mbps)"),
    ("Video", "video_jitter_mbps",     "Video Jitter (Mbps)"),
    ("Video", "video_rebuf_ratio",     "Video Rebuf Ratio"),
    ("Web",   "web_avg_ttfb_s",        "Web TTFB (s)"),
    ("Web",   "web_plt_s",             "Web PLT (s)"),
    ("File",  "large_throughput_mbps", "File T-Put (Mbps)"),
    ("File",  "large_fct_s",           "File Completion (s)")
]

def load_and_clean_data():
    if not os.path.exists(DATA_FILE):
        print(f"Error: Data file not found at {DATA_FILE}")
        return pd.DataFrame()
    
    with open(DATA_FILE, 'r') as f:
        data = json.load(f)
    
    df = pd.DataFrame(data)

    # Handle explicit multiplier field
    df['bdp_mult'] = df.get('bdp_multiplier', 0.0)
    df.sort_values(by='bdp_mult', inplace=True)
    df['Buffer Size'] = df['bdp_mult'].apply(lambda x: f"{x}x BDP")

    # Clean loss label
    def clean_loss_label(row):
        if "Bursty" in str(row["matrix"]): return "2% (Bursty)"
        if row["loss_pct"] == 0: return "0% (Control)"
        return f"{row['loss_pct']}%"
    df["loss_label"] = df.apply(clean_loss_label, axis=1)

    return df

def setup_plot_style():
    sns.set_theme(style="whitegrid")
    plt.rcParams.update({
        "figure.figsize": (16, 5),
        "axes.titlesize": 13,
        "axes.labelsize": 11,
        "lines.linewidth": 2.5,
        "legend.fontsize": 11
    })

# ----------------------------------------------------
# Helper: Generic Grid Plotter
# ----------------------------------------------------
def plot_grid(df, x_col, x_label, title, filename, plot_type="line", log_x=False):
    """
    Generic function to plot ALL_METRICS in a 2-row grid.
    plot_type: 'line' (for continuous X) or 'bar' (for categorical X)
    """
    num_plots = len(ALL_METRICS)
    cols = 4
    rows = math.ceil(num_plots / cols)
    
    fig, axes = plt.subplots(rows, cols, figsize=(5.5 * cols, 5 * rows))
    fig.suptitle(title, fontsize=18, y=0.99)
    
    # Flatten axes array for easy indexing
    axes_flat = axes.flatten()
    
    legend_handles, legend_labels = [], []

    for i, (cat, key, label) in enumerate(ALL_METRICS):
        ax = axes_flat[i]
        
        # Check if metric exists in data (avoid crash if simulation skipped one)
        if key not in df.columns:
            ax.text(0.5, 0.5, "Data Missing", ha='center')
            continue

        if plot_type == "line":
            # For Line plots: Style = Buffer Size
            sns.lineplot(
                ax=ax, data=df, x=x_col, y=key,
                hue="alg", style="Buffer Size",
                palette=CUSTOM_PALETTE,
                markers=True, dashes=True, markersize=8, ci=None
            )
        elif plot_type == "bar":
            # For Bar plots: No style, just bars
            # Sort order for Loss
            order = ["0% (Control)", "0.001%", "1.0%", "2.0%", "2% (Bursty)"]
            sns.barplot(
                ax=ax, data=df, x=x_col, y=key,
                hue="alg", order=order,
                palette=CUSTOM_PALETTE,
                errorbar=None
            )
            ax.tick_params(axis='x', rotation=30)

        # Handle Legends
        if not legend_handles:
            h, l = ax.get_legend_handles_labels()
            legend_handles.extend(h)
            legend_labels.extend(l)
        ax.legend().remove()

        ax.set_title(f"{cat}: {label}")
        ax.set_xlabel(x_label)
        ax.set_ylabel(label)
        
        if log_x: ax.set_xscale("log")
        ax.grid(True, linestyle="--", alpha=0.6)

    # Hide unused subplots
    for j in range(i + 1, len(axes_flat)):
        axes_flat[j].axis("off")

    # Global Legend
    fig.legend(
        legend_handles, legend_labels,
        loc="upper center", bbox_to_anchor=(0.5, 0.02),
        ncol=6, frameon=False
    )
    
    fig.tight_layout(rect=[0, 0.05, 1, 0.96]) # Leave space for title/legend
    save_path = os.path.join(RESULTS_DIR, filename)
    fig.savefig(save_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Generated: {save_path}")

# ----------------------------------------------------
# Matrix Wrappers
# ----------------------------------------------------

def plot_matrix_A(df):
    subset = df[df["matrix"] == "Latency"]
    if subset.empty: return
    plot_grid(subset, "rtt_ms", "RTT (ms)", 
              "Matrix A: Latency Impact (Line Style = Buffer Size)", 
              "graph_A_latency.png", plot_type="line")

def plot_matrix_C(df):
    subset = df[df["matrix"] == "Bandwidth"]
    if subset.empty: return
    plot_grid(subset, "bw_mbps", "Bandwidth (Mbps)", 
              "Matrix C: Bandwidth Impact (Line Style = Buffer Size)", 
              "graph_C_bandwidth.png", plot_type="line", log_x=True)

def plot_matrix_C(df):
    subset = df[df["matrix"].str.contains("Bandwidth_Scaling")]
    
    if subset.empty: 
        print("Warning: Matrix C subset is empty. Check naming convention.")
        return

    plot_grid(subset, "bw_mbps", "Bandwidth (Mbps)", 
              "Matrix C: Bandwidth Scaling (Algo vs Throughput)", 
              "graph_C_bandwidth.png", plot_type="line", log_x=True)

def plot_matrix_D(df):
    # 1. Filter the data
    subset = df[df["matrix"].str.contains("BufferSize")].copy()
    
    if subset.empty: 
        return

    subset["bdp_mult"] = subset["bdp_mult"].astype(float)
    
    subset = subset.sort_values("bdp_mult")

    plot_grid(subset, "bdp_mult", "Buffer Size (x BDP)", 
              "Matrix D: Buffer Size Impact (Direct)", 
              "graph_D_buffer_direct.png", plot_type="line", log_x=True)
    
def plot_matrix_B_separated(df):
    """Generates one image per buffer multiplier for Loss tests."""
    data = df[df["matrix"].str.contains("Loss")]
    if data.empty: return

    multipliers = sorted(data['bdp_mult'].unique())
    for mult in multipliers:
        subset = data[data['bdp_mult'] == mult]
        if subset.empty: continue
        
        filename = f"graph_B_loss_{mult}xBDP.png"
        plot_grid(
            subset, "loss_label", "Loss Rate", 
            f"Matrix B: Loss Resilience (Buffer = {mult}x BDP)", 
            filename, plot_type="bar"
        )

def main():
    print("Loading data...")
    df = load_and_clean_data()
    if df.empty: return

    setup_plot_style()

    plot_matrix_A(df)           # Matrix A (Latency)
    plot_matrix_B_separated(df) # Matrix B (Loss - Separated)
    plot_matrix_C(df)           # Matrix C (Bandwidth)
    plot_matrix_D(df)           # Matrix D (Buffer Size)

    print("\nVisualization Complete.")

if __name__ == "__main__":
    main()