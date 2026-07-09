#!/usr/bin/env python3
"""
============================================================================
  campus_config.py  --  Situation 1 (Traffic Density) shared config
----------------------------------------------------------------------------
  Project : Comparative Analysis of SDN-CDN and SDN-DASH for Video
            Streaming in Vehicular Networks
  Author  : Hadis Rodpradit (DASH side)

  Shared, importable module so the SDN+CDN teammate arm can reuse the exact
  same campus geometry, mobility route and wireless stimulus for a fair
  Situation 1 comparison (same content/ladder/mu already covered in
  TEAMMATE_SETUP.md -- this file adds the platoon/density-specific pieces).

  Everything below that is REUSED is imported, never copied+modified, from
  the frozen Phase 1 baseline:
    - RSU positions + real PSU-Phuket loop route:
        Dash/topology/dash_topology.py (RSU_LAYOUT), mobility_positions.py
    - RSSI -> bandwidth tiering (step2h):
        dash-baseline/baseline_model.py (Step2HysteresisMapper)
  Those source files are NOT touched by this scenario -- read/import only.

  What IS new for Situation 1:
    - Wireless standard changed to IEEE 802.11g (54 Mbps PHY, 20 MHz channel,
      2.4 GHz) instead of the baseline's 802.11a/p @ 5GHz -- Situation 1
      is deliberately testing congestion under a consumer-WiFi-class link,
      not V2I DSRC.
    - AP_CAPACITY_MBPS: the effective Layer-7 throughput ceiling per AP after
      802.11g protocol overhead (encapsulation/ACK/IFS) -- ~20 Mbps, the
      standard measured figure for a 54 Mbps 802.11g link. This is the
      capacity PLATOON vehicles at the same AP contend for.
    - Car-following / platoon mobility model (N vehicles, fixed 10 m gap,
      constant 20 km/h) layered on top of the same loop route.
============================================================================
"""

import os
import sys
import math

# ---------------------------------------------------------------------------
#  Wire up imports from the frozen baseline (read-only -- see header note)
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'Dash', 'topology'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'dash-baseline'))

from mobility_positions import positions           # noqa: E402  (real loop trace)
from baseline_model import Step2HysteresisMapper    # noqa: E402  (RSSI tier + hysteresis)

# Reused helpers from the frozen single-vehicle topology -- imported, not
# duplicated, so any future baseline fix propagates here automatically.
# NOTE: dash_topology.py's ensure_assoc()/flush_host_state()/
# get_link_info()/warmup_connectivity() all hard-code the interface name
# 'car1-wlan0' internally (they were written for exactly one vehicle named
# car1), so they are NOT safe to reuse as-is for car2..carN -- they are
# reimplemented, correctly parameterised by station name, in
# platoon_topology.py instead. Only the genuinely station-agnostic helpers
# are imported here.
from dash_topology import (        # noqa: E402
    target_rsu_by_zone,
    distance,
    disable_mn_wifi_graph_updates,
    parse_link_info,
)

# ---------------------------------------------------------------------------
#  RSU layout -- SAME physical positions as the frozen DASH baseline
#  (Dash/topology/dash_topology.py: RSU_LAYOUT), retagged to 2.4 GHz channels
#  for 802.11g. Only 3 non-overlapping 2.4 GHz channels exist (1/6/11) for
#  4 RSUs that are all mutually within ~150 m of each other on this loop, so
#  one channel is necessarily reused (rsu1 <-> rsu4, the two most physically
#  separated on the loop). This is a known simplification, not a bug.
# ---------------------------------------------------------------------------
RSU_LAYOUT = {
    'rsu1': {'x': 58,  'y': 160, 'channel': '1'},
    'rsu2': {'x': 142, 'y': 160, 'channel': '6'},
    'rsu3': {'x': 138, 'y': 64,  'channel': '11'},
    'rsu4': {'x': 56,  'y': 66,  'channel': '1'},
}

# ---------------------------------------------------------------------------
#  Wireless PHY/MAC -- IEEE 802.11g (Situation 1 spec)
# ---------------------------------------------------------------------------
WIFI_MODE          = 'g'      # IEEE 802.11g, 2.4 GHz
WIFI_RANGE_M        = 300     # unchanged from the baseline's coverage radius
TX_POWER_DBM        = 20
PROPAGATION_MODEL   = 'logDistance'
PROPAGATION_EXP     = 3

PHY_RATE_MBPS       = 54.0    # raw 802.11g signalling rate (20 MHz channel)
AP_CAPACITY_MBPS    = 20.0    # effective L7 throughput cap per AP (shared by
                              # all vehicles currently associated to it --
                              # this is the contention-sharing pool)

BACKHAUL_BW_MBPS    = 100     # RSU <-> SDN core fiber, unchanged
CTRL_IP             = '127.0.0.1'
CTRL_PORT           = 6653

# ---------------------------------------------------------------------------
#  Addressing (single /24, same subnet as the baseline)
# ---------------------------------------------------------------------------
SERVER_IP = '10.0.0.10'
RSU_IPS = {
    'rsu1': '10.0.0.101', 'rsu2': '10.0.0.102',
    'rsu3': '10.0.0.103', 'rsu4': '10.0.0.104',
}


def car_ip(i):
    """1-based vehicle index -> IP. car1=10.0.0.201, car2=10.0.0.202, ..."""
    return f'10.0.0.{200 + i}'


def car_mac(i):
    return '00:00:00:00:03:%02x' % i


# ---------------------------------------------------------------------------
#  Platoon / car-following mobility model
# ---------------------------------------------------------------------------
SPACING_M   = 10.0                 # fixed inter-vehicle gap
SPEED_KMH   = 20.0
SPEED_MPS   = SPEED_KMH / 3.6
CAR_COUNTS  = [3, 5, 7]            # the 3 scalability cases

SAMPLE_DT_S = 0.5                  # RSSI/throttle re-evaluation interval

# Video rendition ladder (must match the baseline exactly -- fairness)
LADDER = {
    "360p":  1.0,
    "720p":  2.5,
    "1080p": 5.0,
}

# ---------------------------------------------------------------------------
#  Arc-length parameterisation of the real loop route, so a fixed physical
#  10 m gap between vehicles is exact (not a fixed-index/waypoint offset,
#  which would only be approximate since waypoints are not perfectly evenly
#  spaced). The recorded route's start/end points are ~7.3 m apart (not
#  exactly closed), so an explicit CLOSING segment is appended back from the
#  last waypoint to the first -- without it, arc-length wraparound has a
#  ~7.3 m discontinuity right at the seam (a trailing vehicle whose position
#  happens to fall there would show a straight-line gap to the vehicle ahead
#  of ~7.3 m + SPACING_M instead of exactly SPACING_M; caught via
#  visualize_platoon.py's gap self-check and fixed here).
# ---------------------------------------------------------------------------
_cum_dist = [0.0]
for _k in range(1, len(positions)):
    _, _x1, _y1 = positions[_k - 1]
    _, _x2, _y2 = positions[_k]
    _cum_dist.append(_cum_dist[-1] + math.hypot(_x2 - _x1, _y2 - _y1))

_, _last_x, _last_y = positions[-1]
_, _first_x, _first_y = positions[0]
_CLOSE_LEN = math.hypot(_first_x - _last_x, _first_y - _last_y)

ROUTE_LENGTH_M  = _cum_dist[-1] + _CLOSE_LEN        # ~514 m (loop, closed)
LAP_DURATION_S  = ROUTE_LENGTH_M / SPEED_MPS        # ~92.6 s at 20 km/h


def position_at_arc(s):
    """(x, y) at arc-length s (metres) along the loop route.

    s is wrapped modulo ROUTE_LENGTH_M so it is always defined; linear
    interpolation is used between the two bracketing waypoints, including
    the explicit closing segment back to the start (see note above).
    """
    s = s % ROUTE_LENGTH_M
    if s > _cum_dist[-1]:
        # in the synthetic closing segment, last waypoint -> first waypoint
        frac = 0.0 if _CLOSE_LEN <= 0 else (s - _cum_dist[-1]) / _CLOSE_LEN
        return (_last_x + frac * (_first_x - _last_x),
                _last_y + frac * (_first_y - _last_y))

    lo, hi = 0, len(_cum_dist) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if _cum_dist[mid] <= s:
            lo = mid
        else:
            hi = mid
    seg_len = _cum_dist[hi] - _cum_dist[lo]
    frac = 0.0 if seg_len <= 0 else (s - _cum_dist[lo]) / seg_len
    _, x1, y1 = positions[lo]
    _, x2, y2 = positions[hi]
    x = x1 + frac * (x2 - x1)
    y = y1 + frac * (y2 - y1)
    return x, y


def vehicle_position(vehicle_index, t):
    """Position of vehicle `vehicle_index` (0-based, 0 = platoon leader) at
    elapsed simulation time t (seconds since the platoon started moving).

    All vehicles move at the SAME constant SPEED_MPS, so vehicle i is always
    exactly `vehicle_index * SPACING_M` metres behind the leader along the
    route -- a car-following / platoon model with a fixed physical gap.
    """
    s = SPEED_MPS * t - vehicle_index * SPACING_M
    return position_at_arc(s)
