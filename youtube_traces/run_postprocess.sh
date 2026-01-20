#!/bin/bash
set -e

if [ -z "$1" ]; then
    echo "Usage: ./run_postprocess.sh <experiment_name>"
    echo "Example: ./run_postprocess.sh my_test_1"
    exit 1
fi

EXP_NAME="$1"

echo "========================================================"
echo " PROCESSING EXPERIMENT: $EXP_NAME"
echo "========================================================"

# Directories
mkdir -p cleaned_pcap cleaned_csv schedules plots

# 1. Count Unique Video IDs from Stats CSV to determine N
STATS_FILE="youtube_stats/yt_${EXP_NAME}.csv"
RAW_PCAP="raw_pcap/${EXP_NAME}.pcap"

if [ ! -f "$STATS_FILE" ]; then
    echo "Error: Stats file $STATS_FILE not found."
    exit 1
fi

# Count unique video IDs (skipping header)
# We use awk to extract column 2 (video_id), sort, uniq, and count
UNIQUE_VIDS=$(awk -F, 'NR>1 {print $2}' "$STATS_FILE" | sort | uniq | wc -l | xargs)
TOP_K=$((UNIQUE_VIDS + 1))

echo ">>> Detected $UNIQUE_VIDS unique videos."
echo ">>> Will keep Top $TOP_K flows (Active + 1 potential Pre-fetch)."

# 2. Clean PCAP (Keep Top K flows)
python3 clean_pcap.py "$RAW_PCAP" "$EXP_NAME" --top_k "$TOP_K"

# 3. Convert Cleaned PCAP to CSV (Absolute Time)
CLEANED_PCAP="cleaned_pcap/${EXP_NAME}.pcap"
CLEANED_CSV="cleaned_csv/trace_${EXP_NAME}.csv"

echo ">>> Converting PCAP to CSV (tshark)..."
# Note: frame.time_epoch gives absolute unix time to match python stats
tshark -r "$CLEANED_PCAP" \
    -T fields \
    -e udp.stream \
    -e frame.time_epoch \
    -e frame.len \
    -E separator=, > "$CLEANED_CSV"

# 4. Generate ABR Schedule
SCHEDULE_JSON="simulation/schedules/abr_${EXP_NAME}.json"
echo ">>> Generating ABR Schedule..."
python3 generate_abr_schedule.py "$CLEANED_CSV" "$STATS_FILE" "$SCHEDULE_JSON"

# 5. Plot Results
PLOT_PNG="plots/${EXP_NAME}_schedule.png"
echo ">>> Generating Plot..."
python3 plot_schedule_seconds.py "$SCHEDULE_JSON" "$PLOT_PNG"

echo ""
echo "========================================================"
echo " DONE!"
echo " Schedule: $SCHEDULE_JSON"
echo " Plot:     $PLOT_PNG"
echo "========================================================"