#!/usr/bin/env python3
"""
baseline_model.py
-----------------
Shared model for the single-vehicle / single-RSU DASH baseline.

The SAME functions are imported by:
  * baseline_topo.py    -> real Mininet-WiFi run (real RSSI, real VLC ABR,
                           real ICMP loss); bandwidth is IMPOSED via tc.
  * baseline_preview.py -> pure-python preview (no sudo) to validate the curve

Framing (important for the paper):
  RSSI is real (mn-wifi log-distance propagation). The bandwidth profile is an
  IMPOSED experimental stimulus (see section 2), used to sweep the ABR across
  all three renditions and validate its response -- it is NOT a claim about
  802.11p capacity. Packet loss is MEASURED in the real run (ICMP); the
  loss_from_rssi() below is illustrative for the preview only.

RSSI anchors (match the measured run): ~ -29 dBm at the RSU, ~ -76 dBm at the
~300 m coverage edge.
"""

import math

# --------------------------------------------------------------------------
# Scenario constants
# --------------------------------------------------------------------------
RSU_POS_X       = 0.0      # RSU sits at the centre of the road
COVERAGE_M      = 300.0    # coverage radius (m)
START_X         = -300.0   # vehicle entry point (m)
END_X           = 300.0    # vehicle exit point  (m)
SPEED_MPS       = 1.0      # vehicle speed (m/s)
SAMPLE_DT       = 1.0      # logging / decision interval (s)

# Video rendition ladder (3 qualities only, as requested)  -- Mbps
LADDER = {
    "360p":  1.0,
    "720p":  2.5,
    "1080p": 5.0,
}

# --------------------------------------------------------------------------
# 1) RSSI  (log-distance path-loss, fitted to the sketch anchors)
#    RSSI(d) = RSSI_REF - 10 * n * log10(max(d,1)/d0)
# --------------------------------------------------------------------------
RSSI_REF_DBM = -29.0   # RSSI at the reference distance d0 = 1 m  (~ centre)
PATHLOSS_N   = 1.9     # path-loss exponent (open campus road, LOS-ish)
D0_M         = 1.0     # reference distance
RSSI_FLOOR   = -95.0   # receiver noise floor (dBm)

def rssi_from_distance(d_m):
    """Real propagation curve. d_m = distance from RSU in metres."""
    d = max(abs(d_m), D0_M)
    rssi = RSSI_REF_DBM - 10.0 * PATHLOSS_N * math.log10(d / D0_M)
    return max(rssi, RSSI_FLOOR)

# --------------------------------------------------------------------------
# 2) IMPOSED BANDWIDTH PROFILE  (experimental stimulus -- NOT a physical
#    802.11p capacity claim)
#
#    For a single vehicle streaming <=5 Mbps within a 300 m RSU, the real
#    802.11p link is NOT the bottleneck (Shannon capacity stays >12 Mbps down
#    to ~-90 dBm), so it would never force adaptation. To VALIDATE that the
#    DASH client adapts correctly across its full operating range, we IMPOSE a
#    controlled bandwidth profile via traffic shaping (tc). It is an
#    experimental input, applied identically to the DASH and CDN arms so the
#    comparison is fair.
#
#    Two mapping modes are provided (selected by the runner via `mode`):
#      "linear" (original) -- a single linear map from RSSI in
#        [RSSI_EDGE, RSSI_CENTER] dBm to BW in [BW_MIN, BW_MAX] Mbps.
#      "step" (proposed)   -- discrete rate tiers, closer to how real 802.11
#        rate adaptation actually behaves (the radio selects from a discrete
#        set of PHY/MCS rates based on channel quality, not a continuous
#        ramp) -- see STEP_TABLE below.
#    Both are monotonic and reproducible; "linear" remains the default so
#    existing runs/scripts are unaffected unless they opt into "step".
# --------------------------------------------------------------------------
RSSI_CENTER = -29.0    # measured RSSI near the RSU
RSSI_EDGE   = -76.0    # measured RSSI at the ~300 m coverage edge
BW_MAX      = 10.0     # Mbps imposed at the RSU      (> 1080p=5 -> headroom)
BW_MIN      = 0.5      # Mbps imposed at the edge     (< 360p=1  -> edge stalls)

def imposed_bandwidth_linear(rssi_dbm):
    """Imposed link bandwidth (Mbps) as a linear function of measured RSSI."""
    frac = (rssi_dbm - RSSI_EDGE) / (RSSI_CENTER - RSSI_EDGE)
    bw = BW_MIN + frac * (BW_MAX - BW_MIN)
    return max(BW_MIN, min(BW_MAX, bw))

# discrete rate tiers (checked top-down, first threshold met wins) -- roughly
# mirrors a real 802.11 rate-adaptation table rather than a continuous curve
STEP_TABLE = [
    (-50.0, 10.0),   # RSSI > -50         -> 10 Mbps
    (-60.0,  8.0),   # -60 <= RSSI < -50  ->  8 Mbps
    (-70.0,  5.0),   # -70 <= RSSI < -60  ->  5 Mbps
    (-76.0,  2.0),   # -76 <= RSSI < -70  ->  2 Mbps
]
STEP_FLOOR = 0.5     # RSSI < -76         -> 0.5 Mbps

def imposed_bandwidth_step(rssi_dbm):
    """Imposed bandwidth (Mbps) as a discrete step function of RSSI."""
    for threshold, bw in STEP_TABLE:
        if rssi_dbm >= threshold:
            return bw
    return STEP_FLOOR

# "step2": same 5-tier idea as STEP_TABLE, but thresholds are chosen from
# equal DISTANCE bands (50/100/200/300 m) instead of equal RSSI steps, then
# converted through rssi_from_distance(). Because RSSI-vs-distance is
# logarithmic, equal RSSI steps compress into very narrow distance/time bands
# near the RSU (the original STEP_TABLE's 10/8 Mbps tiers were only ~4.6s/5.4s
# wide -- close to the 4.0s segment duration, which is what produced the
# extra quality-switch flapping seen in the smoke tests). Distance-equal bands
# instead guarantee >=18s dwell in every tier (>=3x segment duration), fixing
# that root cause directly without touching ABR hysteresis or anything else.
#   d <=  50 m -> 10 Mbps   (18s dwell)      d in ( 50,100] ->  8 Mbps (18s)
#   d in (100,200] -> 5 Mbps (36s)           d in (200,300] ->  2 Mbps (36s)
STEP2_TABLE = [
    (-61.3, 10.0),   # RSSI >= -61.3          (d <=  50 m) -> 10 Mbps
    (-67.0,  8.0),   # -67.0 <= RSSI < -61.3  ( 50 < d <= 100 m) -> 8 Mbps
    (-72.7,  5.0),   # -72.7 <= RSSI < -67.0  (100 < d <= 200 m) -> 5 Mbps
    (-76.1,  2.0),   # -76.1 <= RSSI < -72.7  (200 < d <= 300 m) -> 2 Mbps
]
STEP2_FLOOR = 0.5    # RSSI < -76.1  (d > 300 m, outside coverage) -> 0.5 Mbps

def imposed_bandwidth_step2(rssi_dbm):
    """Imposed bandwidth (Mbps): step function with distance-equal tiers."""
    for threshold, bw in STEP2_TABLE:
        if rssi_dbm >= threshold:
            return bw
    return STEP2_FLOOR

def imposed_bandwidth(rssi_dbm, mode="linear"):
    """Imposed link bandwidth (Mbps). mode: 'linear' (default), 'step'
    (equal-RSSI tiers) or 'step2' (equal-distance tiers, wider dwell time)."""
    if mode == "step":
        return imposed_bandwidth_step(rssi_dbm)
    if mode == "step2":
        return imposed_bandwidth_step2(rssi_dbm)
    return imposed_bandwidth_linear(rssi_dbm)

# backward-compatible name used by the runner / preview
throughput_from_rssi = imposed_bandwidth

# --------------------------------------------------------------------------
# 2b) "step2h" -- step2 with Schmitt-trigger hysteresis around each boundary
#
#    step2 (equal-distance tiers) fixed the narrow-dwell-time problem (more
#    1080p time, better QoE) but did NOT reduce quality-switch count vs the
#    original step mapping -- some of the remaining switches turned out (per
#    the smoke-test debug) to be VLC-internal segment-to-segment lag, not
#    tier-boundary jitter, so they won't respond to this. But switches that
#    ARE caused by live RSSI hovering right at a step2 threshold (bouncing
#    the imposed bandwidth across the boundary every sample) should be
#    damped by a dead-band: once in a tier, RSSI has to clear the boundary
#    by STEP2_HYST_DB more before the tier is allowed to change again.
#
#    Stateful (needs to remember the current tier) -- unlike the other
#    mapping functions, so it's a class instantiated once per run, not a
#    plain function. Use Step2HysteresisMapper().update(rssi) per sample.
# --------------------------------------------------------------------------
STEP2_HYST_DB = 1.5   # dB dead-band half-width around each step2 boundary
# full ladder incl. the floor as a sentinel "5th tier" with threshold -inf,
# so up/down logic doesn't need special-casing for the lowest rung
_STEP2_FULL = STEP2_TABLE + [(float("-inf"), STEP2_FLOOR)]

class Step2HysteresisMapper:
    """Stateful step2 mapping with a +/-STEP2_HYST_DB dead-band per boundary."""
    def __init__(self, hyst=STEP2_HYST_DB):
        self.hyst = hyst
        self.idx = len(_STEP2_FULL) - 1   # start at the lowest/floor tier

    def update(self, rssi_dbm):
        # tier this rssi would map to with NO hysteresis (0 = best)
        nominal = len(_STEP2_FULL) - 1
        for i, (th, _bw) in enumerate(_STEP2_FULL):
            if rssi_dbm >= th:
                nominal = i
                break

        if nominal < self.idx:
            # candidate wants a BETTER tier -- only take it if rssi clears
            # that tier's own threshold by the extra hysteresis margin
            th_target = _STEP2_FULL[nominal][0]
            if rssi_dbm >= th_target + self.hyst:
                self.idx = nominal
        elif nominal > self.idx:
            # candidate wants a WORSE tier -- only give up the current one
            # once rssi has fallen the margin below ITS OWN threshold
            th_current = _STEP2_FULL[self.idx][0]
            if rssi_dbm < th_current - self.hyst:
                self.idx = nominal

        return _STEP2_FULL[self.idx][1]

# --------------------------------------------------------------------------
# 3) ABR controller -- throughput based, starts LOW, hysteresis on step-up
# --------------------------------------------------------------------------
class ABRController:
    """
    Rate-based ABR.
      * starts at the lowest rung (matches 'begin poor -> 360p' in the sketch)
      * drops immediately if the current rung is no longer affordable
      * steps up only when the next rung fits with a safety margin (anti-flap)
    """
    def __init__(self, ladder=LADDER, safety=0.90, up_margin=1.15):
        # rungs sorted ascending by bitrate: [(name, mbps), ...]
        self.rungs = sorted(ladder.items(), key=lambda kv: kv[1])
        self.names = [n for n, _ in self.rungs]
        self.brs   = [b for _, b in self.rungs]
        self.safety = safety
        self.up_margin = up_margin
        self.idx = 0  # start at lowest

    def update(self, throughput_mbps):
        budget = throughput_mbps * self.safety

        # highest rung affordable right now (for dropping)
        down_idx = 0
        for i, br in enumerate(self.brs):
            if br <= budget:
                down_idx = i
        # highest rung affordable *with margin* (for climbing)
        up_idx = 0
        for i, br in enumerate(self.brs):
            if br * self.up_margin <= budget:
                up_idx = i

        if down_idx < self.idx:
            self.idx = down_idx          # fall fast
        elif up_idx > self.idx:
            self.idx = up_idx            # rise slowly

        stall = budget < self.brs[0]     # cannot even sustain 360p
        return self.names[self.idx], self.brs[self.idx], stall

# --------------------------------------------------------------------------
# 4) QoE  (simple MOS-style linear model: utility - switch - rebuffer)
# --------------------------------------------------------------------------
_QOE_UTIL = {"360p": 1.5, "720p": 3.5, "1080p": 5.0}
SWITCH_PENALTY  = 0.6   # per quality change
REBUFFER_FLOOR  = 1.0   # QoE value while stalling

def qoe(rendition, switched, stall):
    if stall:
        return REBUFFER_FLOOR
    val = _QOE_UTIL[rendition]
    if switched:
        val -= SWITCH_PENALTY
    return max(1.0, min(5.0, val))

# --------------------------------------------------------------------------
# 5) Packet loss from RSSI (logistic, climbs near the coverage edge)
# --------------------------------------------------------------------------
LOSS_MAX   = 100.0
LOSS_C     = -80.0   # 50%-loss centre point (dBm)
LOSS_K     = 0.5     # steepness

def loss_from_rssi(rssi_dbm):
    """Packet-loss percentage as a function of RSSI."""
    return LOSS_MAX / (1.0 + math.exp(LOSS_K * (rssi_dbm - LOSS_C)))
