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
#    Definition: a single linear map from the (real, measured) RSSI to the
#    imposed bandwidth.  RSSI in [RSSI_EDGE, RSSI_CENTER] dBm  ->  BW in
#    [BW_MIN, BW_MAX] Mbps, clipped at the ends. Monotonic and reproducible.
# --------------------------------------------------------------------------
RSSI_CENTER = -29.0    # measured RSSI near the RSU
RSSI_EDGE   = -76.0    # measured RSSI at the ~300 m coverage edge
BW_MAX      = 10.0     # Mbps imposed at the RSU      (> 1080p=5 -> headroom)
BW_MIN      = 0.5      # Mbps imposed at the edge     (< 360p=1  -> edge stalls)

def imposed_bandwidth(rssi_dbm):
    """Imposed link bandwidth (Mbps) as a linear function of measured RSSI."""
    frac = (rssi_dbm - RSSI_EDGE) / (RSSI_CENTER - RSSI_EDGE)
    bw = BW_MIN + frac * (BW_MAX - BW_MIN)
    return max(BW_MIN, min(BW_MAX, bw))

# backward-compatible name used by the runner / preview
throughput_from_rssi = imposed_bandwidth

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
