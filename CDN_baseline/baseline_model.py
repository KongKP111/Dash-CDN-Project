#!/usr/bin/env python3
"""
baseline_model.py — CDN Baseline
=================================
IDENTICAL to dash-baseline/baseline_model.py so the imposed bandwidth profile
and RSSI model are exactly the same between DASH and CDN arms.

The only CDN-specific addition: QoE model adapted for cache HIT/MISS instead
of ABR bitrate selection.
"""

import math

# ── Scenario constants ─────────────────────────────────────────────────────
# 4 APs on a straight line — same layout as DASH 4-RSU scenario
# spacing 500 m, radius 300 m, overlap 100 m, START/END at coverage edge
AP_POSITIONS = [0.0, 500.0, 1000.0, 1500.0]  # m  (matches DASH RSU_X)
AP_COVERAGE  = 300.0                           # m per AP (overlap = 100 m)
START_X      = -300.0                          # m  (AP1 coverage edge)
END_X        = 1800.0                          # m  (AP4 coverage edge)
SPEED_MPS    = {20: 20/3.6, 25: 25/3.6, 30: 30/3.6}   # km/h → m/s
# 0.5s, not 1.0 -- aligned to match CDN_SIT1/Situation1_DASH's shared
# campus_config.py (SAMPLE_DT_S=0.5) and dash-baseline/baseline_4rsu_model.py
# (SAMPLE_DT=0.5), the two other places this project samples a 4-AP/RSU
# handover scenario. CDN_baseline (and CDN_SIT2/SIT3, which import this same
# constant) were the only outlier at 1.0. Safe to change: since Option 2,
# position is derived from real wall-clock elapsed time, not accumulated
# from SAMPLE_DT, so this only affects sampling/logging density, not
# position accuracy.
SAMPLE_DT    = 0.5                            # s

# ── RSSI model (identical to DASH) ────────────────────────────────────────
RSSI_REF_DBM = -29.0
PATHLOSS_N   = 1.9
D0_M         = 1.0
RSSI_FLOOR   = -95.0

def rssi_from_distance(d_m):
    d = max(abs(d_m), D0_M)
    return max(RSSI_REF_DBM - 10.0 * PATHLOSS_N * math.log10(d / D0_M),
               RSSI_FLOOR)

def nearest_ap_distance(x_m):
    """Distance to nearest AP in metres."""
    return min(abs(x_m - ap) for ap in AP_POSITIONS)

def nearest_ap_index(x_m):
    """0-based index of nearest AP."""
    return min(range(len(AP_POSITIONS)),
               key=lambda i: abs(x_m - AP_POSITIONS[i]))

# ── Imposed bandwidth profile (IDENTICAL to DASH) ─────────────────────────
# Same mapping modes as dash-baseline/baseline_model.py, kept byte-for-byte
# identical (thresholds/values) so both arms react to the same stimulus —
# see TEAMMATE_SETUP.md #2. step2h is the mapping the DASH side landed on
# (best QoE, fewest switches) and is the default here too.
RSSI_CENTER = -29.0
RSSI_EDGE   = -76.0
BW_MAX      = 10.0   # Mbps (same as DASH)
BW_MIN      = 0.5    # Mbps

def imposed_bandwidth_linear(rssi_dbm):
    frac = (rssi_dbm - RSSI_EDGE) / (RSSI_CENTER - RSSI_EDGE)
    bw   = BW_MIN + frac * (BW_MAX - BW_MIN)
    return max(BW_MIN, min(BW_MAX, bw))

# discrete rate tiers (equal-RSSI steps) — mirrors dash-baseline STEP_TABLE
STEP_TABLE = [
    (-50.0, 10.0),
    (-60.0,  8.0),
    (-70.0,  5.0),
    (-76.0,  2.0),
]
STEP_FLOOR = 0.5

def imposed_bandwidth_step(rssi_dbm):
    for threshold, bw in STEP_TABLE:
        if rssi_dbm >= threshold:
            return bw
    return STEP_FLOOR

# discrete rate tiers (equal-DISTANCE bands, >=18s dwell) — mirrors
# dash-baseline STEP2_TABLE
STEP2_TABLE = [
    (-61.3, 10.0),
    (-67.0,  8.0),
    (-72.7,  5.0),
    (-76.1,  2.0),
]
STEP2_FLOOR = 0.5

def imposed_bandwidth_step2(rssi_dbm):
    for threshold, bw in STEP2_TABLE:
        if rssi_dbm >= threshold:
            return bw
    return STEP2_FLOOR

def imposed_bandwidth(rssi_dbm, mode="linear"):
    """Imposed link bandwidth (Mbps). mode: 'linear' (default), 'step'
    (equal-RSSI tiers), 'step2' (equal-distance tiers) or 'step2h' (step2 +
    hysteresis — use Step2HysteresisMapper for that, it's stateful)."""
    if mode == "step":
        return imposed_bandwidth_step(rssi_dbm)
    if mode == "step2":
        return imposed_bandwidth_step2(rssi_dbm)
    return imposed_bandwidth_linear(rssi_dbm)

throughput_from_rssi = imposed_bandwidth   # backward-compat alias

# ── step2h: step2 + Schmitt-trigger hysteresis around each boundary ───────
# Stateful — instantiate ONCE per run, call .update(rssi) every sample.
# Mirrors dash-baseline's Step2HysteresisMapper exactly (see its docstring
# for the rationale — damps switches from live RSSI jitter at a boundary).
STEP2_HYST_DB = 1.5
_STEP2_FULL = STEP2_TABLE + [(float("-inf"), STEP2_FLOOR)]

class Step2HysteresisMapper:
    """Stateful step2 mapping with a +/-STEP2_HYST_DB dead-band per boundary."""
    def __init__(self, hyst=STEP2_HYST_DB):
        self.hyst = hyst
        self.idx = len(_STEP2_FULL) - 1   # start at the lowest/floor tier

    def update(self, rssi_dbm):
        nominal = len(_STEP2_FULL) - 1
        for i, (th, _bw) in enumerate(_STEP2_FULL):
            if rssi_dbm >= th:
                nominal = i
                break

        if nominal < self.idx:
            th_target = _STEP2_FULL[nominal][0]
            if rssi_dbm >= th_target + self.hyst:
                self.idx = nominal
        elif nominal > self.idx:
            th_current = _STEP2_FULL[self.idx][0]
            if rssi_dbm < th_current - self.hyst:
                self.idx = nominal

        return _STEP2_FULL[self.idx][1]

# ── Packet loss model (logistic, identical to DASH) ───────────────────────
LOSS_MAX = 100.0
LOSS_C   = -80.0
LOSS_K   = 0.5

def loss_from_rssi(rssi_dbm):
    return LOSS_MAX / (1.0 + math.exp(LOSS_K * (rssi_dbm - LOSS_C)))

# ── CDN QoE model (Yin et al., SIGCOMM'15 — same formula as DASH) ─────────
#   QoE = sum(q(R_k)) - mu * sum(|q(R_k+1) - q(R_k)|) - sum(T_k)
# See dash_cdn_comparison.py::compute_dash_qoe() for the DASH-side twin of
# this function and TEAMMATE_SETUP.md #5 for the formula/citation.
#
# CDN has no ABR ladder — every request (HIT or MISS) delivers the SAME
# encoded file at the SAME bitrate; only the delivery speed (cache hit vs.
# origin fetch) differs. So q(R_k) here is a *constant* measured from the
# actual content file, and the |q(R_k+1)-q(R_k)| switch-penalty term is
# always 0 — that's the honest, correct result of this architecture having
# no rendition switching, not a workaround. Any handover/MISS disruption
# shows up through T_k (rebuffer), via the caller's latency-based `stall`
# flag, exactly like it does on the DASH side — no separate ad-hoc
# handover penalty is added on top.
#
# CDN_BITRATE_MBPS is measured directly off Video.mp4's video stream
# (`ffprobe -select_streams v:0 -show_entries stream=bit_rate Video.mp4`
# -> 4,965,301 bps). Re-encoded 2026-07-10 from the same pristine source
# (~/sdn-vanet-project/bbb_sunflower_1080p_30fps_normal.mp4) DASH's ladder
# uses, same libx264 settings as its 1080p rung (-b:v 5000k -maxrate 5500k
# -bufsize 10000k, veryfast/main/yuv420p) but as a proper 2-pass encode
# this time (-pass 1/-pass 2) instead of single-pass -- the original
# single-pass encode (kept at CDN/origin/_old_4.81mbps_encode/) landed at
# 4.81 Mbps, a ~4% undershoot from the 5.0 Mbps target that's a known
# single-pass VBR characteristic, not anything wrong with the settings.
# 2-pass rate control targets the requested average bitrate far more
# precisely -- 4.97 Mbps is within 0.7% of DASH's 5.0 Mbps top rung, vs.
# the previous ~4% gap. Video2.mp4 is a byte-identical copy (see their
# md5sums).
CDN_BITRATE_MBPS = 4.97
MU = 1.0    # standard default, same as DASH

def cdn_qoe(stall, dt=1.0):
    """Return this sample's Yin et al. QoE term: q(R) - mu*switch - T_k.
    Summing across a run's rows gives the run's total QoE from the formula
    (mirrors compute_dash_qoe() in dash_cdn_comparison.py).

    Same T_k treatment as compute_dash_qoe(): read the already-computed
    `stall` flag straight off the row -- don't re-derive it from latency
    here, the topology scripts already fold latency/cache/vlc-buffering
    into that one flag when they write the CSV (see
    cdn_baseline_topo_sdn.py's `stall = (latency >= 3.0 or cache ==
    'UNKNOWN' or vlc_stalling)`), so re-checking a latency threshold here
    too would just be a redundant, easy-to-drift-out-of-sync duplicate of
    that condition.

    `dt` is this row's REAL elapsed seconds since the previous row (T_k must
    be real seconds, per the formula) -- default 1.0 only for a caller with
    no timing info at all. compute_cdn_qoe() below always passes the real
    value: since Option 2 (wall-clock-driven position, no freeze during
    handover), a tick's real duration is not a fixed constant -- verified on
    a real run's CSV, inter-row dt ranged 0.9-5.0s, not a flat 1.0s -- and
    CDN_SIT1 feeds this same function 0.5s-nominal ticks (campus_config.py's
    SAMPLE_DT_S), not this file's own 1.0s SAMPLE_DT. A hardcoded 1.0
    overcounts every stalled row's rebuffer penalty whenever its real dt is
    less than 1.0s (e.g. ~2x for CDN_SIT1's 0.5s ticks) and undercounts it
    whenever dt runs long (e.g. a struggling handover retry)."""
    rebuf_s = dt if stall else 0.0
    return CDN_BITRATE_MBPS - rebuf_s

def compute_cdn_qoe(rows):
    """Post-hoc per-row QoE for a whole CDN run: apply cdn_qoe() to each
    already-collected raw CSV row (dicts, e.g. from csv.DictReader).

    Deliberately NOT computed during the mininet-wifi run itself -- the raw
    CSV only stores signals (cache, latency_s, stall, handover), same as the
    DASH arm's raw CSV never bakes in a qoe value either. Compute it here
    instead, so a formula change (mu, thresholds, ...) never requires
    re-running the experiment, just recomputing from the CSV on disk.

    dt is derived from consecutive rows' own 't' column (real elapsed
    seconds, not a fixed SAMPLE_DT) -- see cdn_qoe()'s docstring. The first
    row has no previous timestamp to diff against, so its dt is 0.0 (that
    row's own stall, if any, contributes no rebuffer penalty) -- a minor,
    unavoidable edge case, not a source of systematic bias since it's only
    ever one row per run."""
    qoes = []
    prev_t = None
    for r in rows:
        t = float(r['t'])
        dt = (t - prev_t) if prev_t is not None else 0.0
        qoes.append(cdn_qoe(int(r['stall']), dt))
        prev_t = t
    return qoes