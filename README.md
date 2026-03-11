# 6.5820 Congestion Control Algorithm Analysis Project

This project investigates the performance of modern congestion control algorithms under workloads representative of contemporary Internet applications. In particular, we examine how algorithms behave when supporting traffic patterns similar to video streaming, web browsing, and short-form media consumption, which often involve bursty downloads and frequent flow turnover.

Our evaluation focuses on two widely deployed congestion control algorithms: CUBIC, a loss-based algorithm commonly used in traditional TCP implementations, and BBR, a model-based algorithm that estimates network bandwidth and round-trip time to manage congestion. While CUBIC relies on packet loss as a signal of congestion and may lead to increased queueing delay, BBR attempts to maintain high throughput while minimizing latency by modeling network conditions.

Experiments are conducted in a controlled testbed where network characteristics such as bandwidth, round-trip time (RTT), packet loss, and buffer size are systematically varied. We measure performance across several metrics, including throughput, fairness between flows, and flow completion time.

The evaluation considers multiple usage scenarios, including single-flow transfers, multi-flow competition, and workloads that emulate real-world video consumption patterns. By analyzing algorithm behavior under these conditions, the project aims to better understand how congestion control design choices impact performance for modern Internet applications.
