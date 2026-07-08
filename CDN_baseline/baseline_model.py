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
RSSI_CENTER = -29.0
RSSI_EDGE   = -76.0
BW_MAX      = 10.0   # Mbps (same as DASH)
BW_MIN      = 0.5    # Mbps

def imposed_bandwidth(rssi_dbm):
    frac = (rssi_dbm - RSSI_EDGE) / (RSSI_CENTER - RSSI_EDGE)
    bw   = BW_MIN + frac * (BW_MAX - BW_MIN)
    return max(BW_MIN, min(BW_MAX, bw))

throughput_from_rssi = imposed_bandwidth   # backward-compat alias

# ── Packet loss model (logistic, identical to DASH) ───────────────────────
LOSS_MAX = 100.0
LOSS_C   = -80.0
LOSS_K   = 0.5

def loss_from_rssi(rssi_dbm):
    return LOSS_MAX / (1.0 + math.exp(LOSS_K * (rssi_dbm - LOSS_C)))

# ── CDN QoE model ──────────────────────────────────────────────────────────
# Maps cache status + throughput to a 1-5 MOS-style QoE score.
# Mirrors the DASH QoE utility values so scores are comparable.
#   HIT  + high throughput → 5.0  (like 1080p, instant delivery)
#   HIT  + degraded        → 3.5  (like 720p, cache still fast)
#   MISS + normal          → 1.5  (like 360p, fetching from origin)
#   MISS + stall (timeout) → 1.0  (stall)
#
# Penalty of 0.6 per handover (same as DASH quality-switch penalty).
QOE_HIT_FULL    = 5.0
QOE_HIT_PARTIAL = 3.5
QOE_MISS_NORMAL = 1.5
QOE_STALL       = 1.0
SWITCH_PENALTY  = 0.6    # applied when AP changes (handover)
TIMEOUT_S       = 3.0    # request > 3s treated as stall
# 0.15s: HITs close to AP (lat<0.15s) → 5.0, HITs near AP edge (lat≥0.15s) → 3.5
HIT_FULL_LATENCY_S = 0.15

def cdn_qoe(cache_status, latency_s, handover, stall):
    """Return QoE score 1-5 for one CDN measurement."""
    if stall or latency_s >= TIMEOUT_S:
        return QOE_STALL
    if cache_status == "HIT":
        score = QOE_HIT_FULL if latency_s < HIT_FULL_LATENCY_S else QOE_HIT_PARTIAL
    else:
        score = QOE_MISS_NORMAL #haha
    if handover: 
        score -= SWITCH_PENALTY
    return max(1.0, min(5.0, score))