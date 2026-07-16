test05_step2h_v2.csv -- 2026-07-13

Smoke test (n=1) of baseline_4rsu_topo.py after it was rewritten to match
CDN_baseline's run_loop methodology (ported by a teammate/parallel session
on 2026-07-13): wall-clock-driven position, proactive handover trigger
(fires on entering the NEXT RSU's coverage radius, not just on nearest-RSU
change), one-association-attempt-per-tick state machine bounded by
HANDOVER_TIMEOUT_S=8.0, and new outage/cum_outage_s tracking (forces
bw=0, loss=100, rssi_src="none" on a verified outage tick).

CSV schema changed vs all earlier runs (this repo's runs/2026-07-02_* and
runs/2026-07-08_* folders): adds `outage,cum_outage_s` columns (16 total
vs the old 14). Position/timing model also changed (wall-clock vs the old
step-counter `x += SPEED_MPS*SAMPLE_DT`), so this run is NOT directly
comparable tick-for-tick with test04_step2h.csv -- it supersedes it as the
current baseline methodology going forward, but the old evolution story
(linear->step->step2->step2h) documented in runs/2026-07-08_bw-mapping-smoke-tests/
remains valid as historical methodology reference, just produced under the
prior (pre-outage-model) run_loop().

Command:
  sudo python3 baseline_4rsu_topo.py --headless --run-id test05_step2h_v2 \
    --bw-mapping step2h --out results_smoke/test05_step2h_v2.csv

Result: 754 rows, 3 handovers, 0 outage ticks, 0 stalls,
quality mix 360p=170/720p=285/1080p=299.
Per-tick Yin et al. QoE (Situation2-style, see Step2h_Analysis/plot_step2h_percolumn.py):
Net=2345.5 (avg 3.111/tick, n=754).
