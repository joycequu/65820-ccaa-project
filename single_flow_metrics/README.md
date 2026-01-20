`docker build -t tcp-sim-node .`

## Set up docker (google cloud VM)
```
# 1. Update and install system tools
sudo apt-get update
sudo apt-get install -y docker.io python3 python3-pip git

# 2. Add your user to the docker group (so you don't need 'sudo' for python scripts)
sudo usermod -aG docker $USER

# 3. CRITICAL: Log out and log back in for group changes to take effect!
# You can simulate this by running:
newgrp docker

# 4. Install Python libraries
pip3 install docker pandas matplotlib seaborn
```

## Prerequisites
- Docker installed and running
- Python 3.x installed
- Python libraries: `pip install docker pandas matplotlib seaborn`
- Host kernel support: `sysctl net.ipv4.tcp_available_congestion_control` --> `cubic reno bbr`


## How to run
1. Start the simulation: `python network_sim.py`
2. Visualize results: `python plot_results.py`

