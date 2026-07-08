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

## SDN baseline: real VLC playback (buffer/stall telemetry)

`cdn_baseline_topo_sdn.py` additionally runs real VLC playback on car1
against the same edge URLs used by the curl-based HIT/MISS probe above —
this doesn't replace that probe, it runs alongside it to capture genuine
buffering/stall behavior. On handover, playback position is preserved
(pause → switch edge → seek back), not restarted from 0. Sit 1 (popular
content) is now pre-warmed at all 4 edges before the drive starts, not just
edge1. Output: `vlc_playback_<run_id>.csv` (periodic position/buffer
samples) and `vlc_events_<run_id>.csv` (stall/handover-reload events) next
to the usual per-run CSV. This is SDN-only — the no-SDN baseline
(`cdn_baseline_topo.py`) is unaffected.

**Dependency**: `pip3 install python-vlc` (or `sudo apt install
python3-vlc`) — must be visible to the `python3` that `sudo` resolves to,
since the topology script runs as root. `run_baseline_sdn.sh` checks for
this and fails fast if missing.

**Watching it live**: playback is headless by default (no display needed —
this is what every batch/automated run uses). To actually see the video
window while it streams, run `cdn_baseline_topo_sdn.py` directly (not
through `run_baseline_sdn.sh`, which never enables this) with `--vlc-show`:
```bash
xhost +si:localuser:root   # one-time per session — lets root's VLC reach your X display
sudo python3 cdn_baseline_topo_sdn.py --sit 1 --speed 20 --round 1 --auto --no-gui --vlc-show
```

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