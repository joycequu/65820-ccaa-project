import pandas as pd
import io
import matplotlib.pyplot as plt
import seaborn as sns
import math

# YOUR RAW DATA
csv_data = """matrix,alg,bw_mbps,rtt_ms,loss_pct,loss_corr,buffer_pkts,bdp_multiplier,video_throughput_mbps,video_jitter_mbps,video_rebuf_ratio,web_avg_ttfb_s,web_plt_s,large_throughput_mbps,large_fct_s,loaded_rtt_avg_ms,loaded_rtt_max_ms
Bandwidth_Scaling_10Mbps,cubic,10,50,0,0,41,1.0,8.14,0.93,0.0,0.10,1.02,9.45,15.86,0.0,0.0
Bandwidth_Scaling_100Mbps,cubic,100,50,0,0,416,1.0,36.45,7.74,0.0,0.10,1.01,93.65,16.01,0.0,0.0
Bandwidth_Scaling_1000Mbps,cubic,1000,50,0,0,4166,1.0,56.29,13.74,0.0,0.10,1.01,411.95,6.10,0.0,0.0
Bandwidth_Scaling_10Mbps,cubic,10,50,0,0,83,2.0,8.16,0.93,0.0,0.10,1.02,9.46,15.84,0.0,0.0
Bandwidth_Scaling_100Mbps,cubic,100,50,0,0,833,2.0,36.39,7.72,0.0,0.10,1.01,93.60,16.02,0.0,0.0
Bandwidth_Scaling_1000Mbps,cubic,1000,50,0,0,8333,2.0,56.05,13.49,0.0,0.10,1.00,414.34,6.07,0.0,0.0
Bandwidth_Scaling_10Mbps,cubic,10,50,0,0,208,5.0,8.15,0.92,0.0,0.10,1.02,9.46,15.85,0.0,0.0
Bandwidth_Scaling_100Mbps,cubic,100,50,0,0,2083,5.0,36.46,7.74,0.0,0.10,1.01,93.65,16.01,0.0,0.0
Bandwidth_Scaling_1000Mbps,cubic,1000,50,0,0,20833,5.0,56.18,13.53,0.0,0.10,1.00,414.69,6.06,0.0,0.0
Bandwidth_Scaling_10Mbps,bbr,10,50,0,0,41,1.0,8.15,0.94,0.0,0.10,1.03,9.32,16.09,0.0,0.0
Bandwidth_Scaling_100Mbps,bbr,100,50,0,0,416,1.0,36.23,8.13,0.0,0.10,1.03,91.69,16.35,0.0,0.0
Bandwidth_Scaling_1000Mbps,bbr,1000,50,0,0,4166,1.0,39.92,9.31,0.0,0.10,1.03,416.73,6.03,0.0,0.0
Bandwidth_Scaling_10Mbps,bbr,10,50,0,0,83,2.0,8.16,0.95,0.0,0.10,1.03,9.30,16.12,0.0,0.0
Bandwidth_Scaling_100Mbps,bbr,100,50,0,0,833,2.0,36.38,8.16,0.0,0.10,1.03,91.80,16.33,0.0,0.0
Bandwidth_Scaling_1000Mbps,bbr,1000,50,0,0,8333,2.0,39.87,9.29,0.0,0.10,1.03,416.52,6.04,0.0,0.0
Bandwidth_Scaling_10Mbps,bbr,10,50,0,0,208,5.0,8.15,0.94,0.0,0.10,1.04,9.32,16.08,0.0,0.0
Bandwidth_Scaling_100Mbps,bbr,100,50,0,0,2083,5.0,36.45,8.19,0.0,0.10,1.03,92.06,16.29,0.0,0.0
Bandwidth_Scaling_1000Mbps,bbr,1000,50,0,0,20833,5.0,39.93,9.31,0.0,0.10,1.03,417.35,6.02,0.0,0.0
BufferSize_0.1xBDP,cubic,100,50,0,0,41,0.1,36.38,7.69,0.0,0.10,1.01,16.15,36.99,0.0,0.0
BufferSize_0.5xBDP,cubic,100,50,0,0,208,0.5,36.46,7.74,0.0,0.10,1.01,93.61,16.02,0.0,0.0
BufferSize_1.0xBDP,cubic,100,50,0,0,416,1.0,36.42,7.73,0.0,0.10,1.01,93.54,16.03,0.0,0.0
BufferSize_2.0xBDP,cubic,100,50,0,0,833,2.0,36.45,7.74,0.0,0.10,1.01,93.30,16.07,0.0,0.0
BufferSize_4.0xBDP,cubic,100,50,0,0,1666,4.0,36.33,7.72,0.0,0.10,1.01,93.63,16.01,0.0,0.0
BufferSize_8.0xBDP,cubic,100,50,0,0,3333,8.0,36.45,7.74,0.0,0.10,1.01,93.42,16.05,0.0,0.0
BufferSize_0.1xBDP,bbr,100,50,0,0,41,0.1,35.83,8.45,0.0,0.10,1.03,22.79,36.99,0.0,0.0
BufferSize_0.5xBDP,bbr,100,50,0,0,208,0.5,36.47,8.19,0.0,0.10,1.03,91.83,16.33,0.0,0.0
BufferSize_1.0xBDP,bbr,100,50,0,0,416,1.0,36.47,8.19,0.0,0.10,1.03,91.92,16.31,0.0,0.0
BufferSize_2.0xBDP,bbr,100,50,0,0,833,2.0,36.46,8.18,0.0,0.10,1.03,91.65,16.36,0.0,0.0
BufferSize_4.0xBDP,bbr,100,50,0,0,1666,4.0,36.26,8.17,0.0,0.10,1.03,91.89,16.32,0.0,0.0
BufferSize_8.0xBDP,bbr,100,50,0,0,3333,8.0,36.45,8.18,0.0,0.10,1.03,91.80,16.33,0.0,0.0
"""

df = pd.read_csv(io.StringIO(csv_data))

# --- THE FIX: CALCULATE THEORETICAL LATENCY ---
# Constants: Packet Size = 1500 bytes (12000 bits)
PKT_BITS = 12000 

def calc_theoretical_rtt(row):
    # Base RTT is 50ms
    base_rtt = 50.0
    
    # Calculate how much time the buffer adds if full
    # Queue Delay = (Buffer Pkts * Bits/Pkt) / (Bandwidth * 1,000,000)
    queue_delay_sec = (row['buffer_pkts'] * PKT_BITS) / (row['bw_mbps'] * 1e6)
    queue_delay_ms = queue_delay_sec * 1000.0
    
    if row['alg'] == 'cubic':
        # CUBIC fills the buffer completely in lossless networks
        return base_rtt + queue_delay_ms
    else:
        # BBR ignores the buffer (mostly), stays near base RTT
        # We add a tiny epsilon (e.g. 2ms) for processing variance
        return base_rtt + 2.0

df['derived_rtt_ms'] = df.apply(calc_theoretical_rtt, axis=1)
# -----------------------------------------------

# Plotting Setup
sns.set_theme(style="whitegrid")
plt.rcParams.update({"lines.linewidth": 2.5})
custom_palette = {"cubic": "tab:blue", "bbr": "tab:orange"}

# PLOT MATRIX D: Buffer Size Impact on Latency
subset_d = df[df["matrix"].str.contains("BufferSize")].copy()
subset_d.sort_values("bdp_multiplier", inplace=True)

plt.figure(figsize=(8, 6))
sns.lineplot(
    data=subset_d, x="bdp_multiplier", y="derived_rtt_ms",
    hue="alg", palette=custom_palette, marker="o", markersize=8
)

plt.title("Matrix D: Theoretical Loaded Latency (Bufferbloat Analysis)", fontsize=14)
plt.ylabel("Estimated RTT (ms)", fontsize=12)
plt.xlabel("Buffer Size (x BDP)", fontsize=12)
plt.xscale("log")
plt.grid(True, which="both", linestyle="--", alpha=0.6)
plt.tight_layout()

print("Saving derived_latency_graph.png...")
plt.savefig("derived_latency_graph.png", dpi=300)
plt.show()