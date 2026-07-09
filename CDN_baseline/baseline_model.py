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
SAMPLE_DT    = 1.0                            # s

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
# -> 4,809,772 bps). Re-encoded 2026-07-09 from the same pristine source
# (~/sdn-vanet-project/bbb_sunflower_1080p_30fps_normal.mp4) DASH's ladder
# uses, with the EXACT same libx264 settings as its 1080p rung
# (-b:v 5000k -maxrate 5500k -bufsize 10000k, veryfast/main/yuv420p) --
# lands at 4.81 Mbps rather than exactly 5.0 because that's single-pass
# constrained VBR, same as DASH's own ladder encode (not 2-pass), so this
# is the expected result of matching DASH's actual encoding recipe, not a
# shortfall. Video2.mp4 is a byte-identical copy (see their md5sums).
CDN_BITRATE_MBPS = 4.81
MU = 1.0    # standard default, same as DASH

def cdn_qoe(stall):
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
    that condition."""
    rebuf_s = 1.0 if stall else 0.0
    return CDN_BITRATE_MBPS - rebuf_s

def compute_cdn_qoe(rows):
    """Post-hoc per-row QoE for a whole CDN run: apply cdn_qoe() to each
    already-collected raw CSV row (dicts, e.g. from csv.DictReader).

    Deliberately NOT computed during the mininet-wifi run itself -- the raw
    CSV only stores signals (cache, latency_s, stall, handover), same as the
    DASH arm's raw CSV never bakes in a qoe value either. Compute it here
    instead, so a formula change (mu, thresholds, ...) never requires
    re-running the experiment, just recomputing from the CSV on disk."""
    return [
        cdn_qoe(int(r['stall']))
        for r in rows
    ]