FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Basic tools + Mininet dependencies
RUN apt-get update && apt-get install -y \
    mininet \
    iproute2 \
    iputils-ping \
    python3 \
    python3-pip \
    net-tools \
    openvswitch-switch \
    tcpdump \
    && rm -rf /var/lib/apt/lists/*

# Optional Python libs (extend as needed)
RUN pip3 install numpy

WORKDIR /app
COPY . /app

# Default: open a shell; experiments are run via `docker run ...`
CMD ["/bin/bash"]