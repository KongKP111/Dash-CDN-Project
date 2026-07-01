#!/usr/bin/env python3
"""
baseline_4rsu_model.py
-----------------------
Scenario constants for the 4-RSU handover baseline. Reuses the SAME physical
model (RSSI-from-distance, imposed-bandwidth-from-RSSI, ABR, QoE) as the
1-RSU baseline (baseline_model.py) -- only the RSU layout differs.
"""
import baseline_model as M

# --------------------------------------------------------------------------
# RSU layout: 4 RSUs in a straight line, 300 m radius each, 100 m overlap
#   ap1 @ 0m, ap2 @ 500m, ap3 @ 1000m, ap4 @ 1500m
#   coverage: [-300,300] [200,800] [700,1300] [1200,1800]
#   overlap zones (handover bands): [200,300] [700,800] [1200,1300]
# --------------------------------------------------------------------------
RSU_X        = [0.0, 500.0, 1000.0, 1500.0]
RSU_SSIDS    = ["rsu1-ssid", "rsu2-ssid", "rsu3-ssid", "rsu4-ssid"]
RSU_CHANNELS = ["1", "6", "11", "1"]     # non-overlapping 2.4GHz reuse pattern
COVERAGE_M   = 300.0                      # coverage radius per RSU

START_X   = RSU_X[0] - COVERAGE_M         # -300
END_X     = RSU_X[-1] + COVERAGE_M        # 1800
SPEED_KMH = 20.0
SPEED_MPS = SPEED_KMH / 3.6               # ~5.556 m/s
SAMPLE_DT = 0.5                           # logging / decision interval (s)

# re-export the shared physical/QoE model (same functions as the 1-RSU case)
rssi_from_distance   = M.rssi_from_distance
throughput_from_rssi = M.throughput_from_rssi
LADDER                = M.LADDER
PATHLOSS_N            = M.PATHLOSS_N


def nearest_rsu(x):
    """Index (0-based) of the RSU closest to position x, and that distance."""
    dists = [abs(x - rx) for rx in RSU_X]
    idx = min(range(len(RSU_X)), key=lambda i: dists[i])
    return idx, dists[idx]
