#!/usr/bin/env python3
"""
plot_results.py

Simple matplotlib plots for the multiflow results CSV/JSON.
"""

import json
import csv
import os
import matplotlib.pyplot as plt

RESULTS_DIR = "multiflow_results"
CSV_PATH = os.path.join(RESULTS_DIR, "results_multiflow.csv")
JSON_PATH = os.path.join(RESULTS_DIR, "results_multiflow.json")
OUT_PNG = os.path.join(RESULTS_DIR, "throughput_bar.png")
OUT_FAIR = os.path.join(RESULTS_DIR, "fairness_summary.png")

def plot_throughputs(csv_path=CSV_PATH):
    # Read CSV
    rows = []
    with open(csv_path, newline='') as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    # Group by scenario
    scenarios = {}
    for r in rows:
        sc = r['scenario']
        scenarios.setdefault(sc, []).append(float(r.get('large_throughput_mbps') or 0.0))
    # Create bar chart per scenario (avg per-flow)
    labels = []
    values = []
    for sc, ths in scenarios.items():
        labels.append(sc)
        values.append(sum(ths)/len(ths) if ths else 0.0)

    plt.figure(figsize=(8,4))
    plt.bar(labels, values)
    plt.ylabel("Avg per-flow throughput (Mbps)")
    plt.title("Average per-flow throughput by scenario")
    plt.xticks(rotation=30, ha='right')
    plt.tight_layout()
    plt.savefig(OUT_PNG)
    print("Saved", OUT_PNG)

def plot_fairness(json_path=JSON_PATH):
    with open(json_path) as f:
        data = json.load(f)
    scenarios = [d['scenario'] for d in data]
    fairness = [d.get('fairness') or 0.0 for d in data]
    plt.figure(figsize=(8,3))
    plt.plot(scenarios, fairness, marker='o')
    plt.ylim(0,1.05)
    plt.ylabel("Jain's Fairness")
    plt.title("Fairness across scenarios")
    plt.xticks(rotation=30, ha='right')
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(OUT_FAIR)
    print("Saved", OUT_FAIR)

if __name__ == "__main__":
    plot_throughputs()
    plot_fairness()
