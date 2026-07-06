# CDN Baseline

Single-vehicle CDN baseline — mirrors `dash-baseline` structure for fair comparison.

## Topology
- 4 APs on a straight 600m road (x = 0, 200, 400, 600 m)
- No SDN controller (standalone mode — same as DASH baseline)
- 1 vehicle starts at x=0, **stopped until `start_mobility()` is called**
- nginx edge cache: Video.mp4 (HIT) vs Video2.mp4 (MISS)
- Imposed bandwidth: same RSSI→BW mapping as DASH

## Situations
| Sit | Video | Cache | Description |
|-----|-------|-------|-------------|
| 1 | Video.mp4  | Always HIT  | Popular content — cached |
| 2 | Video2.mp4 | Always MISS | Unpopular — never cached |

## Metrics (per second)
- `latency_s` — HTTP request latency
- `speed_bps` — throughput
- `cache` — HIT / MISS
- `loss_pct` — ICMP packet loss (same probe as DASH)
- `qoe` — QoE score 1-5 (comparable to DASH MOS)
- `handover` — 1 if AP changed this step

## Quick Start

```bash
# Single run (with CLI — call py net.start_mobility() to start)
sudo python3 cdn_baseline_topo.py --sit 1 --speed 20 --round 1

# Single run (auto)
sudo python3 cdn_baseline_topo.py --sit 1 --speed 20 --round 1 --auto --no-gui

# All 60 runs
sudo bash run_baseline_multi.sh

# Aggregate + plots
python3 aggregate_runs.py results/cdn_baseline
```

## Output
```
results/cdn_baseline/
  sit1/speed20/cdn_baseline_sit1_spd20_r1/cdn_baseline_sit1_spd20_r1.csv
  ...
  summary/
    sit1/speed20/
      cdn_baseline_sit1_spd20_temporal.png
      cdn_baseline_sit1_spd20_correlation.png
      cdn_baseline_sit1_spd20_summary.json
```