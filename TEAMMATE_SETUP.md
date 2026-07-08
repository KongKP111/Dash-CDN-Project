# Setup for the SDN+CDN arm (teammate handoff)

This repo now has a working **SDN+DASH** baseline (`dash-baseline/`): 4 RSUs on a
straight road, one vehicle streaming MPEG-DASH via VLC, handover managed by a
custom Ryu controller. This doc is for setting up the **SDN+CDN** arm
(`CDN/`) the same way, so the two are a fair, controlled comparison — same
mobility, same imposed-bandwidth stimulus, same client, only the delivery
architecture (single origin server + SDN handover vs. CDN edge caching + SDN
handover) differs.

## 1. Environment setup (same as the DASH side)

```bash
git clone https://github.com/KongKP111/Dash-CDN-Project.git ~/sdn-cdn-dash-research
cd ~/sdn-cdn-dash-research
```

Do **not** reuse the origin URL with an embedded token from anyone else's
clone — set up your own GitHub auth (SSH key or your own PAT).

Prereqs (see `dash-baseline/README.md` for the full list):
- Mininet-WiFi for python3.12 (`python3.12 -c "import mn_wifi"` works)
- VLC from apt, **not snap** (`sudo apt install -y vlc xvfb`)
- Docker, your user in the `docker` group (`groups | grep docker`)
- `ffmpeg`, `numpy`, `matplotlib`

Edit `dash-baseline/config.py` — `DEFAULT_USER` / `HOME` — for your machine.
This file is shared/reused as-is; don't duplicate it into `CDN/`.

Encode the content once (same command as the DASH side, so both arms stream
the identical video + bitrate ladder — see "fairness" below):
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

## 2. Use the SAME imposed-bandwidth model as the DASH side (important for fairness)

This week we tried 4 versions of the RSSI→bandwidth mapping on the DASH arm
and landed on **`step2h`** as the best (highest QoE, fewest quality
switches — see `dash-baseline/baseline_model.py` for the full story in
comments). **Reuse it, don't reinvent it** — that's what makes DASH-vs-CDN a
fair comparison instead of two differently-tuned experiments.

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dash-baseline"))
import baseline_model as M

# stateful -- instantiate ONCE per run, call .update(rssi) every sample
bw_mapper = M.Step2HysteresisMapper()

# in your per-sample loop, wherever you currently compute imposed bandwidth:
bw_mbps = bw_mapper.update(current_rssi_dbm)
set_tc(edge_or_origin_host, iface, bw_mbps)   # same tc HTB pattern you already have
```

`CDN/topology/real_campus_live.py` currently has its own independent
bandwidth-throttle scheduler (timed changes, not RSSI-driven) — swap that for
`Step2HysteresisMapper` so both arms react to the *same* signal (RSSI at the
vehicle's actual position), not two different stimuli.

## 3. Use VLC as the client, same launch pattern

```bash
sudo -u $USER env HOME=$HOME \
  xvfb-run -a --server-args='-screen 0 1280x1024x24 -ac -extension GLX' \
  vlc -I dummy --no-audio --avcodec-hw=none --vout=x11 --play-and-exit \
  --adaptive-logic=rate --network-caching=3000 \
  'http://<server_ip>:<port>/index.mpd' >/tmp/vlc.log 2>&1 &
```
(`--vout=x11` not fully headless — true headless leaves VLC's ABR stuck at
360p, see `dash-baseline/README.md`.) Parse quality/segment from the HTTP
access log the same way `baseline_topo.py`'s `QualityPoller` does — reuse
that class if your CDN script's HTTP server can produce a similar log.

## 4. Fairness checklist before comparing DASH vs CDN numbers

From what we worked out this week — all four must hold or the comparison
isn't valid, no matter how correct the QoE formula is:
- [ ] Same content + same bitrate ladder (360p/720p/1080p @ 1.0/2.5/5.0 Mbps)
- [ ] Same `μ` (switch-penalty weight) and same utility function `q(R)` in the QoE formula
- [ ] Same mobility model (road length, vehicle speed, RSU/edge-node spacing) driving the *same* imposed-bandwidth stimulus (`step2h`, see above)
- [ ] Report QoE **per segment** (normalize by K), not raw summed totals, since run lengths must be compared like-for-like

## 5. QoE formula (for the paper)

Linear QoE model (Yin et al., SIGCOMM 2015) — not the toy 1-5 formula in
`baseline_model.py:qoe()`, that one's a placeholder, don't cite it:

```
QoE = sum(q(R_k)) - mu * sum(|q(R_k+1) - q(R_k)|) - sum(T_k)
```
`q(R_k)` = bitrate of the segment k rendition (Mbps), `mu=1` (standard
default), `T_k` = rebuffer seconds before segment k. Reference:

> Yin, X., Jindal, A., Sekar, V., & Sinopoli, B. (2015). *A Control-Theoretic
> Approach for Dynamic Adaptive Video Streaming over HTTP*. SIGCOMM '15,
> pp. 325–338. https://doi.org/10.1145/2785956.2787486

## 6. What's already there for you in `CDN/`

- `CDN/topology/real_campus_live.py` — main topology (needs the bandwidth-model swap above)
- `CDN/topology/mobility_positions.py` — mobility positions
- `CDN/edge/nginx_edge.conf`, `CDN/origin/nginx_origin.conf` — edge/origin server configs
- `CDN/CDN_run_experiment.sh`, `CDN/CDN_run_all.sh` — run scripts
- `Ryu-SDN-Controller/cdn_switch_13.py` — your own controller variant (separate from `sdn_controller.py`, which the DASH arm uses)

Questions on any of the above → ask in the group chat, or point Claude at
this file for context.
