FROM ubuntu:22.04

# Install necessary tools
RUN apt-get update && apt-get install -y \
    nginx \
    curl \
    iproute2 \
    iperf3 \
    python3 \
    coreutils \
    && rm -rf /var/lib/apt/lists/*

# Create a 100MB test file for download
# RUN dd if=/dev/zero of=/var/www/html/testfile.bin bs=1M count=1024

# Nginx configuration to ensure no GZIP (we want raw transfer speed)
RUN echo "daemon off;" >> /etc/nginx/nginx.conf
RUN sed -i 's/gzip on;/gzip off;/g' /etc/nginx/nginx.conf

# Expose HTTP port
EXPOSE 80

# Default command
CMD ["nginx"]
