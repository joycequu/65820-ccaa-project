#!/bin/bash

set -e

CONTENT_DIR="$(pwd)/content"
CONF_FILE="$(pwd)/default.conf"

# Create content dir and test files if missing
mkdir -p "$CONTENT_DIR"

if [ ! -f "$CONTENT_DIR/10MB.bin" ]; then
    dd if=/dev/zero of="$CONTENT_DIR/10MB.bin" bs=1M count=10
    dd if=/dev/zero of="$CONTENT_DIR/100MB.bin" bs=1M count=100
    dd if=/dev/zero of="$CONTENT_DIR/1000MB.bin" bs=1M count=1000
fi

# Clean old containers
docker rm -f tcp_server_cubic >/dev/null 2>&1 || true
docker rm -f tcp_server_bbr >/dev/null 2>&1 || true

echo "[*] Launching CUBIC server..."
docker run --name tcp_server_cubic -d \
  --cap-add=NET_ADMIN \
  --sysctl net.ipv4.tcp_congestion_control=cubic \
  -p 8081:80 \
  -v "$CONTENT_DIR:/usr/share/nginx/html:ro" \
  -v "$CONF_FILE:/etc/nginx/conf.d/default.conf:ro" \
  nginx-tc

echo "[*] Launching BBR server..."
docker run --name tcp_server_bbr -d \
  --cap-add=NET_ADMIN \
  --sysctl net.ipv4.tcp_congestion_control=bbr \
  -p 8082:80 \
  -v "$CONTENT_DIR:/usr/share/nginx/html:ro" \
  -v "$CONF_FILE:/etc/nginx/conf.d/default.conf:ro" \
  nginx-tc

echo "[*] Servers started!"
echo "CUBIC → http://localhost:8081/"
echo "BBR   → http://localhost:8082/"
