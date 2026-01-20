#!/bin/bash

set -e

FILES=("10MB.bin" "100MB.bin" "1000MB.bin")
DELAYS=("0ms" "50ms" "100ms")
SERVER_PORTS=(8081 8082)
SERVER_NAMES=("cubic" "bbr")

RESULTS="results.csv"
echo "protocol,file,delay,download_speed_bytes_per_sec" > "$RESULTS"

for delay in "${DELAYS[@]}"; do
    echo "[*] Applying delay = $delay"
    # Apply artificial delay + bottleneck
    docker exec tcp_server_cubic tc qdisc replace dev eth0 root netem delay $delay rate 100mbit
    docker exec tcp_server_bbr   tc qdisc replace dev eth0 root netem delay $delay rate 100mbit

    for i in ${!SERVER_PORTS[@]}; do
        proto=${SERVER_NAMES[$i]}
        port=${SERVER_PORTS[$i]}

        for f in "${FILES[@]}"; do
            echo "[*] Testing $proto with $f at $delay"
            speed=$(curl -o /dev/null -s -w "%{speed_download}" "http://localhost:$port/$f")
            echo "$proto,$f,$delay,$speed" >> "$RESULTS"
        done
    done
done

echo "[*] Tests complete! Saved to $RESULTS."
