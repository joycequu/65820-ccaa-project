import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import glob
import os

# --- Configuration ---
RESULTS_CSV = "results_2.csv"
OUTPUT_DIR = "plots"

# Set plot style
sns.set_theme(style="whitegrid")
plt.rcParams.update({'figure.autolayout': True})

def ensure_output_dir():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

def plot_aggregate_metrics():
    """
    Reads results_2.csv and plots bar charts for key metrics.
    """
    if not os.path.exists(RESULTS_CSV):
        print(f"File {RESULTS_CSV} not found. Skipping aggregate plots.")
        return

    df = pd.read_csv(RESULTS_CSV)
    
    # Clean up scenario names (remove path and extension for cleaner labels)
    df['Scenario Name'] = df['scenario'].apply(lambda x: os.path.basename(x).replace('.json:long', '').replace('.json:short', '').replace('.json', ''))
    
    # Define metrics to plot
    metrics = [
        ("avg_throughput_mbps", "Avg Throughput (Mbps)"),
        ("jitter_mbps", "Jitter (Mbps)"),
        ("rebuffering_ratio", "Rebuffering Ratio"),
        ("fairness_index", "Fairness Index (Jain's)")
    ]

    # Create a figure with 2x2 subplots
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes = axes.flatten()

    for i, (col, title) in enumerate(metrics):
        if col not in df.columns:
            continue
            
        sns.barplot(
            data=df,
            x="Scenario Name",
            y=col,
            hue="algorithm",
            ax=axes[i],
            palette="viridis"
        )
        axes[i].set_title(title, fontsize=14, fontweight='bold')
        axes[i].set_xlabel("")
        axes[i].set_ylabel("")
        axes[i].tick_params(axis='x', rotation=45)
        axes[i].legend(title="Algorithm")

    fig.suptitle("CUBIC vs BBR Performance Comparison", fontsize=16)
    plt.tight_layout()
    
    out_file = os.path.join(OUTPUT_DIR, "aggregate_metrics_comparison.png")
    plt.savefig(out_file, dpi=300)
    print(f"Saved aggregate plot to {out_file}")
    plt.close()

def plot_throughput_timeseries():
    """
    Finds matching throughput_*.csv files and plots time series comparisons.
    """
    # Find all throughput files
    files = glob.glob("throughput_*.csv")
    if not files:
        print("No throughput_*.csv files found. Skipping time-series plots.")
        return

    # Group files by trace name
    # Filename format: throughput_<trace_name>_<algo>.csv
    scenarios = {}
    for f in files:
        # crude parsing
        parts = f.replace("throughput_", "").replace(".csv", "").split("_")
        algo = parts[-1]
        trace_name = "_".join(parts[:-1])
        
        if trace_name not in scenarios:
            scenarios[trace_name] = {}
        scenarios[trace_name][algo] = f

    # Plot for each scenario
    for trace, algo_files in scenarios.items():
        plt.figure(figsize=(12, 6))
        
        has_data = False
        for algo, filepath in algo_files.items():
            try:
                df = pd.read_csv(filepath)
                if df.empty:
                    continue
                
                # Plot line
                plt.plot(df['time_sec'], df['throughput_mbps'], label=algo.upper(), linewidth=2, alpha=0.8)
                has_data = True
            except Exception as e:
                print(f"Error reading {filepath}: {e}")

        if has_data:
            plt.title(f"Throughput over Time: {trace}", fontsize=14)
            plt.ylabel("Throughput (Mbps)")
            plt.xlabel("Time (s)")
            plt.legend()
            plt.grid(True, linestyle='--', alpha=0.7)
            
            out_file = os.path.join(OUTPUT_DIR, f"timeseries_{trace}.png")
            plt.savefig(out_file, dpi=300)
            print(f"Saved time-series plot to {out_file}")
        
        plt.close()

if __name__ == "__main__":
    ensure_output_dir()
    print("Generating aggregate metrics plots...")
    plot_aggregate_metrics()
    print("\nGenerating time-series plots...")
    plot_throughput_timeseries()
    print("\nDone! Check the 'plots' directory.")