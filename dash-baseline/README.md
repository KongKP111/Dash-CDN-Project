# SDN-VANET DASH ABR Baseline (single vehicle, single RSU)

Controlled baseline that streams **real** Big Buck Bunny over a 3-rung DASH
ladder while one vehicle drives past one RSU, and records how the player adapts.
Produces the quality "staircase", RSSI, packet loss, and rebuffering — single
run and aggregated over N runs with mean +/- spread.

## What is REAL vs IMPOSED (read this before the viva)
| Quantity | Source |
|---|---|
| RSSI | **Real** -- Mininet-WiFi log-distance propagation (`iw` per second) |
| Rendition / quality switching | **Real** -- VLC's own ABR; read from the server access log (which `chunk-streamN` is fetched) |
| Packet loss | **Real** -- protected ICMP probe = pure wireless reliability (Way 1) |
| Rebuffering / stalls | **Estimated** from real segment fetches via a playback-buffer model |
| Available bandwidth | **Imposed** stimulus via `tc` (a documented RSSI->BW map, NOT an 802.11p capacity claim) |

See `METHODOLOGY.md` for paper-ready wording and the justification.

## Repo layout
| file | role |
|---|---|
| `config.py` | **edit this on a new machine** (username, paths) |
| `baseline_model.py` | scenario constants, RSSI model, imposed-bandwidth profile, QoE |
| `baseline_topo.py` | the experiment (Mininet-WiFi, VLC, tc, metric collection) |
| `run_baseline_multi.sh` | run N times headless, one CSV per run |
| `plot_run.py` | 4-panel plot of one run |
| `aggregate_runs.py` | combine N runs -> mean +/- std / 95% CI + `aggregate.png` |
| `baseline_preview.py` | pure-python preview (no sudo) to sanity-check the curve |

## Prerequisites (lab PC)
- Mininet-WiFi installed for **python3.12** (`python3.12 -c "import mn_wifi"` works)
- **VLC from apt, not snap** (`sudo apt install -y vlc`; snap VLC fails inside the netns)
- `ffmpeg` (for the one-time content encode)
- `numpy`, `matplotlib` for the plots: `pip install -r requirements.txt`

## One-time setup
1. **Encode the 3-rung content** into `~/sdn-vanet-project/bbb_3rung/`:
   ```bash
   cd ~/sdn-vanet-project
   ffmpeg -y -i bbb_sunflower_1080p_30fps_normal.mp4 \
     -map 0:v -map 0:v -map 0:v -an \
     -c:v libx264 -preset veryfast -profile:v main -pix_fmt yuv420p \
     -g 120 -keyint_min 120 -sc_threshold 0 \
     -b:v:0 1000k -maxrate:v:0 1100k -bufsize:v:0 2000k -s:v:0 640x360 \
     -b:v:1 2500k -maxrate:v:1 2750k -bufsize:v:1 5000k -s:v:1 1280x720 \
     -b:v:2 5000k -maxrate:v:2 5500k -bufsize:v:2 10000k -s:v:2 1920x1080 \
     -use_template 1 -use_timeline 1 -seg_duration 4 \
     -adaptation_sets "id=0,streams=v" -f dash bbb_3rung/index.mpd
   ```
2. **Edit `config.py`** -- set `DEFAULT_USER` and `HOME` for the machine.

## Run -- single (with popups, good for a demo)
```bash
cd ~/sdn-cdn-dash-research/dash-baseline
sudo python3 baseline_topo.py            # pops topology + VLC, ~10 min
python3 plot_run.py baseline_run.csv     # -> baseline_run.png
```

## Run -- final dataset (10 runs, headless, unattended)
```bash
sudo ./run_baseline_multi.sh 10 results  # ~100 min
python3 aggregate_runs.py results        # -> aggregate.csv + aggregate.png
```

## CSV columns
`t, x, dist, rssi, rssi_src, bw_mbps, quality, quality_idx, seg, loss, stall, buffer_s`
- `rssi_src`: `live` (from the link) or `model` (fallback when association drops)
- `quality_idx`: 0/1/2 = 360p/720p/1080p, -1 = buffering
- `loss`: protected-probe ICMP loss (%) -- pure wireless
- `stall`: 1 if a rebuffering second; `buffer_s`: modelled buffer occupancy

## Troubleshooting (issues already hit & fixed)
- **`No module named mn_wifi`** -> run with `sudo python3.12` (module is in 3.12 dist-packages).
- **paths point to `/root/...`** -> `sudo` changes `$HOME`; `config.py` handles it (set `DEFAULT_USER`).
- **VLC `cannot find tracking cgroup`** -> that's snap VLC; install apt VLC instead.
- **VLC won't run as root** -> it is launched as your user via `sudo -u $USER`; no action needed.
- **`tc` device not found** -> confirm the iface with `--cli` then `server1 ip a` (should be `server1-eth0`).
- **window doesn't pop** -> `xhost +SI:localuser:root` (popup mode only; headless needs neither X nor xhost).
- **quality stuck at `buffering`** -> `cat /tmp/dashsrv.log | grep GET | head`; if empty, car1<->server1 connectivity is broken.
