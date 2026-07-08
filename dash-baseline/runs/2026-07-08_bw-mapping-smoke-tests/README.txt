4-RSU SDN baseline, 20 km/hr -- RSSI->bandwidth mapping smoke tests (n=1 each)
Collected: 2026-07-08

Single validation runs while developing the RSSI->bandwidth mapping, one per
step in the progression (see dash-baseline/baseline_model.py for the full
mapping code + comments, and TEAMMATE_SETUP.md at the repo root):

  test01_step.csv         -- mode=step   (equal-RSSI tiers)
                              1080p=16.1%, switches=20, QoE/seg=1.80
  test02_step_debug.csv   -- mode=step, --vlc-verbose (adaptive-demux debug
                              log analysis, same mapping as test01_step)
  test03_step2.csv        -- mode=step2  (equal-distance tiers, >=18s dwell)
                              1080p=36.2%, switches=22, QoE/seg=2.49
  test04_step2h.csv       -- mode=step2h (step2 + Schmitt-trigger hysteresis)
                              1080p=37.4%, switches=12, QoE/seg=2.93  <- FINAL,
                              chosen mapping going into the 10-run batch

  2026-07-08_summary_run04_vs_linear10run.png
      -- quality/bandwidth-vs-position + QoE decomposition (Yin et al. 2015),
         test04_step2h.csv vs the archived 2026-07-02 linear-baseline 10-run
         mean+-std (see ../2026-07-02_linear-baseline/)

QoE figures use the Yin et al. (2015) linear model, not the placeholder
qoe() in baseline_model.py. mu=1, q(R)=R in Mbps. Reported per-segment
(divide by K), not raw summed totals.

Next: 10-run batch of --bw-mapping step2h (not yet run as of this writing) --
    ./run_4rsu_multi.sh 10 results_4rsu_step2h step2h
