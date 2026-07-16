# Situation 2 (Mobility Speed) — CDN-side changes, for the DASH team

This documents every change made to build the CDN arm of Situation 2 (20 →
80/100/120 km/h through 4 APs, straight highway route), in the order they
happened, including two real bugs found along the way. Goal: let the DASH
side (`dash-baseline/baseline_4rsu_topo.py`) mirror the same fixes so a
DASH-vs-CDN comparison at highway speed is actually fair — right now it
would not be (see **Step 4**, the most important one).

Everything below lives in `CDN_SIT2/cdn_sdn_hight_speed.py`, a copy of
`CDN_baseline/cdn_baseline_topo_sdn.py` (not `dash-baseline`'s route — that
one's the campus loop for Situation 1/traffic-density, unrelated to this).

---

## Step 1 — Set up the vehicle for 80/100/120 km/h

`CDN_baseline/cdn_baseline_topo_sdn.py` already modeled a single vehicle
driving a straight route (`-300m → 1800m`) through 4 fixed APs
(`x = 0, 500, 1000, 1500`, 300m coverage radius each → 100m overlap zone
between neighbours) — copied it to `CDN_SIT2/cdn_sdn_hight_speed.py` and:

- `--speed` choices: `[20, 25, 30]` → `[20, 80, 100, 120]` (20 kept as the
  low-speed baseline point, per the original ask).
- Fixed import paths (`sys.path.insert` now points at `CDN_baseline/` since
  the file moved out of that directory) — not relevant to DASH.
- `--ryu-port` default 6653 → 6654, so it doesn't collide with
  `Situation1_DASH`'s Ryu docker container (`ryu-ctrl`, confirmed running
  persistently on 6653 via `docker ps`) if both are up at once.

**DASH side has no equivalent yet**: `dash-baseline/baseline_4rsu_topo.py`
(the straight-route, single-vehicle, 4-RSU DASH counterpart to
`cdn_baseline_topo_sdn.py`) has `SPEED_KMH = 20.0` **hardcoded** as a module
constant in `baseline_4rsu_model.py`, no `--speed` CLI flag at all. **Action:
add a `--speed` argument the same way**, threading it through to wherever
`SPEED_MPS` is derived, with the same `[20, 80, 100, 120]` choices so the
two arms sweep identical speed points.

---

## Step 2 — Diagnosed why the GUI/position looked jumpy

Both arms process each simulated "tick" sequentially and do real blocking
work (curl probes, `iw` commands) inside each one — the more that work
costs in real time, the choppier the position updates look. This part is
already common to both arms and not new to Situation 2; no action needed
here, just background for what follows.

---

## Step 3 — Ran 20 vs 80 km/h, found the headline result

Real WiFi (re)association takes a roughly **constant** amount of real time
regardless of vehicle speed (see Step 6 for the actual measured numbers),
but the time available to cross the 100m inter-AP overlap zone shrinks
proportionally with speed:

| speed | overlap dwell (100m / speed) |
|---|---|
| 20 km/h | 18.0s |
| 80 km/h | 4.5s |
| 100 km/h | 3.6s |
| 120 km/h | 3.0s |

At 20 km/h a slow handover barely dents that budget; at 80+ km/h it can
consume the entire window. This is the core research question — **DASH
needs the same speed sweep and the same overlap-zone math to be
comparable** (`dash-baseline/baseline_4rsu_model.py` already has the same
RSU spacing/coverage as CDN's `AP_POSITIONS`/`AP_COVERAGE`, per
`TEAMMATE_SETUP.md`'s fairness checklist, so the dwell-time numbers above
should already apply identically once DASH's speed is parameterized).

---

## Step 4 — THE key architectural fix: don't freeze position during handover (MOST IMPORTANT for DASH)

**Original CDN design (matching what DASH currently does — see below):**
the simulated "drive clock" was paused for the entire duration of a
handover — real time spent re-associating didn't count, so the vehicle's
logged position never advanced while a handover was in progress. This
**structurally guarantees a handover can never fail from running out of
road** — no matter how slow the real WiFi reconnection is or how fast the
vehicle "drives," the car is never actually moving while reconnecting, so
it always has unlimited time to finish. That's not realistic (a real car
doesn't stop and wait for WiFi), and it hides the exact failure mode this
whole experiment exists to study.

**Fixed by removing the pause** — the vehicle's logged position is now
always `START_X + (real_elapsed_wall_clock_seconds) * speed_mps`, so it
keeps advancing in real time even while a handover attempt is still in
progress. A handover that can't complete before the car leaves the AP's
range now shows up as a **real, measured outage** instead of being
invisible.

**⚠️ Checked DASH's `baseline_4rsu_topo.py::run_loop()` and it does neither
of the above — it does something different and arguably worse for this
specific experiment.** Its loop is:

```python
t = 0.0
x = M4.START_X
while x <= M4.END_X + 1e-9:
    car1.setPosition("%.1f,0,0" % x)
    ...
    if nearest != cur_rsu:
        ok = ensure_assoc(car1, aps[nearest])   # blocking, real time
        ...
    ...
    t += M4.SAMPLE_DT          # ALWAYS +1.0s, however long the tick took
    x += M4.SPEED_MPS * M4.SAMPLE_DT   # ALWAYS a fixed distance step
```

`t`/`x` advance by a **fixed synthetic amount every iteration**, completely
decoupled from how long that iteration's real handover work actually took.
This means DASH's own `x`/`t` values in its CSV **never reflect real
elapsed time at all** — a handover that takes 8 real seconds costs exactly
the same `t`/`x` increment (1.0s / one sample-step) as a handover that
takes 50ms. The wall-clock run just silently takes longer overall, but
nothing in the data shows it. Structurally this is even less able to
surface a real "ran out of road" failure than CDN's old frozen-position
design was — it can't show slow handovers **or** their consequences at
all.

**Action for DASH**: change `run_loop()` to derive `x`/`t` from actual
elapsed wall-clock time (`time.monotonic() - t_start`) the same way
`cdn_sdn_hight_speed.py::run_loop_sdn()` now does, instead of the fixed
`+= SAMPLE_DT` increment. Without this change, a DASH-vs-CDN comparison at
100/120 km/h is not apples-to-apples — CDN would be measuring real outage
risk while DASH's numbers structurally couldn't show the equivalent even
if DASH's real handover were just as slow.

---

## Step 5 — Added explicit outage tracking + a retry state machine

Once position could genuinely advance during a handover, two more pieces
were needed:

1. **Dynamic re-targeting with a hard timeout.** A handover retries against
   whichever AP is nearest the car's *current* position (re-checked every
   attempt — the car may have drifted to a different AP by the time attempt
   #5 runs), bounded by `--handover-timeout` (default 8.0s) so a persistent
   failure can't block the loop forever. On timeout: real outage recorded,
   next tick starts a fresh attempt sequence against whatever AP is nearest
   by then.

2. **One attempt per tick, not one blocking multi-attempt call.** First
   implementation bundled all retries into a single call that could block
   for up to 8s straight — this meant a struggling handover produced *one*
   CSV row before the struggle and *one* after, with nothing in between
   (looked like the car "froze then teleported" in the log/GUI even though
   the underlying position value was correct). Rewrote so the outer loop
   does exactly one association attempt per iteration, falling through to
   the normal measurement/CSV-write path every time — so a 9-attempt
   struggle now produces 9 real samples, each with its own position, RSSI,
   etc., tagged `[OUTAGE-RETRYING]` until either it succeeds or hits the
   timeout (`[OUTAGE-GAVEUP]`).

3. **New CSV columns**: `outage` (0/1 this row) and `cum_outage_s`
   (cumulative real seconds with zero connectivity, charged per-tick so it
   correctly includes struggles that *eventually* succeed, not just ones
   that time out completely).

4. During a verified outage: `rssi_src='none'`, `bw_mbps=0`, `loss_pct=100`
   are set **explicitly** rather than letting the existing synthetic
   distance-model RSSI fallback quietly imply a plausible signal that the
   car isn't actually receiving. The HTTP cache probe is skipped entirely
   during an outage tick (see Step 7) rather than burning a real 3-second
   timeout to reconfirm what the failed association attempt already showed
   — a real device wouldn't attempt a request with no link either.

**Action for DASH**: if/when DASH adds equivalent outage tracking, use the
same two columns/semantics (`outage`, `cum_outage_s`) so
`compare_speeds.py`-style analysis can line the two arms up directly.
`ensure_assoc()` (`dash-baseline/fix_assoc.py`) already returns a clean
True/False and DASH's loop already does the "stay on old RSU on failure,
retry next tick" pattern — the main missing piece is the wall-clock-driven
`x`/`t` from Step 4, plus surfacing `ok == False` as a tracked outage
state/cumulative metric instead of just a log warning it currently is (see
`run_loop()` lines ~249-251 — failure is logged but never recorded to the
CSV or accumulated anywhere).

---

## Step 6 — Found and fixed a real timing bug (important gotcha)

While testing, one run showed **near-total outage for the entire second
half of a trip** (46+ continuous seconds, spanning two AP zones, never
recovering) — looked like a devastating real finding, turned out to be a
bug. When the single-attempt logic was factored out into its own function,
a critical line was dropped:

```python
# What ensure_assoc_sdn() (and DASH's fix_assoc.ensure_assoc()) correctly do:
car1.cmd('iw dev car1-wlan0 connect %s %s %s' % (...))
time.sleep(wait)                              # let the handshake actually finish
link = car1.cmd('iw dev car1-wlan0 link')     # THEN check

# What the refactored version accidentally did (BUG):
car1.cmd('iw dev car1-wlan0 connect %s %s %s' % (...))
link = car1.cmd('iw dev car1-wlan0 link')     # checked immediately, handshake not done yet
```

`iw connect` returns as soon as it *issues* the request, not once the
radio-level negotiation actually completes. Checking with zero delay means
almost every attempt gets judged before it had any real chance to succeed.
Fixed by restoring the `time.sleep(settle_s=0.8)` between issuing the
connect and checking its result.

**Good news: DASH's `fix_assoc.py::ensure_assoc()` already has this right**
(`time.sleep(wait)` is correctly placed between `iw connect` and the link
check) — this was a mistake introduced by copy-refactoring on the CDN side,
not a shared/pre-existing issue. **Flagging only as a gotcha**: if DASH
builds an equivalent per-tick retry state machine (Step 5), watch for this
exact trap if any association logic gets refactored/extracted — it's an
easy line to lose.

---

## Step 7 — Cache HIT/MISS/UNKNOWN → HIT/MISS/LOSS

Cache status is strictly a question about edge content: either the edge has
it (**HIT**) or it doesn't (**MISS**). There is no legitimate third
"unknown" content-state. The old code used `'UNKNOWN'` for two different
things that both actually mean "we never got a real answer":
`measure_cdn()`'s own default when a curl probe returns no
`X-Cache-Status` header (timeout/failure), and the outage-handling code
manually setting it during a verified outage. Both cases are a
**connection/request LOSS**, not cache ambiguity — renamed both to
`'LOSS'`. Updated the stall-detection check (`cache == 'LOSS'` instead of
`== 'UNKNOWN'`) and `compare_speeds.py`'s plot (`CV_MAP`, y-axis tick
labels) to match.

**Not directly applicable to DASH** — DASH doesn't have a CDN cache concept,
its quality/rung classification already has its own distinct fallback
(`RUNG_LABEL.get(qidx, "buffering")` — "buffering" is its own real state,
not muddled with a chosen-quality-tier value). **Only relevant if/when DASH
adds its own outage/connection-loss tracking**: keep the same principle —
a connectivity-layer failure (no signal at all) should be its own distinct
state, not folded into whatever content/quality enum already exists for
unrelated reasons.

---

## Fairness checklist for DASH vs CDN at highway speed

Once DASH mirrors the above:
- [ ] Same speed sweep: 20 (baseline) / 80 / 100 / 120 km/h
- [ ] Same RSU/AP layout (`x = 0, 500, 1000, 1500`, 300m coverage) — already true per `TEAMMATE_SETUP.md`
- [ ] Both arms derive position from **real elapsed wall-clock time**, not a fixed per-tick increment (Step 4 — currently only true for CDN)
- [ ] Both arms track real outage (`outage`, `cum_outage_s` columns) the same way (Step 5)
- [ ] Both arms use the same `--handover-timeout` budget (default 8.0s) so neither gives up "easier" than the other
- [ ] Report **outage ratio** (`cum_outage_s / total_run_time`) as the headline speed-comparison metric, not just rebuffer ratio — rebuffer% can understate the problem since a player can coast through a real outage on existing buffer without itself rebuffering (confirmed on real 80km/h data: outage went from 0s → 16s across two otherwise-identical runs while rebuffer% barely moved)
- [ ] Run **multiple rounds per speed**, not one — real WiFi reassociation success/failure is non-deterministic run-to-run in this testbed (confirmed: identical 80km/h config gave 0 outages on one run, 16.95% outage on another)

## File reference (CDN side)

- `CDN_SIT2/cdn_sdn_hight_speed.py` — the topology/run script (all changes above)
- `CDN_SIT2/compare_speeds.py` — multi-speed comparison table + overlay plot
- `CDN_SIT2/results_hightspeed/sit{N}/speed{S}/<run_id>/` — where results land (mirrors `results/cdn_baseline/sdn/sit{N}/speed{S}/` layout)
- Run: `sudo python3 cdn_sdn_hight_speed.py --sit 1 --speed <20|80|100|120> --round <N>` (Ryu must be running first: `ryu-manager cdn_switch_13.py --ofp-tcp-listen-port 6654`)
