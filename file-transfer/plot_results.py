import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("results.csv")

# Convert speeds to Mbps
df["speed_mbps"] = df["download_speed_bytes_per_sec"] * 8 / 1e6

# Plot 1: Throughput vs delay
for fname in df["file"].unique():
    subset = df[df["file"] == fname]
    plt.figure()
    for proto in ["cubic", "bbr"]:
        s = subset[subset["protocol"] == proto]
        plt.plot(s["delay"], s["speed_mbps"], marker="o", label=proto)
    plt.title(f"Throughput vs Delay ({fname})")
    plt.ylabel("Mbps")
    plt.xlabel("Delay")
    plt.legend()
    plt.grid()
    plt.tight_layout()
    plt.savefig(f"plot_delay_{fname}.png")

# Plot 2: Throughput vs File Size at each delay
for d in df["delay"].unique():
    subset = df[df["delay"] == d]
    plt.figure()
    for proto in ["cubic", "bbr"]:
        s = subset[subset["protocol"] == proto]
        plt.plot(s["file"], s["speed_mbps"], marker="o", label=proto)
    plt.title(f"Throughput vs File Size (delay={d})")
    plt.ylabel("Mbps")
    plt.xlabel("File Size")
    plt.legend()
    plt.grid()
    plt.tight_layout()
    plt.savefig(f"plot_filesize_{d}.png")

# Plot 3: Overall average
avg = df.groupby("protocol")["speed_mbps"].mean()
plt.figure()
avg.plot(kind="bar")
plt.title("Overall Average Throughput")
plt.ylabel("Mbps")
plt.tight_layout()
plt.savefig("plot_overall.png")
