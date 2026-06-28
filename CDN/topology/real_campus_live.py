#!/usr/bin/python3
"""
============================================================
real_campus_live.py  —  Cooperative Edge CDN in SDN-VANET
                        4-Edge-Server Edition

Topology:
  car1 (10.0.0.1/24) ─wifi─ ap1/ap2/ap3/ap4 ─── s1 ─┬─ edge1 (10.0.0.11/24)
                                                       ├─ edge2 (10.0.0.12/24)
                                                       ├─ edge3 (10.0.0.13/24)
                                                       ├─ edge4 (10.0.0.14/24)
                                                       └─ origin (10.0.0.100/24)

  AP ↔ Edge assignment:
    ap1 → edge1  |  ap2 → edge2  |  ap3 → edge3  |  ap4 → edge4

  origin (port 8080) : nginx static file server  +200 ms WAN delay (tc-netem)
  edge1-4 (port 8081): nginx reverse-proxy cache  (proxy_cache_min_uses=2)
  SDN (Ryu / OF 1.3) : routes car1 HTTP to nearest edge based on AP zone

  Project : Cooperative Edge CDN in SDN-Vehicular Networks
  Author  : Kongpop Tipmontree (6630613025)
  Advisor : Asst. Prof. Dr. Kuljaree Tantayakul
============================================================
"""

import os
import sys
import time
import math
import re
import argparse
import threading
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle

from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel, info
from mn_wifi.net import Mininet_wifi
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mobility_positions import positions


# =========================================================
# NETWORK CONFIGURATION
# =========================================================
WIFI_MODE         = 'a'      # 802.11a OFDM at 5GHz (same PHY as 802.11p)
REAL_WIFI_RANGE_M = 300      # 300 m urban DSRC range (ETSI ITS-G5)
TX_POWER_DBM      = 20       # 20 dBm = 100 mW (FCC RSU limit)
PROPAGATION_MODEL = 'logDistance'
PROPAGATION_EXP   = 3
BACKHAUL_BW_MBPS  = 100

CTRL_IP   = '127.0.0.1'
CTRL_PORT = 6653

# Addressing (single /24)
CAR_IP    = '10.0.0.1'
ORIGIN_IP = '10.0.0.100'
EDGE_IPS  = {
    'edge1': '10.0.0.11',
    'edge2': '10.0.0.12',
    'edge3': '10.0.0.13',
    'edge4': '10.0.0.14',
}
ORIGIN_PORT = 8080
EDGE_PORT   = 8081

# Each AP has a dedicated co-located edge server
AP_EDGE_MAP = {
    'ap1': 'edge1',
    'ap2': 'edge2',
    'ap3': 'edge3',
    'ap4': 'edge4',
}

# AP positions — identical layout/channels to DASH RSU_LAYOUT for direct comparison
AP_LAYOUT = {
    'ap1': {'x': 58,  'y': 160, 'channel': '36'},
    'ap2': {'x': 142, 'y': 160, 'channel': '36'},
    'ap3': {'x': 138, 'y': 64,  'channel': '36'},
    'ap4': {'x': 56,  'y': 66,  'channel': '36'},
}

RSSI_LOG_FILE        = 'rssi_real_campus_cdn.csv'
PLOT_COVERAGE_RADIUS = 62
HANDOVER_SETTLE_TIME = 0.60

CDN_VIDEO_PATH  = 'Video.mp4'    # popular  (will be cached → HIT)
CDN_VIDEO2_PATH = 'Video2.mp4'   # unpopular (not cached  → MISS)
CDN_ORIGIN_ROOT = '/home/kongpop/PSU_Project/Dash-CDN-Project/CDN/origin'
CDN_CACHE_BASE  = '/home/kongpop/PSU_Project/Dash-CDN-Project/CDN/edge'

# -----------------------------------------------------------------
# SITUATION TABLE — SDN-CDN Test Cases (matches SDN_Test_Case_Scenarios.pdf)
#
#   speed_kmh : vehicle speed  (30 = urban/suburban, 60 = highway)
#   throttle  : bandwidth stress applied during simulation
#               'none'          — 3 Mbps stable (no throttle)
#               'constant_2m'   — constant 2 Mbps
#               'handover_250k' — 250 kbps for 30 s at each handover event
#               'drop_100k'     — 100 kbps at t=30-45 s and t=75-90 s
#   step_scale: 1.0 = 30 km/h base; 0.5 = 60 km/h  (derived in topology())
# -----------------------------------------------------------------
_BASE_SPEED_KMH = 23.7  # actual avg speed @ step_scale=1.0 (measured from waypoints)
_SIT_CONFIG = {
    1: {'speed_kmh': 30, 'throttle': 'none',
        'desc': 'Normal/Baseline — 30 km/h, 3 Mbps stable'},
    2: {'speed_kmh': 30, 'throttle': 'constant_2m',
        'desc': 'Light Handover (Urban) — 30 km/h, 2 Mbps constant'},
    3: {'speed_kmh': 30, 'throttle': 'handover_250k',
        'desc': 'Heavy Handover (Suburban) — 30 km/h, 250 kbps at each handover'},
    4: {'speed_kmh': 30, 'throttle': 'drop_100k',
        'desc': 'Sudden BW Drop (Dead Zone) — 30 km/h, 100 kbps at t=30-45s, 75-90s'},
    5: {'speed_kmh': 60, 'throttle': 'none',
        'desc': 'High Mobility (Highway) — 60 km/h, 3 Mbps stable'},
    6: {'speed_kmh': 60, 'throttle': 'drop_100k',
        'desc': 'Combined Stress (Worst Case) — 60 km/h, 100 kbps drop at handover'},
}


# =========================================================
# BANDWIDTH THROTTLE  (tc htb on edge servers' outgoing eth)
# =========================================================
def _tc_set(host, iface, kbps):
    """Apply (or remove) htb rate limit on host's iface."""
    host.cmd(f'tc qdisc del dev {iface} root 2>/dev/null || true')
    if kbps and kbps > 0:
        host.cmd(f'tc qdisc add dev {iface} root handle 1: htb default 10')
        host.cmd(f'tc class add dev {iface} parent 1: classid 1:10 '
                 f'htb rate {kbps}kbit ceil {kbps}kbit')


def set_bw_all_edges(edge_objs, kbps):
    """Set (or clear) outgoing rate limit on all edge servers."""
    for name, edge in edge_objs.items():
        _tc_set(edge, f'{name}-eth0', kbps)
    label = f'{kbps} kbps' if kbps else 'unlimited'
    info(f'*** [BW] Edge throttle set to {label}\n')


def start_throttle_scheduler(edge_objs, throttle_mode):
    """
    Launch a daemon thread that applies timed bandwidth changes.
    Called once after network is ready; returns immediately.
    """
    if throttle_mode == 'none':
        return
    if throttle_mode == 'constant_2m':
        set_bw_all_edges(edge_objs, 2000)
        return

    def _schedule():
        t0 = time.time()

        def elapsed():
            return time.time() - t0

        def wait_until(target_s):
            remaining = target_s - elapsed()
            if remaining > 0:
                time.sleep(remaining)

        if throttle_mode == 'drop_100k':
            # 0-30s: normal, 30-45s: 100 kbps, 45-75s: normal, 75-90s: 100 kbps, 90+: normal
            wait_until(30)
            set_bw_all_edges(edge_objs, 100)
            wait_until(45)
            set_bw_all_edges(edge_objs, None)
            wait_until(75)
            set_bw_all_edges(edge_objs, 100)
            wait_until(90)
            set_bw_all_edges(edge_objs, None)

    t = threading.Thread(target=_schedule, daemon=True)
    t.start()


# =========================================================
# NGINX CONFIG GENERATION
# =========================================================
def write_nginx_configs():
    """Write per-edge and origin nginx config files to /tmp/ before Mininet starts."""

    # Origin: static file server on ORIGIN_IP:ORIGIN_PORT
    origin_conf = (
        f'pid /tmp/nginx_origin.pid;\n'
        f'worker_processes 1;\n'
        f'events {{ worker_connections 64; }}\n'
        f'http {{\n'
        f'    server {{\n'
        f'        listen {ORIGIN_IP}:{ORIGIN_PORT};\n'
        f'        root {CDN_ORIGIN_ROOT};\n'
        f'        autoindex on;\n'
        f'        location / {{ try_files $uri $uri/ =404; }}\n'
        f'    }}\n'
        f'}}\n'
    )
    with open('/tmp/nginx_origin.conf', 'w') as fh:
        fh.write(origin_conf)

    # Per-edge: reverse-proxy cache pointing at origin
    for name, ip in EDGE_IPS.items():
        cache_dir = f'{CDN_CACHE_BASE}/{name}_cache'
        os.makedirs(cache_dir, exist_ok=True)

        edge_conf = (
            f'pid /tmp/nginx_{name}.pid;\n'
            f'worker_processes 1;\n'
            f'events {{ worker_connections 64; }}\n'
            f'http {{\n'
            f'    proxy_cache_path {cache_dir}\n'
            f'                     levels=1:2 keys_zone={name}_zone:10m\n'
            # max_size must exceed the largest single cached file (Video.mp4/
            # Video2.mp4 are ~339 MiB each) or nginx's cache manager evicts it
            # almost immediately after writing -> permanent MISS during the run.
            f'                     max_size=500m inactive=60m use_temp_path=off;\n'
            f'    server {{\n'
            f'        listen {ip}:{EDGE_PORT};\n'
            f'        location / {{\n'
            f'            proxy_pass             http://{ORIGIN_IP}:{ORIGIN_PORT};\n'
            # slice splits the cached object into 1m byte-range chunks, so a
            # client requesting only the first 1 MiB only needs THAT slice
            # fetched+cached from origin (~1m, sub-second) instead of forcing
            # a full 339m fetch that can't finish before the next handover.
            f'            slice                  1m;\n'
            f'            proxy_set_header       Range $slice_range;\n'
            f'            proxy_cache_key        $uri$is_args$args$slice_range;\n'
            f'            proxy_set_header       Host $host;\n'
            # Without this, a client disconnect (e.g. curl --max-time hit)
            # aborts the upstream origin fetch and discards the half-written
            # cache file -> every MISS restarts from scratch, never finishes
            # caching. Keep fetching/caching in the background regardless.
            f'            proxy_ignore_client_abort on;\n'
            f'            proxy_cache            {name}_zone;\n'
            f'            proxy_cache_min_uses   2;\n'
            f'            proxy_cache_valid      200 206 10m;\n'
            f'            proxy_cache_use_stale  error timeout updating;\n'
            f'            proxy_ignore_headers   Cache-Control Expires;\n'
            f'            add_header X-Cache-Status $upstream_cache_status;\n'
            f'            add_header X-Edge-Server  {name};\n'
            f'        }}\n'
            f'    }}\n'
            f'}}\n'
        )
        with open(f'/tmp/nginx_{name}.conf', 'w') as fh:
            fh.write(edge_conf)

    info('*** nginx config files written to /tmp/\n')


# =========================================================
# UTILITY
# =========================================================
def distance(x1, y1, x2, y2):
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def estimate_speed_kmh(prev_x, prev_y, x, y, step_time_s):
    if step_time_s <= 0:
        return 0.0
    return (distance(prev_x, prev_y, x, y) / step_time_s) * 3.6


def get_link_info(car1):
    return car1.cmd('iw dev car1-wlan0 link')


def parse_link_info(output, prev_signal=None):
    ap_mac = 'N/A'
    signal = None
    mac_m = re.search(r'Connected to ([0-9a-f:]{17})', output)
    sig_m = re.search(r'signal:\s*(-?\d+)\s*dBm', output)
    if mac_m:
        ap_mac = mac_m.group(1)
    if sig_m:
        try:
            signal = int(sig_m.group(1))
        except ValueError:
            signal = None
    if signal is not None and (signal > 0 or signal < -100):
        signal = prev_signal if prev_signal is not None else -50
    if signal is None:
        signal = prev_signal if prev_signal is not None else -50
    return ap_mac, signal


def ensure_assoc(car1, ap, retries=4, wait=0.8):
    last_output = ''
    for attempt in range(1, retries + 1):
        try:
            car1.setAssociation(ap, intf='car1-wlan0')
        except Exception as e:
            info(f'*** setAssociation warning: {e}\n')
        time.sleep(wait)
        last_output = get_link_info(car1)
        if 'Connected to' in last_output:
            info(f'*** Association success with {ap.name} on attempt {attempt}\n')
            return last_output
        info(f'*** Association attempt {attempt} to {ap.name} failed\n')
        time.sleep(0.3)
    info(f'*** Failed to associate with {ap.name} after {retries} attempts\n')
    return last_output


def flush_host_state(car1, origin, edge_objs):
    car1.cmd('ip neigh flush dev car1-wlan0')
    car1.cmd('ip route flush cache')
    origin.cmd('ip neigh flush dev origin-eth0')
    origin.cmd('ip route flush cache')
    for name, edge in edge_objs.items():
        edge.cmd(f'ip neigh flush dev {name}-eth0')
        edge.cmd('ip route flush cache')


def warmup_connectivity(car1):
    car1.cmd(f'arping -c 2 -I car1-wlan0 {ORIGIN_IP} > /dev/null 2>&1')
    car1.cmd(f'ping -c 2 -W 1 {ORIGIN_IP} > /dev/null 2>&1')


def disable_mn_wifi_graph_updates(sta):
    sta.update_graph = lambda *_: None


# =========================================================
# HANDOVER ZONES  (identical to DASH for fair comparison)
# =========================================================
def target_ap_by_zone(x, y):
    # Top strip
    if y >= 145:
        if x < 108:
            return 'ap1'
        return 'ap2'
    # Right strip
    if x >= 145 and 95 <= y < 145:
        return 'ap2'
    # Bottom strip
    if y < 95:
        if x >= 92:
            return 'ap3'
        return 'ap4'
    # Left strip
    if x < 98 and 95 <= y < 145:
        return 'ap1'
    # Fallback quadrants
    if x >= 120 and y >= 120:
        return 'ap2'
    if x >= 120 and y < 120:
        return 'ap3'
    if x < 120 and y < 120:
        return 'ap4'
    return 'ap1'


# =========================================================
# SDN FLOW HELPERS
# =========================================================
def install_fallback_flows(ap_list, switch_list):
    info('*** Installing fallback flows (priority=100)\n')
    for node in ap_list + switch_list:
        result = node.cmd(
            f'ovs-ofctl -O OpenFlow13 add-flow {node.name} '
            '"priority=100,actions=normal"'
        )
        info(f'    {node.name}: {result.strip() or "OK"}\n')


def set_static_arp(car1, origin, edge_objs):
    info('*** Setting static ARP entries\n')
    origin_mac = origin.cmd('cat /sys/class/net/origin-eth0/address').strip()
    car_mac    = car1.cmd('cat /sys/class/net/car1-wlan0/address').strip()

    if origin_mac:
        car1.cmd(f'arp -s {ORIGIN_IP} {origin_mac}')
        info(f'    car1 → origin  {ORIGIN_IP} = {origin_mac}\n')
    if car_mac:
        origin.cmd(f'arp -s {CAR_IP} {car_mac}')

    for name, edge in edge_objs.items():
        edge_ip  = EDGE_IPS[name]
        edge_mac = edge.cmd(f'cat /sys/class/net/{name}-eth0/address').strip()
        if edge_mac:
            car1.cmd(f'arp -s {edge_ip} {edge_mac}')
            info(f'    car1 → {name}  {edge_ip} = {edge_mac}\n')
        if car_mac:
            edge.cmd(f'arp -s {CAR_IP} {car_mac}')

    info('*** Static ARP done\n')


def verify_connectivity(car1, edge_objs):
    info('*** Verifying connectivity\n')
    r = car1.cmd(f'ping -c 3 -W 2 {ORIGIN_IP}')
    if '0 received' in r or 'Unreachable' in r:
        info('*** WARNING: car1 cannot reach origin\n')
    else:
        info('*** car1 → origin OK\n')

    for name in edge_objs:
        edge_ip = EDGE_IPS[name]
        r2 = car1.cmd(
            f'curl -o /dev/null -s -w "HTTP %{{http_code}} in %{{time_total}}s\\n" '
            f'--max-time 5 http://{edge_ip}:{EDGE_PORT}/'
        )
        info(f'    {name} ({edge_ip}:{EDGE_PORT}): {r2.strip()}\n')


# =========================================================
# NGINX STARTUP  (pid-file-safe: shared PID namespace in Mininet)
# =========================================================
def start_nginx_origin(origin):
    info('*** Starting nginx origin (10.0.0.100:8080)\n')
    pid_file = '/tmp/nginx_origin.pid'
    origin.cmd(f'[ -f {pid_file} ] && kill $(cat {pid_file}) 2>/dev/null; sleep 0.3')
    origin.cmd(f'rm -f {pid_file}')
    origin.cmd('mkdir -p /run/nginx')
    r = origin.cmd('nginx -c /tmp/nginx_origin.conf 2>&1')
    info(f'*** nginx origin: {r.strip() or "OK"}\n')
    time.sleep(0.5)


def start_nginx_edges(edge_objs):
    info('*** Starting nginx on all 4 edge servers\n')
    for name, edge in edge_objs.items():
        pid_file = f'/tmp/nginx_{name}.pid'
        edge.cmd(f'[ -f {pid_file} ] && kill $(cat {pid_file}) 2>/dev/null; sleep 0.2')
        edge.cmd(f'rm -f {pid_file}')
        # Clear cache left over from a previous run so every round starts
        # cold (no prewarm anymore — see topology()).
        edge.cmd(f'rm -rf {CDN_CACHE_BASE}/{name}_cache/*')
        edge.cmd('mkdir -p /run/nginx')
        r = edge.cmd(f'nginx -c /tmp/nginx_{name}.conf 2>&1')
        info(f'*** {name}: {r.strip() or "OK"}\n')
    time.sleep(1)


# =========================================================
# WAN DELAY  (200 ms on origin outgoing — simulates remote cloud)
# =========================================================
def add_wan_delay(origin):
    info('*** Adding 200ms WAN delay on origin-eth0 outgoing\n')
    origin.cmd('tc qdisc del dev origin-eth0 root 2>/dev/null')
    origin.cmd('tc qdisc add dev origin-eth0 root netem delay 200ms')
    info('*** Origin WAN delay OK\n')


# =========================================================
# LIVE PLOT
# =========================================================
_EDGE_COLORS = {
    'edge1': '#e74c3c',
    'edge2': '#2ecc71',
    'edge3': '#9b59b6',
    'edge4': '#f39c12',
}


class RealRoadLivePlot:
    def __init__(self, road_positions, ap_layout, coverage_radius=58):
        self.road_positions  = road_positions
        self.ap_layout       = ap_layout
        self.coverage_radius = coverage_radius
        self.fig = self.ax = self.car_marker = self.info_text = None
        self.path_trace_done = self.path_trace_future = None
        self.all_x = [pos[1] for pos in road_positions]
        self.all_y = [pos[2] for pos in road_positions]

    def setup(self):
        # mn_wifi.telemetry sets style.use('fivethirtyeight') on import,
        # which would otherwise tint this plot grey instead of matching
        # the white DASH reference style (visualize_topology.py).
        plt.style.use('default')
        plt.ion()
        self.fig, self.ax = plt.subplots(figsize=(10, 8))
        xs, ys = self.all_x, self.all_y

        # ── Route path (same style as DASH visualize_topology.py) ────────
        self.path_trace_future, = self.ax.plot(
            xs + [xs[0]], ys + [ys[0]],
            linewidth=2.5, marker='o', markersize=3, color='#1482c5'
        )
        self.path_trace_done, = self.ax.plot(
            [], [], linewidth=2.5, marker='o', markersize=3, color='#1482c5'
        )
        self.ax.scatter(xs[0], ys[0], s=160, marker='o', color='dimgray')
        self.ax.text(xs[0] + 1, ys[0] + 2, 'START/END',
                     fontsize=10, fontweight='bold')

        # ── AP markers + coverage circles (all same light blue — like DASH) ──
        _AP_MARKER_COLORS = {
            'ap1': '#1f77b4',   # blue
            'ap2': '#ff7f0e',   # orange
            'ap3': '#2ca02c',   # green
            'ap4': '#d62728',   # red
        }
        for ap_name, cfg in self.ap_layout.items():
            apx, apy = cfg['x'], cfg['y']
            mcolor   = _AP_MARKER_COLORS[ap_name]
            # Coverage circle — ALL same skyblue (matches DASH)
            self.ax.add_patch(Circle(
                (apx, apy), radius=self.coverage_radius,
                fill=True, facecolor='skyblue', edgecolor='red',
                linewidth=2, alpha=0.18
            ))
            # AP square marker
            self.ax.scatter(apx, apy, s=140, marker='s', color=mcolor, zorder=5)
            # Label: "AP1 (R=300m)" — same format as DASH "RSU1 (R=300m)"
            self.ax.text(apx + 2, apy + 2,
                         f'{ap_name.upper()} (R={REAL_WIFI_RANGE_M}m)',
                         fontsize=10, fontweight='bold')

        # ── info text (top-left, same as DASH status bar) ─────────────────
        self.info_text = self.ax.text(
            0.02, 0.98,
            't=0.0s | speed=0.00 km/h | AP=N/A | car=(0,0)',
            transform=self.ax.transAxes, verticalalignment='top',
            fontsize=10, bbox=dict(boxstyle='round', alpha=0.3)
        )

        # ── Coverage label bottom-left (matches DASH visualize_topology.py) ─
        covered = 0
        for x, y in zip(xs, ys):
            for cfg in self.ap_layout.values():
                d = math.hypot(cfg['x'] - x, cfg['y'] - y)
                if d <= self.coverage_radius:
                    covered += 1
                    break
        pct = covered / len(xs) * 100
        self.ax.text(
            0.02, 0.02, f'Coverage: {pct:.0f}% of route',
            transform=self.ax.transAxes,
            fontsize=10, bbox=dict(boxstyle='round', alpha=0.3)
        )

        self.ax.set_title('Campus Route + AP Coverage (CDN Topology)', fontsize=12)
        self.ax.set_xlabel('X')
        self.ax.set_ylabel('Y')
        self.ax.grid(True)
        self.ax.set_aspect('equal', adjustable='box')

        ap_xs = [c['x'] for c in self.ap_layout.values()]
        ap_ys = [c['y'] for c in self.ap_layout.values()]
        r = self.coverage_radius
        self.ax.set_xlim(min(min(xs), min(ap_xs) - r) - 5,
                         max(max(xs), max(ap_xs) + r) + 5)
        self.ax.set_ylim(min(min(ys), min(ap_ys) - r) - 5,
                         max(max(ys), max(ap_ys) + r) + 5)

        try:
            self.fig.canvas.manager.set_window_title(
                'Campus Route + AP Coverage (CDN Topology)')
        except Exception:
            pass

        plt.show(block=False)
        plt.pause(0.1)

    def update(self, t, x, y, speed_kmh, current_ap):
        idx = min(range(len(self.road_positions)),
                  key=lambda i: (self.road_positions[i][1] - x) ** 2
                              + (self.road_positions[i][2] - y) ** 2)
        self.path_trace_done.set_data(self.all_x[:idx + 1], self.all_y[:idx + 1])
        self.info_text.set_text(
            f't={t:.1f}s | speed={speed_kmh:.2f} km/h | '
            f'AP={current_ap.upper()} | car=({x:.1f},{y:.1f})'
        )
        if self.fig:
            self.fig.canvas.draw_idle()
            plt.pause(0.05)

    def close(self):
        if self.fig:
            plt.ioff()
            plt.close('all')


# =========================================================
# MOBILITY REPLAY
# =========================================================
def move_car1_real_trace(car1, origin, edge_objs, ap_objs, live_plot=None,
                         step_scale=1.0, meas_log=None, on_handover=None):
    """
    Replay campus GPS trace.
    step_scale   : 1.0 = 30 km/h base; 0.5 = 60 km/h
    meas_log     : path for CDN latency CSV (None = no client measurements)
    on_handover  : optional callable(old_ap, new_ap) fired on each handover event
    """
    step_time_s       = 1.0 * step_scale
    last_valid_signal = None
    current_target    = None

    meas_fh = open(meas_log, 'w') if meas_log else None
    if meas_fh:
        meas_fh.write('time,x,y,ap,edge,edge_ip,video,cache_status,time_total_s,speed_bps\n')

    with open(RSSI_LOG_FILE, 'w') as f:
        f.write('time,x,y,target_ap,edge_server,edge_ip,'
                'ap_mac,signal_dBm,speed_kmh\n')

        prev_t, prev_x, prev_y = positions[0]
        start_target = target_ap_by_zone(prev_x, prev_y)

        # Initial association gets a much bigger retry budget than handovers:
        # it's a one-time cost per run, but if it never lands, every step on
        # this AP repeats the same doomed check (saw 12 consecutive failures
        # on ap1 before the first handover in practice).
        link_output = ensure_assoc(car1, ap_objs[start_target], retries=15, wait=1.0)
        flush_host_state(car1, origin, edge_objs)
        warmup_connectivity(car1)

        current_target    = start_target
        ap_mac, signal    = parse_link_info(link_output, last_valid_signal)
        last_valid_signal = signal
        car1.setPosition(f'{prev_x},{prev_y},0')

        edge_name = AP_EDGE_MAP[current_target]
        edge_ip   = EDGE_IPS[edge_name]

        if live_plot:
            live_plot.update(prev_t, prev_x, prev_y, 0.0, current_target)

        f.write(f'{prev_t},{prev_x},{prev_y},{current_target},'
                f'{edge_name},{edge_ip},{ap_mac},{signal},0.00\n')
        f.flush()

        for t, x, y in positions[1:]:
            time.sleep(max(0, (t - prev_t) * step_scale))
            car1.setPosition(f'{x},{y},0')

            speed_kmh = estimate_speed_kmh(prev_x, prev_y, x, y, step_time_s)
            target_ap = target_ap_by_zone(x, y)

            if target_ap != current_target:
                old_edge = AP_EDGE_MAP[current_target]
                new_edge = AP_EDGE_MAP[target_ap]
                info(f'*** Handover: {current_target} → {target_ap} '
                     f'({old_edge} → {new_edge})\n')
                link_output = ensure_assoc(car1, ap_objs[target_ap],
                                           retries=4, wait=0.8)
                if 'Connected to' not in link_output:
                    time.sleep(0.3)
                    link_output = ensure_assoc(car1, ap_objs[target_ap],
                                               retries=2, wait=0.5)
                flush_host_state(car1, origin, edge_objs)
                warmup_connectivity(car1)
                if on_handover:
                    on_handover(current_target, target_ap)
                current_target = target_ap
                time.sleep(HANDOVER_SETTLE_TIME)
            else:
                # No AP change: just refresh signal info. Re-running the full
                # ensure_assoc() retry loop here when 'Connected to' is absent
                # never actually helps (the AP hasn't changed, so there's
                # nothing to re-associate to) — it only burns ~4s per step
                # for no benefit, observed as 12 consecutive failures in a row
                # on the starting AP before the first handover.
                link_output = get_link_info(car1)

            ap_mac, signal    = parse_link_info(link_output, last_valid_signal)
            last_valid_signal = signal
            edge_name  = AP_EDGE_MAP[current_target]
            edge_ip    = EDGE_IPS[edge_name]

            info(f'*** car1 ({x:.1f},{y:.1f}) t={t:.0f}s | '
                 f'AP={current_target} | Edge={edge_name} ({edge_ip}) | '
                 f'sig={signal}dBm | v={speed_kmh:.1f}km/h\n')

            if live_plot:
                live_plot.update(t, x, y, speed_kmh, current_target)

            f.write(f'{t},{x},{y},{current_target},'
                    f'{edge_name},{edge_ip},{ap_mac},{signal},{speed_kmh:.2f}\n')
            f.flush()

            # CDN latency measurement (only when running automated experiment)
            if meas_fh:
                raw = car1.cmd(
                    f'curl -s -o /dev/null -r 0-1048576 -D - --max-time 3 '
                    f'-w "\\nTIME=%{{time_total}}\\nSPEED=%{{speed_download}}" '
                    f'http://{edge_ip}:{EDGE_PORT}/{CDN_VIDEO_PATH} 2>/dev/null'
                )
                cache_st  = 'UNKNOWN'
                time_s    = ''
                speed_bps = ''
                for line in raw.splitlines():
                    ll = line.lower()
                    if 'x-cache-status' in ll:
                        cache_st = line.split(':', 1)[-1].strip()
                    elif line.startswith('TIME='):
                        time_s = line[5:].strip()
                    elif line.startswith('SPEED='):
                        speed_bps = line[6:].strip()
                meas_fh.write(
                    f'{t},{x:.2f},{y:.2f},{current_target},'
                    f'{edge_name},{edge_ip},{CDN_VIDEO_PATH},'
                    f'{cache_st},{time_s},{speed_bps}\n'
                )
                meas_fh.flush()
                info(f'    [CDN] {cache_st} t={time_s}s spd={speed_bps}B/s\n')

            prev_t, prev_x, prev_y = t, x, y

        _, sx, sy = positions[0]
        car1.setPosition(f'{sx},{sy},0')
        if live_plot:
            live_plot.update(t + 1, sx, sy, 0.0, target_ap_by_zone(sx, sy))

    if meas_fh:
        meas_fh.close()
    info('*** Real campus mobility finished\n')
    if live_plot:
        live_plot.close()


# =========================================================
# MAIN TOPOLOGY
# =========================================================
def topology(args=None):
    # Write nginx configs before Mininet starts (host filesystem access)
    write_nginx_configs()

    net = Mininet_wifi(
        controller=RemoteController,
        link=wmediumd,
        wmediumd_mode=interference
    )

    info('*** Creating nodes\n')
    c0 = net.addController('c0', controller=RemoteController,
                           ip=CTRL_IP, port=CTRL_PORT)

    _, start_x, start_y = positions[0]

    # Mobile vehicle (OBU)
    car1 = net.addStation('car1', ip=f'{CAR_IP}/24',
                          mac='00:00:00:00:02:00',
                          position=f'{start_x},{start_y},0')

    # Origin server (cloud / remote, gets +200ms WAN delay)
    origin = net.addHost('origin', ip=f'{ORIGIN_IP}/24',
                         mac='00:00:00:00:00:ff')

    # 4 Edge servers — one per AP zone
    edge_objs = {}
    for idx, (name, ip) in enumerate(EDGE_IPS.items(), start=1):
        edge = net.addHost(name, ip=f'{ip}/24',
                           mac=f'00:00:00:00:00:{idx:02x}')
        edge_objs[name] = edge

    # 4 Access Points (802.11a, 5 GHz, non-overlapping channels — DASH-equivalent)
    ap_objs = {}
    for ap_name, cfg in AP_LAYOUT.items():
        ap = net.addAccessPoint(
            ap_name,
            ssid='cdn-vanet',
            mode=WIFI_MODE,
            channel=cfg['channel'],
            position=f"{cfg['x']},{cfg['y']},0",
            range=str(REAL_WIFI_RANGE_M),
            txpower=TX_POWER_DBM,
            protocols='OpenFlow13',
        )
        ap_objs[ap_name] = ap

    # Core SDN switch
    s1 = net.addSwitch('s1', cls=OVSKernelSwitch, protocols='OpenFlow13')

    net.setPropagationModel(model=PROPAGATION_MODEL, exp=PROPAGATION_EXP)

    info('*** Configuring WiFi nodes\n')
    net.configureWifiNodes()

    info('*** Creating wired backbone links (100 Mbps)\n')
    for ap in ap_objs.values():
        net.addLink(ap, s1, bw=BACKHAUL_BW_MBPS)
    net.addLink(s1, origin, bw=BACKHAUL_BW_MBPS)
    for edge in edge_objs.values():
        net.addLink(s1, edge, bw=BACKHAUL_BW_MBPS)

    info('*** Starting network\n')
    net.build()
    c0.start()
    for ap in ap_objs.values():
        ap.start([c0])
    s1.start([c0])

    # Bring up interfaces and add default routes
    car1.cmd('ip link set car1-wlan0 up')

    origin.cmd('ip link set origin-eth0 up')
    origin.cmd('ip route add default dev origin-eth0 2>/dev/null || true')

    for name, edge in edge_objs.items():
        edge.cmd(f'ip link set {name}-eth0 up')
        edge.cmd(f'ip route add default dev {name}-eth0 2>/dev/null || true')

    info('*** Waiting for Ryu controller to be ready...\n')
    ready = False
    for i in range(30):
        result = s1.cmd('ovs-vsctl get-controller s1 2>/dev/null')
        conn   = s1.cmd('ovs-vsctl get controller s1 is_connected 2>/dev/null').strip()
        if 'tcp:127.0.0.1:6653' in result and conn == 'true':
            info(f'*** Ryu controller connected after {i+1}s\n')
            ready = True
            break
        time.sleep(1)
    if not ready:
        info('*** WARNING: Ryu may not be fully ready, continuing anyway\n')
    time.sleep(2)

    install_fallback_flows(list(ap_objs.values()), [s1])
    set_static_arp(car1, origin, edge_objs)

    # Start services
    start_nginx_origin(origin)
    start_nginx_edges(edge_objs)

    verify_connectivity(car1, edge_objs)

    # No prewarm: cache starts cold on every edge. proxy_cache_min_uses=2
    # means each edge naturally becomes a HIT after its 2nd request during
    # the drive itself — saves ~4 min/run of full-file warmup fetches.
    add_wan_delay(origin)

    time.sleep(1)

    # Resolve situation config from args
    sit        = getattr(args, 'sit',        1)
    speed_ovr  = getattr(args, 'speed',      None)   # optional CLI override
    run_id     = getattr(args, 'run_id',     f'cdn_sit{sit}_r1')
    out_dir    = getattr(args, 'out_dir',    '.')
    no_gui     = getattr(args, 'no_gui',     False)
    run_client = getattr(args, 'run_client', False)

    cfg        = _SIT_CONFIG.get(sit, _SIT_CONFIG[1])
    throttle   = cfg['throttle']
    speed_kmh  = speed_ovr if speed_ovr else cfg['speed_kmh']
    step_scale = _BASE_SPEED_KMH / speed_kmh

    os.makedirs(out_dir, exist_ok=True)
    meas_log = os.path.join(out_dir, f'cdn_measurements_{run_id}.csv') if run_client else None

    info(f'*** Situation {sit}: {cfg["desc"]}\n')
    info(f'*** speed={speed_kmh} km/h (step_scale={step_scale:.2f}) | throttle={throttle}\n')

    # Start bandwidth throttle scheduler (background thread, non-blocking)
    if throttle == 'handover_250k':
        # Applied per-handover inside start_mobility — handled via on_handover callback
        pass
    else:
        start_throttle_scheduler(edge_objs, throttle)

    if not no_gui:
        try:
            max_x = max(pos[1] for pos in positions) + 30
            max_y = max(pos[2] for pos in positions) + 30
            net.plotGraph(max_x=max_x, max_y=max_y)
        except Exception as e:
            info(f'*** plotGraph warning: {e}\n')
        disable_mn_wifi_graph_updates(car1)
        live_plot = RealRoadLivePlot(positions, AP_LAYOUT,
                                     coverage_radius=PLOT_COVERAGE_RADIUS)
        live_plot.setup()
    else:
        live_plot = None

    # Handover throttle callback for sit 3 (250 kbps for 30 s at each handover)
    def _on_handover_250k(old_ap, new_ap):
        info(f'*** [BW] Handover {old_ap}→{new_ap}: throttle 250 kbps for 30 s\n')
        set_bw_all_edges(edge_objs, 250)
        threading.Timer(30, lambda: set_bw_all_edges(edge_objs, None)).start()

    on_handover_cb = _on_handover_250k if throttle == 'handover_250k' else None

    def start_mobility():
        info('*** Starting real-campus CDN mobility trace\n')

        # =====================================================================
        # CRITICAL WORKAROUND for Mininet-WiFi initial-AP bug
        # ---------------------------------------------------------------------
        # When the topology starts, car1 auto-associates with the nearest AP
        # but the SDN controller has not yet seen any Packet-In from car1, so
        # no learned flow exists for car1's MAC. Subsequent setAssociation()
        # calls return "already connected!" and become no-ops, so the issue
        # never self-corrects.
        #
        # Workaround: BEFORE the real mobility starts, walk car1 through each
        # AP zone once. This forces real associations and installs flow rules
        # end-to-end. After this warmup, the actual mobility trace runs cleanly.
        # =====================================================================
        info('*** [WARMUP] Pre-touching every AP to prime SDN flows\n')
        ap_centers = [
            ('ap1', AP_LAYOUT['ap1']['x'], AP_LAYOUT['ap1']['y']),
            ('ap2', AP_LAYOUT['ap2']['x'], AP_LAYOUT['ap2']['y']),
            ('ap3', AP_LAYOUT['ap3']['x'], AP_LAYOUT['ap3']['y']),
            ('ap4', AP_LAYOUT['ap4']['x'], AP_LAYOUT['ap4']['y']),
        ]
        for ap_name, cx, cy in ap_centers:
            info(f'*** [WARMUP] Touching {ap_name} at ({cx},{cy})\n')
            car1.setPosition(f'{cx},{cy},0')
            time.sleep(0.3)
            try:
                car1.setAssociation(ap_objs[ap_name], intf='car1-wlan0')
            except Exception:
                pass
            time.sleep(0.8)
            edge_ip = EDGE_IPS[AP_EDGE_MAP[ap_name]]
            car1.cmd(f'ping -c 1 -W 1 {edge_ip} > /dev/null 2>&1')
            car1.cmd(f'curl -s -o /dev/null --max-time 2 '
                     f'http://{edge_ip}:{EDGE_PORT}/ > /dev/null 2>&1')

        _, sx, sy = positions[0]
        car1.setPosition(f'{sx},{sy},0')
        time.sleep(0.5)
        info('*** [WARMUP] Done. Starting real mobility trace.\n')

        move_car1_real_trace(car1, origin, edge_objs, ap_objs, live_plot,
                             step_scale=step_scale, meas_log=meas_log,
                             on_handover=on_handover_cb)

    net.start_mobility = start_mobility

    if run_client:
        # Automated experiment mode: run mobility + measurements, then exit
        info('*** [AUTO] Running mobility trace with client measurements...\n')
        start_mobility()
        # Copy RSSI log to results dir
        rssi_dst = os.path.join(out_dir, f'rssi_{run_id}.csv')
        try:
            import shutil
            shutil.copy(RSSI_LOG_FILE, rssi_dst)
        except Exception:
            pass
        info(f'*** Results saved to {out_dir}/\n')
    else:
        # Interactive mode — drop into Mininet CLI
        info('*** =====================================================\n')
        info('*** CDN topology ready. CLI:\n')
        info('***   py net.start_mobility()\n')
        info('***\n')
        info('***   [HIT - Video.mp4 cached]\n')
        info(f'***   car1 curl -s -o /dev/null -r 0-1 -D - '
             f'-w "time=%{{time_total}}s\\n" '
             f'http://10.0.0.11:{EDGE_PORT}/{CDN_VIDEO_PATH} '
             '| grep -iE "x-cache|x-edge|time="\n')
        info('***\n')
        info('***   [MISS - Video2.mp4 not cached]\n')
        info(f'***   car1 curl -s -o /dev/null -r 0-1 -D - '
             f'-w "time=%{{time_total}}s\\n" '
             f'http://10.0.0.11:{EDGE_PORT}/{CDN_VIDEO2_PATH} '
             '| grep -iE "x-cache|x-edge|time="\n')
        info('***\n')
        info('***   [Clear edge1 cache]\n')
        info(f'***   edge1 rm -rf {CDN_CACHE_BASE}/edge1_cache/* && '
             'edge1 nginx -c /tmp/nginx_edge1.conf -s reload\n')
        info('*** =====================================================\n')
        CLI(net)

    info('*** Stopping network\n')
    net.stop()


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Cooperative Edge CDN topology')
    ap.add_argument('--sit',   type=int, default=1,
                    choices=[1, 2, 3, 4, 5, 6],
                    help='Situation 1-6 (see _SIT_CONFIG)')
    ap.add_argument('--speed', type=int, default=None, dest='speed',
                    help='Vehicle speed km/h (overrides situation default)')
    ap.add_argument('--round', type=int, default=1, dest='round',
                    help='Repetition round number')
    ap.add_argument('--run-id', default=None, dest='run_id',
                    help='Experiment run ID (auto-derived if omitted)')
    ap.add_argument('--out-dir', default='.', dest='out_dir',
                    help='Directory for result CSVs')
    ap.add_argument('--run-client', action='store_true', dest='run_client',
                    help='Auto-run mobility + client measurements (no CLI)')
    ap.add_argument('--no-gui', action='store_true', dest='no_gui',
                    help='Skip matplotlib live plot')
    args = ap.parse_args()

    if args.run_id is None:
        cfg   = _SIT_CONFIG.get(args.sit, _SIT_CONFIG[1])
        speed = args.speed if args.speed else cfg['speed_kmh']
        args.run_id = f'cdn_sit{args.sit}_spd{speed}_r{args.round}'

    setLogLevel('info')
    topology(args)