# TCP Multiflow Simulation

This repository contains a Docker-based simulation environment to study TCP congestion control fairness across multiple flows. It supports workloads for video streaming (ABR), web page load times (PLT), and large file downloads.

---

## Prerequisites

- Docker (20.10+ recommended)
- Python 3.10+
- Required Python packages: `docker`, `matplotlib`, `pandas`, `seaborn`

---

## 1. Build Docker Image

Build the server/client image if not already built:

```bash
docker build -t tcp-sim-node .
````

This image includes:

* Nginx (server)
* `curl` (client requests)
* `iproute2` (traffic control via `tc`)
* `iperf3`
* Python 3

---

## 2. Run Multiflow Simulation

Run the Python harness to execute all multiflow experiments:

```bash
python3 multiflow_sim.py
```

* The script will launch one server container and multiple client containers depending on the scenario.
* Scenarios include:

  * 3 flows with the same RTT and same CCA
  * 3 flows with same CCA but different RTTs
  * 1 CUBIC and 1 BBR flow competing
* Results are printed to the terminal and saved to:

```
multiflow_results/results_multiflow.json
multiflow_results/results_multiflow.csv
```

> Note: `sudo` may be required depending on your Docker setup.

---

## 3. Plot Results

Generate plots from the saved results:

```bash
python3 plot_results.py
```

* This script produces PNG figures in `multiflow_results/`
* Includes:

  * Average throughput per flow
  * Throughput fairness (Jain’s index)
  * Video ABR quality and rebuffering metrics

---

## 4. Notes

* Only **one server container** is used; multiple client containers simulate competing flows.
* Traffic shaping (bandwidth, RTT, loss, buffer size) is applied mainly on the **server** interface to emulate a shared bottleneck.
* Each client can have a different TCP congestion control algorithm (CUBIC or BBR) and per-flow RTT if desired.

---

## 5. Directory Structure

```
.
├── Dockerfile
├── multiflow_sim.py
├── plot_results.py
├── multiflow_results/
│   ├── results_multiflow.json
│   └── results_multiflow.csv
└── README.md
```

---

## 6. References

* BBR Paper: [Google BBR: Congestion-Based Congestion Control](https://research.google/pubs/pub44824/)
* ns-3 TCP Fairness Studies
