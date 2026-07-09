#!/usr/bin/env python3
"""
============================================================================
  cdn_sdn_multi_car.py -- Situation 1 (Traffic Density), SDN+CDN arm
----------------------------------------------------------------------------
  N vehicles (3 / 5 / 7, --cars) drive in a fixed-gap platoon (10 m,
  20 km/h car-following model) around the SAME real PSU-Phuket campus loop
  route and 4-RSU layout as the SDN+DASH arm (Situation1_DASH/), so the two
  arms face an identical mobility + wireless stimulus -- only the delivery
  architecture (CDN edge caching vs. DASH ABR) differs. This is the direct
  CDN counterpart of Situation1_DASH/platoon_topology.py: same platoon
  mobility, same zone-based handover, same hybrid step2h + AP-contention
  bandwidth model, same real PSU-Phuket loop -- NOT the straight-line 4-AP
  layout the single-vehicle CDN_baseline/ scripts use.

  What's reused (imported only, never modified):
    - Situation1_DASH/campus_config.py: RSU_LAYOUT, the real loop route,
      platoon constants, Step2HysteresisMapper, target_rsu_by_zone(),
      parse_link_info(), vehicle_position(). This is itself Situation1_DASH's
      own re-export of the frozen Phase-1 baseline's geometry -- reusing it
      here (rather than copying) means any future fix to the shared route/
      RSU layout propagates to both arms automatically.
    - CDN_baseline/cdn_baseline_topo.py: write_nginx_configs(), setup_tc(),
      measure_cdn(), EDGE_PORTS -- all already station-agnostic (they
      operate on the shared origin/edge server or a single RSU, not on any
      specific vehicle). NOTE: set_tc() itself is deliberately NOT reused --
      it drives a single-class HTB tree on the origin server's own egress,
      the straight-line single-vehicle scenario's bandwidth mechanism.
      PlatoonThrottleController below drives its own multi-class,
      per-RSU/per-vehicle HTB tree directly instead, same design as
      Situation1_DASH's version.
    - CDN_baseline/cdn_baseline_topo_sdn.py: vlc_start()/vlc_switch()/
      vlc_stop() (take the target car as a parameter already, no hardcoded
      station name), cooperative_warm()/_wait_for_coop_warm() (scoped by
      RSU index, not by car).
    - CDN_baseline/config.py: CONTENT_DIR, VIDEO_HIT/VIDEO_MISS, ORIGIN_PORT.
    - CDN_baseline/baseline_model.py: cdn_qoe()/compute_cdn_qoe() for
      post-hoc analysis (see the "why post-hoc" note in run_platoon()).

  What's reimplemented here (NOT imported), and why:
    - ensure_assoc()/flush_host_state()/warmup_connectivity(): CDN_baseline's
      versions hard-code the interface name 'car1-wlan0' and host name
      'server1' internally (single-vehicle scripts), so they only work for
      exactly one station literally named car1 -- same issue
      Situation1_DASH/campus_config.py's header flags about dash_topology.py.
      Reimplemented here, parameterised by car.name, same principle.
    - ensure_assoc() ALSO fixes a real bug found and verified this session
      (see its docstring): mn_wifi's own association path (setAssociation()
      -> iw_connect()) never passes a frequency to `iw connect`, so a
      station's simulated radio never retunes off its first AP's channel on
      handover -- confirmed via CDN_baseline's single-vehicle 4-AP scenario
      (live RSSI never recovered past AP1 until this was fixed). This
      reimplementation issues `iw connect <ssid> <freq-MHz> <bssid>`
      directly, same fix, not present in Situation1_DASH's own ensure_assoc()
      (worth flagging to that teammate separately, not this file's job to
      fix their code).
    - PlatoonThrottleController: architecturally identical to
      Situation1_DASH's version (same per-RSU HTB tree, same hybrid
      step2h + AP_CAPACITY_MBPS/n_active contention-sharing design) --
      it's genuinely delivery-architecture-agnostic (pure wireless
      bandwidth modelling), so reusing the exact same design (not
      copy-pasting the DASH-specific parts, there aren't any) is what makes
      this a fair comparison. Reimplemented (not imported) only because
      Situation1_DASH doesn't currently expose it as an importable/reusable
      class from outside its own module.
    - VehiclePacketLossPoller: same per-vehicle ICMP poller pattern as
      Situation1_DASH's version; CDN_baseline's own PingLossPoller hard-codes
      a single /tmp path, so it needs the same per-vehicle-path
      reimplementation Situation1_DASH already had to do.

  Usage:
    sudo python3 cdn_sdn_multi_car.py --cars 3 --run-id case1_3cars
    sudo python3 cdn_sdn_multi_car.py --cars 5 --cli     # interactive debug
============================================================================
"""

import os
import sys
import csv
import time
import threading
import argparse

from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel, info
from mn_wifi.net import Mininet_wifi
from mn_wifi.node import OVSKernelAP
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, '..'))

# Reuse CDN_baseline's station-agnostic server-side helpers + config.
#
# IMPORT ORDER MATTERS HERE: this must come BEFORE campus_config is
# imported below. Both dash-baseline/baseline_model.py and
# CDN_baseline/baseline_model.py are importable under the exact same bare
# module name ("baseline_model"), and campus_config.py's own
# `from baseline_model import Step2HysteresisMapper` does a bare import
# too -- Python caches modules by name in sys.modules, so whichever
# baseline_model.py loads FIRST silently wins for the rest of the process,
# including inside cdn_baseline_topo.py's own `import baseline_model as M`.
# Importing CDN_baseline's version first is the safe direction: it's a
# strict superset of dash-baseline's for the parts campus_config actually
# needs (Step2HysteresisMapper verified byte-identical between the two
# earlier this session), so campus_config transparently reuses the
# already-cached CDN_baseline module instead of loading its own -- the
# reverse order breaks cdn_baseline_topo.py instead (AttributeError on
# CDN-only constants like AP_COVERAGE that dash-baseline's copy lacks).
sys.path.insert(0, os.path.join(_REPO_ROOT, 'CDN_baseline'))
import config as CDN_CFG
import baseline_model as CDN_M
from cdn_baseline_topo import (
    write_nginx_configs, setup_tc, measure_cdn, EDGE_PORTS,
)
from cdn_baseline_topo_sdn import (
    vlc_switch, VlcTelemetryPoller, VLC_PLAYER_SCRIPT, _vlc_paths,
    cooperative_warm, _wait_for_coop_warm, mininet_cleanup_preserving_ryu,
)
# vlc_start()/vlc_stop() themselves are NOT imported (see vlc_start()/
# vlc_stop() below, reimplemented here) -- their pkill pattern matches
# `vlc_player.py` process-wide. Mininet hosts share the host PID namespace
# (only network namespaces differ per host), so a bare `pkill -f
# vlc_player.py` run from car2's netns still matches and kills car1's
# already-running vlc_player.py process, and so on for every later car's
# vlc_start()/vlc_stop() call -- fine for exactly one vehicle, silently
# breaks every earlier vehicle's playback with more than one. vlc_switch()
# itself has no such issue (it only ever touches its own run_id's control
# file), so it's safe to reuse as-is.

# Reuse Situation1_DASH's shared geometry/platoon config (see module
# docstring) -- read/import only, never modified. Must come AFTER the
# CDN_baseline imports above -- see the import-order note there.
sys.path.insert(0, os.path.join(_REPO_ROOT, 'Situation1_DASH'))
import campus_config as C

VIDEO_FILE = CDN_CFG.VIDEO_HIT   # popular content (always HIT once warm) --
                                  # this scenario tests density, not content
                                  # popularity, so sit2/MISS isn't wired up
HANDOVER_SETTLE_S = 0.60

# Stable rsu-name -> edge-port index mapping (rsu1->EDGE_PORTS[0]=8081, ...),
# fixed at RSU_LAYOUT's own declared order rather than re-derived from
# rsu_objs.keys() at each call site.
RSU_NAMES = list(C.RSU_LAYOUT.keys())


def rsu_edge_idx(rsu_name):
    return RSU_NAMES.index(rsu_name)


# ===========================================================================
#  Per-vehicle helpers (reimplemented, NOT imported -- see module docstring)
# ===========================================================================
def ensure_assoc(car, rsu, retries=6, wait=1.0):
    """Associate car with target rsu, retuning the radio to rsu's own
    channel explicitly (see module docstring for why: mn_wifi's own
    setAssociation()/iw_connect() never does this, so a station stays
    parked on whichever AP it associated with FIRST for the entire run
    otherwise -- verified against the single-vehicle CDN_baseline scenario
    earlier this session). Bypasses car.setAssociation() entirely rather
    than trying to patch around it.
    """
    intf = f'{car.name}-wlan0'
    rsu_intf = rsu.wintfs[0]
    bssid = rsu_intf.mac
    freq_mhz = rsu_intf.format_freq()
    ssid = rsu_intf.ssid
    last_output = ''
    for attempt in range(1, retries + 1):
        try:
            car.cmd(f'iw dev {intf} disconnect')
            out = car.cmd(f'iw dev {intf} connect {ssid} {freq_mhz} {bssid}')
            if out and out.strip():
                info(f'*** [{car.name}] iw connect -> {out.strip()}\n')
        except Exception as e:
            info(f'*** [{car.name}] iw connect warning: {e}\n')
        time.sleep(wait)
        last_output = car.cmd(f'iw dev {intf} link')
        if 'Connected to' in last_output:
            car.getNameToWintf(intf).associatedTo = rsu_intf
            return last_output
        info(f'*** [{car.name}] association attempt {attempt} to {rsu.name} failed\n')
    info(f'*** [{car.name}] failed to associate with {rsu.name}\n')
    return last_output


def flush_host_state(car, server):
    intf = f'{car.name}-wlan0'
    car.cmd(f'ip neigh flush dev {intf}')
    car.cmd('ip route flush cache')
    server.cmd('ip neigh flush dev server-eth0')
    server.cmd('ip route flush cache')


def warmup_connectivity(car, server):
    intf = f'{car.name}-wlan0'
    car.cmd(f'arping -c 2 -I {intf} {C.SERVER_IP} > /dev/null 2>&1')
    car.cmd(f'ping -c 2 -W 1 {C.SERVER_IP} > /dev/null 2>&1')


def vlc_start(car, out_dir, run_id, initial_url, show=False):
    """Per-vehicle-scoped reimplementation of
    cdn_baseline_topo_sdn.vlc_start() -- see the import comment above for
    why the original isn't safe to call directly for more than one car.
    pkill is filtered on this run_id's own vlc_player.py invocation
    (--run-id run_id is part of the command line every instance is
    launched with, so the pattern is unambiguous per vehicle) instead of
    matching every vlc_player.py process on the host.
    """
    paths = _vlc_paths(out_dir, run_id)
    car.cmd(f"pkill -f 'vlc_player.py .*--run-id {run_id}( |$)' 2>/dev/null; true")
    if os.path.exists(paths['ctrl']):
        os.remove(paths['ctrl'])
    show_flag = '--show' if show else ''
    env_prefix = 'DISPLAY=%s ' % os.environ['DISPLAY'] if show and os.environ.get('DISPLAY') else ''
    if show and not os.environ.get('DISPLAY'):
        info(f'*** [VLC] WARNING: --vlc-show requested but no DISPLAY set '
             f'in this shell — {car.name}\'s video window will likely fail to open\n')
    info('*** [VLC] Starting real playback on %s%s: %s\n'
         % (car.name, ' (with video window)' if show else '', initial_url))
    car.cmd(
        '%spython3 %s --run-id %s --initial-ap 1 --initial-url %s '
        '--ctrl-file %s --telemetry-csv %s --events-csv %s %s '
        '> %s 2>&1 &'
        % (env_prefix, VLC_PLAYER_SCRIPT, run_id, initial_url,
           paths['ctrl'], paths['tel'], paths['evt'], show_flag, paths['log'])
    )
    time.sleep(0.3)
    return paths


def vlc_stop(car, run_id):
    """Per-vehicle-scoped reimplementation of
    cdn_baseline_topo_sdn.vlc_stop() -- see vlc_start() above for why."""
    car.cmd(f"pkill -TERM -f 'vlc_player.py .*--run-id {run_id}( |$)' 2>/dev/null; true")
    time.sleep(0.5)


# ===========================================================================
#  Per-vehicle ICMP loss poller (CDN_baseline's PingLossPoller hard-codes a
#  single /tmp path -- same reimplementation Situation1_DASH already needed)
# ===========================================================================
class VehiclePacketLossPoller:
    def __init__(self, car, server_ip, log_path):
        self.car = car
        self.server_ip = server_ip
        self.log_path = log_path
        self._pos = 0
        self._started = False

    def start(self):
        self.car.cmd(f'rm -f {self.log_path}')
        intf = f'{self.car.name}-wlan0'
        self.car.cmd(
            f'ping -O -i 1 -I {intf} {self.server_ip} > {self.log_path} 2>&1 &'
        )
        self._started = True

    def poll(self):
        if not self._started or not os.path.exists(self.log_path):
            return 0.0
        with open(self.log_path) as f:
            f.seek(self._pos)
            chunk = f.read()
            self._pos = f.tell()
        replies = chunk.count('bytes from')
        drops = chunk.count('no answer yet')
        total = replies + drops
        return round((100.0 * drops / total), 2) if total else 0.0


# ===========================================================================
#  Live topology plot -- custom-styled to match CDN/topology/
#  real_campus_live.py's RealRoadLivePlot (road-shaped route, RSU coverage
#  circles, branded info box), NOT mn_wifi's own net.plotGraph() (plain
#  generic dots/labels -- that's what looked wrong). Adapted for N
#  simultaneous vehicles: real_campus_live.py draws ONE growing trail for
#  its single car; with a tightly-packed platoon of up to 7 cars, N
#  overlapping trails would just be visual noise, so this draws the route
#  once as a static background line and gives each vehicle its own
#  distinctly-coloured live position marker instead (also fixes a gap in
#  the single-car reference: it declares self.car_marker but never actually
#  uses it, so today it has no visible "current position" dot at all,
#  only the trailing line -- see the reference image this was built from).
# ===========================================================================
class MultiCarLivePlot:
    _CAR_COLORS = ['#1482c5', '#e67e22', '#27ae60', '#c0392b',
                   '#8e44ad', '#16a085', '#e84393']
    _RSU_MARKER_COLORS = {
        'rsu1': '#1f77b4', 'rsu2': '#ff7f0e',
        'rsu3': '#2ca02c', 'rsu4': '#d62728',
    }

    def __init__(self, road_positions, rsu_layout, n_cars, wifi_range_m,
                 coverage_radius=62):
        self.road_positions = road_positions
        self.rsu_layout = rsu_layout
        self.n_cars = n_cars
        self.wifi_range_m = wifi_range_m
        self.coverage_radius = coverage_radius   # drawn circle size -- a
                                                    # scaled-down stand-in
                                                    # for wifi_range_m, same
                                                    # reason real_campus_
                                                    # live.py's own circles
                                                    # aren't drawn to true
                                                    # scale (300m circles on
                                                    # a ~200-unit-wide plot
                                                    # would just be one big
                                                    # overlapping blob)
        self.fig = self.ax = self.info_text = None
        self.car_markers = []
        self.all_x = [pos[1] for pos in road_positions]
        self.all_y = [pos[2] for pos in road_positions]

    def setup(self):
        import matplotlib
        matplotlib.use('TkAgg')
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle
        self._plt = plt

        plt.style.use('default')
        plt.ion()
        self.fig, self.ax = plt.subplots(figsize=(10, 8))
        xs, ys = self.all_x, self.all_y

        # Static road (background reference line -- same visual style as
        # real_campus_live.py's path_trace_future, just left undrawn-over
        # rather than growing, per the class docstring above)
        self.ax.plot(xs + [xs[0]], ys + [ys[0]],
                     linewidth=2.5, marker='o', markersize=3,
                     color='#1482c5', alpha=0.35, zorder=1)
        self.ax.scatter(xs[0], ys[0], s=160, marker='o', color='dimgray', zorder=2)
        self.ax.text(xs[0] + 1, ys[0] + 2, 'START/END',
                     fontsize=10, fontweight='bold')

        for rsu_name, cfg in self.rsu_layout.items():
            rx, ry = cfg['x'], cfg['y']
            mcolor = self._RSU_MARKER_COLORS.get(rsu_name, '#333333')
            self.ax.add_patch(Circle(
                (rx, ry), radius=self.coverage_radius,
                fill=True, facecolor='skyblue', edgecolor='red',
                linewidth=2, alpha=0.18, zorder=1))
            self.ax.scatter(rx, ry, s=140, marker='s', color=mcolor, zorder=5)
            self.ax.text(rx + 2, ry + 2,
                         f'{rsu_name.upper()} (R={self.wifi_range_m:.0f}m)',
                         fontsize=10, fontweight='bold')

        for i in range(self.n_cars):
            color = self._CAR_COLORS[i % len(self._CAR_COLORS)]
            marker = self.ax.scatter([], [], s=130, marker='o',
                                     color=color, edgecolors='black',
                                     linewidths=1, zorder=10,
                                     label=f'car{i+1}')
            self.car_markers.append(marker)
        self.ax.legend(loc='upper right', fontsize=8, framealpha=0.85,
                       ncol=min(self.n_cars, 4))

        self.info_text = self.ax.text(
            0.02, 0.98, 't=0.0s', transform=self.ax.transAxes,
            verticalalignment='top', fontsize=9,
            bbox=dict(boxstyle='round', alpha=0.3))

        self.ax.set_title(
            f'Situation 1: Traffic Density — {self.n_cars} cars (SDN+CDN)',
            fontsize=12)
        self.ax.set_xlabel('X (m)')
        self.ax.set_ylabel('Y (m)')
        self.ax.grid(True)
        self.ax.set_aspect('equal', adjustable='box')

        rsu_xs = [c['x'] for c in self.rsu_layout.values()]
        rsu_ys = [c['y'] for c in self.rsu_layout.values()]
        r = self.coverage_radius
        self.ax.set_xlim(min(min(xs), min(rsu_xs) - r) - 5,
                         max(max(xs), max(rsu_xs) + r) + 5)
        self.ax.set_ylim(min(min(ys), min(rsu_ys) - r) - 5,
                         max(max(ys), max(rsu_ys) + r) + 5)

        try:
            self.fig.canvas.manager.set_window_title(
                f'Situation 1: Traffic Density — {self.n_cars} cars (SDN+CDN)')
        except Exception:
            pass

        plt.show(block=False)
        plt.pause(0.1)

    def update(self, t, car_states):
        """car_states: list of (x, y, rsu_name), one per car, same order
        as car_markers (i.e. car_states[i] is carN's state, N=i+1)."""
        for i, (x, y, _rsu) in enumerate(car_states):
            self.car_markers[i].set_offsets([[x, y]])
        summary = '  '.join(
            f'car{i+1}={rsu.upper()}' for i, (_, _, rsu) in enumerate(car_states))
        self.info_text.set_text(f't={t:.1f}s | {summary}')
        if self.fig:
            self.fig.canvas.draw_idle()
            self._plt.pause(0.05)

    def close(self):
        if self.fig:
            self._plt.ioff()
            self._plt.close('all')


# ===========================================================================
#  Hybrid bandwidth model: step2h (per-vehicle RSSI tiering) + RSU contention
#  -- architecturally identical to Situation1_DASH's PlatoonThrottleController
#  (see module docstring: this is delivery-architecture-agnostic, so reusing
#  the exact same model is what keeps the DASH vs CDN comparison fair).
# ===========================================================================
class PlatoonThrottleController:
    IDLE_RATE_MBPS = 0.5   # placeholder for a vehicle's class on an RSU it
                            # isn't currently associated with -- no traffic
                            # ever matches it, kept low just so HTB's rate
                            # bookkeeping stays sane

    def __init__(self, rsu_objs, n_cars):
        self.rsu_objs = rsu_objs
        self.n_cars = n_cars
        self.mappers = [C.Step2HysteresisMapper() for _ in range(n_cars)]
        self.cur_rate = [None] * n_cars
        self.car_state = {}          # i -> dict(rsu=, rssi=)
        self._lock = threading.Lock()
        self._stop = threading.Event()

    @staticmethod
    def _iface(rsu_name):
        return f'{rsu_name}-wlan1'

    def setup(self):
        for rsu_name, rsu in self.rsu_objs.items():
            iface = self._iface(rsu_name)
            rsu.cmd(f'tc qdisc del dev {iface} root 2>/dev/null')
            rsu.cmd(f'tc qdisc add dev {iface} root handle 1: htb default 999')
            rsu.cmd(f'tc class add dev {iface} parent 1: classid 1:1 htb '
                    f'rate {C.AP_CAPACITY_MBPS}mbit ceil {C.AP_CAPACITY_MBPS}mbit')
            for i in range(self.n_cars):
                classid = f'1:{10 + i}'
                ip = C.car_ip(i + 1)
                rsu.cmd(f'tc class add dev {iface} parent 1:1 classid {classid} '
                        f'htb rate {self.IDLE_RATE_MBPS}mbit '
                        f'ceil {self.IDLE_RATE_MBPS}mbit')
                rsu.cmd(f'tc qdisc add dev {iface} parent {classid} '
                        f'handle {100 + i}: sfq perturb 10')
                rsu.cmd(f'tc filter add dev {iface} protocol ip parent 1: '
                        f'prio 1 u32 match ip dst {ip}/32 flowid {classid}')
            rsu.cmd(f'tc class add dev {iface} parent 1:1 classid 1:999 htb '
                    f'rate 0.1mbit ceil {C.AP_CAPACITY_MBPS}mbit')

    def update_car_state(self, i, rsu_name, rssi):
        with self._lock:
            self.car_state[i] = {'rsu': rsu_name, 'rssi': rssi}

    def get_rate(self, i):
        return self.cur_rate[i]

    def _recompute_and_apply(self):
        with self._lock:
            state = dict(self.car_state)
        if len(state) < self.n_cars:
            return

        counts = {}
        for st in state.values():
            counts[st['rsu']] = counts.get(st['rsu'], 0) + 1

        for i, st in state.items():
            rsu_name = st['rsu']
            step2h_rate = self.mappers[i].update(st['rssi'])
            fair_share = C.AP_CAPACITY_MBPS / counts[rsu_name]
            rate = max(0.1, min(step2h_rate, fair_share))
            if self.cur_rate[i] is None or abs(rate - self.cur_rate[i]) > 0.05:
                iface = self._iface(rsu_name)
                classid = f'1:{10 + i}'
                # ceil == rate (no HTB borrowing): a vehicle must not burst
                # past its currently computed fair share just because a
                # platoon-mate is momentarily idle -- otherwise the density
                # effect this scenario measures gets diluted by burst timing.
                self.rsu_objs[rsu_name].cmd(
                    f'tc class change dev {iface} parent 1:1 classid {classid} '
                    f'htb rate {rate:.3f}mbit ceil {rate:.3f}mbit')
            self.cur_rate[i] = rate

    def _run_loop(self):
        while not self._stop.is_set():
            self._recompute_and_apply()
            time.sleep(C.SAMPLE_DT_S)

    def start(self):
        self.setup()
        t = threading.Thread(target=self._run_loop, daemon=True)
        t.start()

    def stop(self):
        self._stop.set()


# ===========================================================================
#  Platoon mobility + handover + per-vehicle CDN measurement + logging
# ===========================================================================
def run_platoon(cars, server, rsu_objs, throttle, vlc_paths_list, out_dir,
                 run_id, n_cars, total_t, live_plot=None):
    """Drive the whole platoon for total_t seconds, measuring CDN cache/
    latency + real VLC telemetry for every vehicle every SAMPLE_DT_S.

    No 'qoe' column here on purpose, same reasoning as the single-vehicle
    CDN_baseline scripts: raw signals only (cache/latency/stall), QoE
    derived post-hoc via baseline_model.compute_cdn_qoe() on the saved CSV
    -- a formula change never requires re-running a 3/5/7-car mininet-wifi
    scenario (expensive), just recomputing from the CSV already on disk.

    Note on tick timing: unlike the single-vehicle CDN_baseline scripts
    (which track total_paused and subtract it from drive-time so the
    vehicle's simulated position doesn't advance during a handover pause),
    this loop uses plain wall-clock t = time.time() - t0 throughout, same
    as Situation1_DASH/platoon_topology.py's own run_platoon(). A car mid-
    handover this tick can make the inner per-vehicle loop take a few real
    seconds (mostly cooperative-warm's ~3s wait), which shows up as a
    correspondingly larger position jump for every vehicle on the *next*
    tick rather than a paused clock. Deliberately not fixed here: matching
    the DASH arm's own timing behaviour exactly is more important than
    physical precision for this comparison to be fair -- adding
    total_paused tracking to only one arm would itself be an unfairness.

    live_plot, if given (a MultiCarLivePlot, already .setup()), gets
    .update(t, car_states) called once per tick -- see that class for why
    a plain net.plotGraph() isn't used.
    """
    last_signal = [-50] * n_cars
    current_rsu = [None] * n_cars
    loss_pollers = []
    vlc_tel = []
    net_rows = [[] for _ in range(n_cars)]

    # ---- initial placement + association (t = 0) -------------------------
    for i, car in enumerate(cars):
        x, y = C.vehicle_position(i, 0.0)
        car.setPosition(f'{x},{y},0')
        rsu_name = C.target_rsu_by_zone(x, y)
        link_out = ensure_assoc(car, rsu_objs[rsu_name], retries=6, wait=1.0)
        if 'Connected to' not in link_out:
            info(f'*** [{car.name}] WARNING: not associated with '
                 f'{rsu_name} at t=0 after retries\n')
        flush_host_state(car, server)
        warmup_connectivity(car, server)
        _, sig = C.parse_link_info(link_out, last_signal[i])
        last_signal[i] = sig
        current_rsu[i] = rsu_name
        throttle.update_car_state(i, rsu_name, sig)

        poller = VehiclePacketLossPoller(
            car, C.SERVER_IP, f'/tmp/ping_{run_id}_car{i+1}.log')
        poller.start()
        loss_pollers.append(poller)
        vlc_tel.append(VlcTelemetryPoller(vlc_paths_list[i]['tel']))

        info(f'*** [{car.name}] initial position ({x:.0f},{y:.0f}) '
             f'-> {rsu_name} sig={sig}dBm\n')

    info(f'*** Platoon of {n_cars} vehicles moving for ~{total_t:.0f}s '
         f'(10 m gap, {C.SPEED_KMH:.0f} km/h)\n')

    t0 = time.time()
    while True:
        t = time.time() - t0
        if t >= total_t:
            break
        time.sleep(C.SAMPLE_DT_S)
        t = time.time() - t0

        for i, car in enumerate(cars):
            x, y = C.vehicle_position(i, t)
            car.setPosition(f'{x},{y},0')
            target_rsu = C.target_rsu_by_zone(x, y)
            handover = 0

            if target_rsu != current_rsu[i]:
                handover = 1
                info(f'*** [{car.name}] handover: '
                     f'{current_rsu[i]} -> {target_rsu}\n')
                # Pre-warm the target edge via the cooperative channel
                # BEFORE the (slow, ~1-3s) wifi reassociation completes --
                # same "warm ahead of arrival" pattern as the single-vehicle
                # SDN+CDN scenario, just triggered per-vehicle here. Harmless
                # if several platoon-mates target the same RSU at once (the
                # warm curl is idempotent -- re-warming an already-hot cache
                # is just another HIT).
                target_idx = rsu_edge_idx(target_rsu)
                cooperative_warm(server, target_idx, VIDEO_FILE, block=False)
                link_out = ensure_assoc(car, rsu_objs[target_rsu],
                                         retries=6, wait=1.0)
                if 'Connected to' not in link_out:
                    time.sleep(0.3)
                    link_out = ensure_assoc(car, rsu_objs[target_rsu],
                                             retries=3, wait=0.6)
                if 'Connected to' in link_out:
                    flush_host_state(car, server)
                    warmup_connectivity(car, server)
                    current_rsu[i] = target_rsu
                    time.sleep(HANDOVER_SETTLE_S)
                    _wait_for_coop_warm(server, target_idx, timeout_s=15)
                    new_url = 'http://%s:%d/%s' % (
                        C.SERVER_IP, EDGE_PORTS[target_idx], VIDEO_FILE)
                    vlc_switch(car, vlc_paths_list[i], target_idx, new_url)
                else:
                    # Genuinely failed after all retries -- do NOT advance
                    # current_rsu[i], bookkeeping (throttle's per-RSU
                    # contention count, the network CSV's rsu column) must
                    # reflect where the vehicle actually is. target_rsu will
                    # still differ next tick, so the handover retries
                    # automatically.
                    info(f'*** [{car.name}] still not associated with '
                         f'{target_rsu} after retries; staying on '
                         f'{current_rsu[i]}, will retry next tick\n')
                    handover = 0
            else:
                link_out = car.cmd(f'iw dev {car.name}-wlan0 link')
                if 'Connected to' not in link_out:
                    link_out = ensure_assoc(car, rsu_objs[target_rsu],
                                             retries=6, wait=1.0)
                    flush_host_state(car, server)
                    warmup_connectivity(car, server)

            _, sig = C.parse_link_info(link_out, last_signal[i])
            last_signal[i] = sig
            throttle.update_car_state(i, current_rsu[i], sig)
            bw = throttle.get_rate(i)

            rsu_idx = rsu_edge_idx(current_rsu[i])
            edge_port = EDGE_PORTS[rsu_idx]
            cache, latency, speed_bps = measure_cdn(
                car, VIDEO_FILE, C.SERVER_IP, edge_port)
            loss_pct = loss_pollers[i].poll()
            vlc_stalling, vlc_buffer_pct, vlc_cum_stall_s = vlc_tel[i].poll()
            stall = int(latency >= 3.0 or cache == 'UNKNOWN' or vlc_stalling)

            net_rows[i].append({
                't': round(t, 2), 'x': round(x, 2), 'y': round(y, 2),
                'rsu': current_rsu[i], 'rssi_dbm': sig,
                'bw_mbps': round(bw, 3) if bw is not None else '',
                'cache': cache, 'latency_s': round(latency, 4),
                'speed_bps': round(speed_bps, 0),
                'loss_pct': loss_pct, 'stall': stall,
                'vlc_buffer_pct': round(vlc_buffer_pct, 1),
                'vlc_cum_stall_s': round(vlc_cum_stall_s, 3),
                'handover': handover,
            })

        if int(t) % 10 == 0:
            info('  t=%5.1fs  ' % t + '  '.join(
                'car%d=%s(%ddBm,%s)' % (
                    i + 1, current_rsu[i], last_signal[i],
                    net_rows[i][-1]['cache'])
                for i in range(n_cars)) + '\n')

        if live_plot is not None:
            car_states = [
                (net_rows[i][-1]['x'], net_rows[i][-1]['y'], current_rsu[i])
                for i in range(n_cars)
            ]
            try:
                live_plot.update(t, car_states)
            except Exception as e:
                info(f'*** [plot] update warning: {e}\n')

    info('*** Platoon mobility completed.\n')

    for i in range(n_cars):
        path = os.path.join(out_dir, f'{run_id}_car{i+1}_network.csv')
        if net_rows[i]:
            with open(path, 'w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=list(net_rows[i][0].keys()))
                w.writeheader()
                w.writerows(net_rows[i])
    info(f'*** Per-vehicle CSVs saved under {out_dir}\n')


# ===========================================================================
#  Main topology builder
# ===========================================================================
def build(n_cars=3, run_id=None, use_cli=False, run_client=False,
          out_dir='/tmp/cdn_multi_car_logs', ryu_port=6654, vlc_show=False,
          plot=False):
    setLogLevel('info')

    # Clean up leftover interfaces/bridges from a previous run before
    # building a new one -- run_multi_car.sh's own `mn -c` does this when
    # invoked through the wrapper, but this script is also meant to be
    # runnable directly (e.g. for --plot/--vlc-show, which the wrapper
    # doesn't pass through), so it needs to be safe standalone too.
    # mininet_cleanup_preserving_ryu(), not a plain `mn -c` shell-out: this
    # script (like cdn_baseline_topo_sdn.py) expects Ryu to already be
    # running externally, and plain `mn -c` kills any process literally
    # named "ryu-manager" as part of its own cleanup -- see that
    # function's docstring for the full explanation.
    info('*** Cleaning up leftover Mininet state (preserving any running Ryu controller)\n')
    mininet_cleanup_preserving_ryu()

    if run_id is None:
        run_id = f'cdn_sdn_{n_cars}cars'

    os.makedirs(out_dir, exist_ok=True)
    total_t = C.LAP_DURATION_S + 5   # small buffer, same pattern as
                                       # Situation1_DASH/platoon_topology.py

    info('*** ============================================\n')
    info('*** Scenario     : Situation 1 - Traffic Density (SDN+CDN)\n')
    info(f'*** Vehicles     : {n_cars} (platoon, {C.SPACING_M:.0f} m gap, '
         f'{C.SPEED_KMH:.0f} km/h)\n')
    info(f'*** Wireless     : 802.11{C.WIFI_MODE} (PHY {C.PHY_RATE_MBPS:.0f} Mbps, '
         f'AP cap {C.AP_CAPACITY_MBPS:.0f} Mbps L7)\n')
    info(f'*** BW model     : hybrid step2h + contention-sharing\n')
    info(f'*** Route        : {C.ROUTE_LENGTH_M:.0f} m loop, ~{total_t:.0f}s\n')
    info(f'*** Content      : {VIDEO_FILE} ({CDN_M.CDN_BITRATE_MBPS} Mbps)\n')
    info('*** ============================================\n')

    net = Mininet_wifi(
        controller=RemoteController,
        link=wmediumd,
        wmediumd_mode=interference,
    )

    info(f'*** Adding Ryu remote controller (port {ryu_port})\n')
    c0 = net.addController('c0', controller=RemoteController,
                            ip=C.CTRL_IP, port=ryu_port)

    info('*** Adding core OpenFlow switch\n')
    sw1 = net.addSwitch('sw1', cls=OVSKernelSwitch, protocols='OpenFlow13')

    info('*** Adding origin/edge server\n')
    server = net.addHost('server', ip=C.SERVER_IP + '/24',
                          mac='00:00:00:00:00:10')

    info(f'*** Adding 4 RSUs (mode={C.WIFI_MODE}, range={C.WIFI_RANGE_M}m)\n')
    rsu_objs = {}
    for rsu_name, cfg in C.RSU_LAYOUT.items():
        rsu = net.addAccessPoint(
            rsu_name, ssid='cdn-sit1-multicar', mode=C.WIFI_MODE,
            channel=cfg['channel'], position=f"{cfg['x']},{cfg['y']},0",
            range=str(C.WIFI_RANGE_M), txpower=C.TX_POWER_DBM,
            protocols='OpenFlow13', cls=OVSKernelAP,
            ip=C.RSU_IPS[rsu_name] + '/24',
        )
        rsu_objs[rsu_name] = rsu

    info(f'*** Adding {n_cars} vehicles (platoon)\n')
    cars = []
    for i in range(n_cars):
        x0, y0 = C.vehicle_position(i, 0.0)
        car = net.addStation(
            f'car{i+1}', ip=C.car_ip(i + 1) + '/24', mac=C.car_mac(i + 1),
            position=f'{x0},{y0},0',
        )
        cars.append(car)

    net.setPropagationModel(model=C.PROPAGATION_MODEL, exp=C.PROPAGATION_EXP)

    info('*** Configuring wifi nodes\n')
    net.configureWifiNodes()

    live_plot = None
    if plot:
        # Custom-styled live window (MultiCarLivePlot, see its docstring)
        # instead of mn_wifi's own net.plotGraph() -- that one is never
        # called at all now, so car.update_graph() (mn_wifi's own
        # per-node graph-callback machinery) is irrelevant here and always
        # left disabled below, regardless of --plot. Needs a real X
        # display: run this from a terminal on pc1's own graphical desktop
        # session (DISPLAY set), not a non-interactive/headless SSH
        # command -- same requirement as --vlc-show.
        info('*** Opening live topology plot window\n')
        live_plot = MultiCarLivePlot(C.positions, C.RSU_LAYOUT, n_cars,
                                      C.WIFI_RANGE_M)
        live_plot.setup()

    info(f'*** Building wired backbone ({C.BACKHAUL_BW_MBPS} Mbps fiber)\n')
    net.addLink(server, sw1, bw=C.BACKHAUL_BW_MBPS)
    for rsu in rsu_objs.values():
        net.addLink(sw1, rsu, bw=C.BACKHAUL_BW_MBPS)

    info('*** Starting network\n')
    net.build()
    c0.start()
    sw1.start([c0])
    for rsu in rsu_objs.values():
        rsu.start([c0])

    for car in cars:
        car.cmd(f'ip link set {car.name}-wlan0 up')
        # Always disabled: MultiCarLivePlot draws vehicle positions itself
        # (see run_platoon()'s live_plot.update() calls), it doesn't rely
        # on mn_wifi's own per-node update_graph()/net.plotGraph() machinery
        # at all, headless or not.
        C.disable_mn_wifi_graph_updates(car)
    server.cmd('ip link set server-eth0 up')
    time.sleep(1)

    info('*** Starting CDN origin + 4 per-RSU edge caches (nginx)\n')
    write_nginx_configs(server)

    for rsu_name, rsu in rsu_objs.items():
        setup_tc(rsu, f'{rsu_name}-wlan1')

    info('*** Topology is up.\n')

    if use_cli:
        info('*** ================================================\n')
        info('*** CLI mode. run_platoon() needs a real vlc_paths list\n')
        info('*** (one vlc_start() paths-dict per car, see build()\'s\n')
        info('*** --run-client branch) -- build that list yourself first,\n')
        info('*** then:\n')
        info('***   py throttle = PlatoonThrottleController(rsu_objs, '
             f'{n_cars})\n')
        info('***   py throttle.start()\n')
        info('***   py run_platoon(cars, server, rsu_objs, throttle, '
             f"vlc_paths, '{out_dir}', '{run_id}', {n_cars}, "
             f"{total_t:.1f}, live_plot=live_plot)\n")
        info('*** ================================================\n')
        import builtins
        builtins.cars = cars
        builtins.server = server
        builtins.rsu_objs = rsu_objs
        builtins.run_platoon = run_platoon
        builtins.PlatoonThrottleController = PlatoonThrottleController
        builtins.live_plot = live_plot
        CLI(net)

    elif run_client:
        # Pre-associate + launch every vehicle's VLC player BEFORE the
        # mobility loop starts, so t=0 in the logs is the true start of
        # streaming for every vehicle (same principle as
        # Situation1_DASH/platoon_topology.py: data collection begins at
        # the start of mobility execution, not after a location trigger).
        vlc_paths = []
        for i, car in enumerate(cars):
            x0, y0 = C.vehicle_position(i, 0.0)
            rsu_name = C.target_rsu_by_zone(x0, y0)
            info(f'*** Pre-associating {car.name} with {rsu_name}\n')
            ensure_assoc(car, rsu_objs[rsu_name], retries=6, wait=1.0)
            flush_host_state(car, server)
            warmup_connectivity(car, server)
            rsu_idx = rsu_edge_idx(rsu_name)
            initial_url = 'http://%s:%d/%s' % (
                C.SERVER_IP, EDGE_PORTS[rsu_idx], VIDEO_FILE)
            car_run_id = f'{run_id}_car{i+1}'
            # Only car1 gets a visible window even with --vlc-show: N
            # simultaneous VLC windows on one desktop isn't practical, and
            # every vehicle's real playback/stall telemetry is captured
            # either way (headless or not) -- --vlc-show here is a demo/
            # sanity-check aid, not something batch runs should ever pass.
            show_this = vlc_show and i == 0
            paths = vlc_start(car, out_dir, car_run_id, initial_url,
                               show=show_this)
            vlc_paths.append(paths)
        time.sleep(1)

        info('*** Starting hybrid bandwidth controller (step2h + contention)\n')
        throttle = PlatoonThrottleController(rsu_objs, n_cars)
        throttle.start()

        info('*** Starting platoon mobility...\n')
        run_platoon(cars, server, rsu_objs, throttle, vlc_paths, out_dir,
                    run_id, n_cars, total_t, live_plot=live_plot)
        throttle.stop()

        if live_plot is not None:
            live_plot.close()

        for i, car in enumerate(cars):
            vlc_stop(car, f'{run_id}_car{i+1}')

        safe_dir = os.path.join(_HERE, 'result_multi_car', run_id)
        os.makedirs(safe_dir, exist_ok=True)
        for i, car in enumerate(cars):
            car_run_id = f'{run_id}_car{i+1}'
            # *{car_run_id}*.csv, not {car_run_id}*.csv: the network CSV is
            # named "{car_run_id}_network.csv" (car_run_id at the start,
            # matched either way), but vlc_start()'s telemetry files are
            # named "vlc_playback_{car_run_id}.csv" / "vlc_events_
            # {car_run_id}.csv" (car_run_id in the middle) -- the tighter
            # prefix-only glob silently missed those, leaving the real
            # per-vehicle libvlc buffer/stall telemetry stuck in out_dir
            # (/tmp, not archived) instead of this run's permanent
            # result_multi_car/<run_id>/ folder.
            car.cmd(f'cp {out_dir}/*{car_run_id}*.csv {safe_dir}/ 2>/dev/null')
        _saved = server.cmd(f'ls {safe_dir}/')
        info(f'*** Saved results to {safe_dir}:\n{_saved}\n')

    info('*** Stopping network\n')
    net.stop()


def parse_args():
    p = argparse.ArgumentParser(
        description='Situation 1 (Traffic Density) SDN+CDN platoon topology')
    p.add_argument('--cars', type=int, choices=C.CAR_COUNTS, default=3)
    p.add_argument('--run-id', type=str, default=None)
    p.add_argument('--cli', action='store_true')
    p.add_argument('--run-client', action='store_true')
    p.add_argument('--out-dir', type=str, default='/tmp/cdn_multi_car_logs')
    p.add_argument('--ryu-port', type=int, default=6654,
                    help='OpenFlow port -- default 6654, not 6653, so this '
                         'does not collide with Situation1_DASH\'s '
                         'ryu-ctrl docker container if both are up at once')
    p.add_argument('--vlc-show', action='store_true',
                    help='open a real video window for car1 only (needs a '
                         'reachable X display) -- demo/debug aid, never '
                         'pass this for batch runs')
    p.add_argument('--plot', action='store_true',
                    help='open a live topology window showing RSU + '
                         'vehicle positions as the platoon moves (needs a '
                         'reachable X display, same requirement as '
                         '--vlc-show) -- demo/debug aid, never pass this '
                         'for batch runs')
    return p.parse_args()


if __name__ == '__main__':
    a = parse_args()
    build(n_cars=a.cars, run_id=a.run_id, use_cli=a.cli,
          run_client=a.run_client, out_dir=a.out_dir,
          ryu_port=a.ryu_port, vlc_show=a.vlc_show, plot=a.plot)
