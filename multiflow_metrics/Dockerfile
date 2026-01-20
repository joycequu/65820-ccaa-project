FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    nginx \
    curl \
    iproute2 \
    iperf3 \
    python3 \
    coreutils \
    && rm -rf /var/lib/apt/lists/*

RUN dd if=/dev/urandom of=/var/www/html/testfile.bin bs=1M count=1024

RUN echo "daemon off;" >> /etc/nginx/nginx.conf
RUN sed -i 's/gzip on;/gzip off;/g' /etc/nginx/nginx.conf

EXPOSE 80

CMD ["nginx"]
