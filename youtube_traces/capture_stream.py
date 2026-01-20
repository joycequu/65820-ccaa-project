import subprocess
import time
import re
import csv
import os
import sys
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# --- CONFIGURATION ---
INTERFACE = "en0"  # CHECK THIS: 'en0' for Mac, 'wlan0' for Linux
POLL_INTERVAL = 0.1 

def setup_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-infobars")
    # options.add_argument("--incognito")
    # options.add_argument("--autoplay-policy=no-user-gesture-required")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

def parse_stats_panel(text):
    stats = {
        "epoch_time": time.time(), # ABSOLUTE TIME for Syncing
        "buffer_health_sec": None,
        "network_activity_kb": None,
        "video_id": None
    }

    # Video ID
    vid_match = re.search(r"Video ID.*?sCPN\s+([^\s/]+)", text, re.IGNORECASE | re.DOTALL)
    if vid_match:
        stats["video_id"] = vid_match.group(1)

    # Buffer Health
    buffer_match = re.search(r"Buffer Health.*?([\d\.]+)\s*s", text, re.IGNORECASE | re.DOTALL)
    if buffer_match:
        stats["buffer_health_sec"] = float(buffer_match.group(1))

    # Network Activity
    net_match = re.search(r"Network Activity.*?([\d\.]+)\s*KB", text, re.IGNORECASE | re.DOTALL)
    if net_match:
        stats["network_activity_kb"] = float(net_match.group(1))

    return stats

def run_experiment(target_url, experiment_name):
    # Setup Directories
    os.makedirs("raw_pcap", exist_ok=True)
    os.makedirs("youtube_stats", exist_ok=True)

    output_pcap = f"raw_pcap/{experiment_name}.pcap"
    output_csv = f"youtube_stats/yt_{experiment_name}.csv"

    print(f"--- STARTING EXPERIMENT: {experiment_name} ---")
    print(f"Target URL: {target_url}")

    # 1. Start TCPDUMP (Must start first to catch QUIC Handshake)
    print(f">>> Launching sudo tcpdump on {INTERFACE}...")
    cmd = ["sudo", "tcpdump", "-i", INTERFACE, "-w", output_pcap, "udp", "or", "tcp"]
    tcpdump_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Wait for tcpdump to initialize hooks
    time.sleep(1.5)

    driver = None
    try:
        print(">>> Opening Chrome...")
        driver = setup_driver()
        driver.get(target_url)

        # Force play
        try:
            driver.execute_script("document.querySelector('video').play()")
        except: pass

        print("\n" + "="*60)
        print(" ACTION REQUIRED:")
        print("  Right-click video -> Select 'Stats for nerds'.")
        print("  Recording starts AUTOMATICALLY when panel appears.")
        print("  Press CTRL+C to stop.")
        print("="*60 + "\n")

        # Prepare CSV
        file_exists = os.path.isfile(output_csv)
        with open(output_csv, mode="a", newline="") as f:
            fieldnames = ["epoch_time", "video_id", "buffer_health_sec", "network_activity_kb"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()

            while True:
                try:
                    stats_panel = driver.find_element(By.CSS_SELECTOR, ".html5-video-info-panel-content")

                    if stats_panel.is_displayed():
                        raw = stats_panel.text
                        parsed = parse_stats_panel(raw)

                        if parsed["buffer_health_sec"] is not None:
                            writer.writerow(parsed)
                            f.flush()
                            print(f"\r[REC] Buffer: {parsed['buffer_health_sec']}s | Net: {parsed['network_activity_kb']} KB    ", end="")
                        else:
                            print("\r[Parsing...] ", end="")
                    else:
                        print("\r[Enable Stats for Nerds] ", end="")

                except Exception:
                    print("\r[Waiting for Stats Panel] ", end="")

                time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n\n>>> CTRL+C received. Stopping...")

    finally:
        if tcpdump_proc:
            print(f">>> Killing tcpdump (PID {tcpdump_proc.pid})...")
            subprocess.run(["sudo", "kill", str(tcpdump_proc.pid)])
        if driver:
            driver.quit()
        print(f">>> DONE. Saved:\n    PCAP: {output_pcap}\n    CSV:  {output_csv}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 capture_stream.py <YOUTUBE_URL> <EXPERIMENT_NAME>")
    else:
        run_experiment(sys.argv[1], sys.argv[2])
