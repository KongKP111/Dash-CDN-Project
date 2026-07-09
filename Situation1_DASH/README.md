# Situation1_DASH -- Traffic Density (SDN+DASH arm)

Scalability test: how does DASH QoE degrade as vehicle density increases,
under a fixed-gap platoon (10 m, 20 km/h) sharing the same 802.11g AP
capacity? Three clean cases, run separately: **3 / 5 / 7 cars**.

Everything here is **new code for Situation 1 only**. The frozen Phase 1
baseline (`../Dash/`, `../dash-baseline/`) is reused by import and is never
modified -- see the header comments in `campus_config.py` for the exact
import list and why a few of the baseline's per-vehicle helpers had to be
reimplemented (not imported) for a multi-vehicle platoon.

## Status (2026-07-09)

All three cases have a clean, validated smoke test (1 run each). Results and
figures are in `graphs/` and `results_raw/`:

| cars | avg bitrate | avg throughput | stalls/vehicle | avg stall dur | avg rebuffer ratio |
|---|---|---|---|---|---|
| 3 (`smoke_3cars_v4`) | 4.03 Mbps | 6.24 Mbps | 0.33 | 0.14s | ~0% |
| 5 (`smoke_5cars_v1`) | 2.68 Mbps | 4.45 Mbps | 1.0 | 5.56s | 5.26% |
| 7 (`smoke_7cars_v4`) | 1.99 Mbps | 3.53 Mbps | 1.29 | 7.17s | 6.85% |

A clean, monotonic scalability curve -- bitrate/throughput fall and stalls
rise in lockstep with density, exactly the effect this scenario exists to
measure. Not yet batched (10 runs/case) -- see "Running" below.

## Files

| File | Purpose |
|---|---|
| `campus_config.py` | Shared config: RSU layout (same physical positions as the baseline, reassigned to 802.11g/2.4GHz channels 1/6/11), the real PSU-Phuket loop route (imported), `Step2HysteresisMapper` (imported), platoon constants (10 m gap, 20 km/h, arc-length position helpers) |
| `platoon_topology.py` | Mininet-WiFi topology builder: N vehicles, `PlatoonThrottleController` (hybrid step2h + AP contention-sharing bandwidth model), platoon mobility + zone-based handover, launches one DASH client per vehicle, per-vehicle network CSV logging |
| `run_case.sh` | Headless multi-run wrapper (same pattern as `dash-baseline/run_4rsu_multi.sh`); blocks/unblocks the real WiFi card automatically (see the hardware note below) |
| `visualize_platoon.py` | No-sudo preview: plots the platoon's positions at 6 points across the lap, prints the measured inter-vehicle gap (should read 10.0 m) -- sanity-check the mobility model before spending time on a real run |
| `plot_smoke_run_v2.py` | No-sudo: generates the paper-ready per-vehicle figure (RSSI / bitrate vs. allocated bandwidth / ICMP loss) from a run's saved CSVs -- see `graphs/` |
| `graphs/` | Output of `plot_smoke_run_v2.py` for the 3 validated smoke-test runs above |

## Bandwidth model (why it looks the way it does)

Each vehicle gets `min(step2h_rate, AP_capacity / n_active_at_that_RSU)`:
- `step2h_rate` -- the SAME RSSI-tiered, hysteresis-damped mapping as the
  frozen single-vehicle baseline (one stateful `Step2HysteresisMapper`
  instance per vehicle), so a lone vehicle at an RSU behaves identically to
  the Phase 1 baseline.
- `AP_capacity` -- 20 Mbps, the standard effective Layer-7 throughput for a
  54 Mbps 802.11g link after protocol overhead (encapsulation/ACK/IFS) --
  this is the pool that vehicles *currently associated to the same RSU*
  contend for. This is the new, Situation-1-specific part: as more vehicles
  share one RSU, each one's fair share shrinks, forcing the DASH ABR to
  downgrade even when RSSI alone wouldn't have forced it.

Implemented as a per-RSU Linux HTB tree (`{rsu}-wlan1` egress) with one
child class per vehicle, filtered by destination IP, recomputed every 0.5 s.

## Running

Prereqs: same as the DASH baseline (Mininet-WiFi, Docker for the Ryu
controller), `ffmpeg`-encoded 3-rung DASH content at
`/home/pc1/sdn-vanet-project/bbb_3rung` (**not** `bbb_ladder` -- that's a
different, 5-rung GPAC-encoded ladder with non-matching bitrates that the
frozen single-vehicle baseline happens to use; don't copy that path here).
`sudo` is required for every Mininet run -- run these from your own
terminal on pc1, not over a non-interactive SSH command.

**Hardware gotcha**: running 5+ simultaneous simulated stations on pc1 has
been observed to crash the real onboard WiFi card's firmware (Realtek
`rtw89_8852be`; `dmesg` shows "SER catches error" + a `mac80211` subsystem
restart), which breaks every simulated vehicle's association for the whole
run since Mininet-WiFi's virtual radios share that same `mac80211`
subsystem with the real card. Symptom: every vehicle stuck on RSU1,
`RSSI=-50dBm` (fallback default) and 100% ICMP loss for the entire run --
not a code bug. Fix: `sudo rfkill block wifi` before running (the real
card isn't needed at all), `sudo rfkill unblock wifi` after. `run_case.sh`
already does this automatically; if you run `platoon_topology.py` directly
(e.g. via `--cli`), do it by hand first.

```bash
cd ~/sdn-cdn-dash-research/Situation1_DASH

# 1) Smoke test each case first (1 run), confirm it looks right before batching
./run_case.sh 1 3   # Case 1: 3 cars
./run_case.sh 1 5   # Case 2: 5 cars
./run_case.sh 1 7   # Case 3: 7 cars

# 2) Once a case's smoke test looks right, batch it (e.g. 10 runs)
./run_case.sh 10 3 results_3cars
```

Or run one manually with the CLI for debugging (remember `sudo rfkill
block wifi` first if running 5+ cars):
```bash
sudo python3 platoon_topology.py --cars 3 --cli
```

After a run, plot it (copy the CSVs out of the root-owned `results_raw/`
first since it's written by the sudo mininet process):
```bash
mkdir -p /tmp/plot_data && cp results_raw/<run_id>/*.csv /tmp/plot_data/
python3 plot_smoke_run_v2.py <run_id> --dir /tmp/plot_data --cars <N>
# -> graphs/<run_id>_detailed_plot.png
```

## Output

Per case/run, under `results_raw/<run_id>/`:
- `<run_id>_carN_segments.csv` -- per-segment quality/bitrate/stall (dash_client.py, unmodified)
- `<run_id>_carN_summary.json` -- per-vehicle session summary (QoE inputs: avg bitrate, stalls, switches)
- `<run_id>_carN_network.csv` -- per-vehicle `t, x, y, rsu, rssi_dbm, allocated_bw_mbps, icmp_loss_pct`

QoE must be computed with the same **Yin et al. (2015)** linear model as the
rest of this project (see `TEAMMATE_SETUP.md`), reported **per segment**
(divide by K) so the 3/5/7-car cases are comparable to each other and to the
SDN+CDN arm.
