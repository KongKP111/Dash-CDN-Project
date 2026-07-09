#!/usr/bin/env python3
"""
============================================================================
  platoon_topology.py  --  Situation 1 (Traffic Density), SDN+DASH arm
----------------------------------------------------------------------------
  Project : Comparative Analysis of SDN-CDN and SDN-DASH for Video
            Streaming in Vehicular Networks
  Author  : Hadis Rodpradit (DASH side)

  N vehicles (3 / 5 / 7, --cars) drive in a fixed-gap platoon (10 m,
  20 km/h car-following model) around the real PSU-Phuket campus loop
  route reused from the frozen Phase 1 baseline, streaming MPEG-DASH from
  a single origin server through 4 SDN-controlled RSUs, now running IEEE
  802.11g instead of 802.11a/p. Bandwidth per vehicle is a HYBRID model:
  RSSI-driven step2h tiering (same as the baseline, one stateful mapper
  PER vehicle) capped by contention-sharing of each RSU's ~20 Mbps
  effective 802.11g application-layer capacity across every vehicle
  currently associated to it (see PlatoonThrottleController).

  Everything reused from the frozen baseline (Dash/, dash-baseline/) is
  imported only -- never modified. See campus_config.py for the exact
  import list and why a few of the baseline's per-car helpers
  (ensure_assoc/flush_host_state/get_link_info/warmup_connectivity) are
  reimplemented here instead of imported: they hard-code the interface
  name 'car1-wlan0' internally, so they only work for a single vehicle
  literally named car1.

  Usage:
    sudo python3 platoon_topology.py --cars 3 --run-client --run-id case1_3cars
    sudo python3 platoon_topology.py --cars 3 --cli     # interactive debug
============================================================================
"""

import os
import sys
import csv
import time
import threading
import argparse

from mininet.node import RemoteController
from mininet.log import setLogLevel, info
from mn_wifi.net import Mininet_wifi
from mn_wifi.node import OVSKernelAP
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import campus_config as C

DASH_CLIENT_SCRIPT = os.path.join(C._REPO_ROOT, 'Dash', 'client', 'dash_client.py')
DASH_SERVER_SCRIPT = os.path.join(C._REPO_ROOT, 'Dash', 'servers', 'dash_server.py')

# The documented 3-rung ladder (Big Buck Bunny, Sunflower version --
# 1.0/2.5/5.0 Mbps @ 360p/720p/1080p, chunk-stream0/1/2 naming, matching
# baseline_model.py's LADDER dict, TEAMMATE_SETUP.md and the QoE model).
# NOTE: this is deliberately NOT the same path the frozen dash_topology.py
# uses (`bbb_ladder`, a different GPAC-encoded 5-rung ladder with
# non-round bitrates -- 295k/685k/1465k/2924k/5818k -- that does not match
# the documented ladder at all). Flagged to the user 2026-07-09; not
# changed in dash_topology.py since that file is frozen baseline code.
CONTENT_DIR = '/home/pc1/sdn-vanet-project/bbb_3rung'

HANDOVER_SETTLE_S = 0.60


# ===========================================================================
#  Per-vehicle helpers (reimplemented, NOT imported -- see module docstring)
# ===========================================================================
def ensure_assoc(car, ap, retries=4, wait=0.8):
    """
    mn_wifi's Node.setAssociation() (mn_wifi/node.py) is a NO-OP that just
    prints "X is already connected!" whenever its own internal bookkeeping
    (wintf.associatedTo) already names the requested AP. That bookkeeping is
    set OPTIMISTICALLY: mn_wifi/link.py's iw_connect() fires the real
    `iw dev ... connect` command and immediately marks associatedTo without
    checking whether the real 802.11 association actually completed. So if
    the first attempt's real connect is slow/lost (observed reliably here
    with 3 vehicles handing over to the same RSU within a couple of
    seconds of each other), every subsequent retry used to silently no-op
    instead of re-issuing the connect command.

    IMPORTANT: an earlier version of this fix reset the bookkeeping before
    EVERY attempt, including the first -- that broke the common case where
    the station is already correctly connected (e.g. mn_wifi's own
    auto-association already put it on the right AP): forcing a redundant
    `iw dev connect` to an SSID it's already connected to made every
    association fail outright, a regression (2026-07-09). The fix now only
    forces a real disconnect + bookkeeping reset on RETRIES (attempt > 1),
    after the first attempt has already been confirmed to genuinely fail --
    the first attempt is left alone so setAssociation's own real-vs-already-
    connected logic runs normally.
    """
    intf = f'{car.name}-wlan0'
    wintf = car.getNameToWintf(intf)
    last_output = ''
    for attempt in range(1, retries + 1):
        try:
            if attempt > 1:
                car.cmd(f'iw dev {intf} disconnect')
                if wintf is not None:
                    wintf.associatedTo = None
                time.sleep(0.2)
            car.setAssociation(ap, intf=intf)
        except Exception as e:
            info(f'*** [{car.name}] setAssociation warning: {e}\n')
        time.sleep(wait)
        last_output = car.cmd(f'iw dev {intf} link')
        if 'Connected to' in last_output:
            return last_output
        info(f'*** [{car.name}] association attempt {attempt} to {ap.name} failed\n')
    info(f'*** [{car.name}] failed to associate with {ap.name}\n')
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


def get_link_info(car):
    return car.cmd(f'iw dev {car.name}-wlan0 link')


# ===========================================================================
#  Per-vehicle ICMP loss poller (generalises baseline_topo.py's
#  PingLossPoller -- that one hard-codes a single /tmp/ping.log path, so a
#  per-vehicle version with its own log path is needed instead of imported)
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
        """Loss % over the log lines written since the last poll."""
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
#  Hybrid bandwidth model: step2h (per-vehicle RSSI tiering) + AP contention
# ===========================================================================
class PlatoonThrottleController:
    """
    Per-RSU HTB tree on the AP's wireless egress iface ({rsu}-wlan1):
        1:1   parent class, rate=ceil=AP_CAPACITY_MBPS (the physical
              802.11g effective-L7 cap for that AP, shared by everyone
              associated to it right now)
        1:1X  one child class per vehicle X, filtered by destination IP,
              rate = ceil = min(step2h_rate_X, AP_CAPACITY_MBPS / n_active_at_rsu)
              (rate == ceil deliberately -- no HTB borrowing between
              vehicles' classes, otherwise a vehicle could burst past its
              fair share whenever a platoon-mate is between segment
              requests, which would understate the density effect this
              scenario exists to measure)

    step2h_rate_X comes from a per-vehicle Step2HysteresisMapper (identical
    behaviour to the frozen single-vehicle baseline when a vehicle happens
    to be alone at an RSU); the contention-sharing division on top of that
    is the only new part for Situation 1.
    """
    IDLE_RATE_MBPS = 0.5   # placeholder rate for a vehicle's class on an RSU
                           # it isn't currently associated with (no traffic
                           # ever matches it, so this value is never used --
                           # kept low just so HTB's rate bookkeeping stays sane)

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
                # ceil == rate (no HTB borrowing): a vehicle must NOT be able
                # to burst past its currently computed fair share just
                # because a platoon-mate is momentarily idle between segment
                # requests -- otherwise the density effect this scenario is
                # built to measure gets diluted by burst timing luck.
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
#  Platoon mobility + handover + per-vehicle network logging
# ===========================================================================
def run_platoon(cars, server, rsu_objs, throttle, out_dir, run_id,
                 n_cars, total_t):
    intf_of = {i: f'{cars[i].name}-wlan0' for i in range(n_cars)}
    last_signal = [-50] * n_cars
    current_rsu = [None] * n_cars
    loss_pollers = []
    net_rows = [[] for _ in range(n_cars)]

    # ---- initial placement + association (t = 0) -------------------------
    for i, car in enumerate(cars):
        x, y = C.vehicle_position(i, 0.0)
        car.setPosition(f'{x},{y},0')
        rsu_name = C.target_rsu_by_zone(x, y)
        link_out = ensure_assoc(car, rsu_objs[rsu_name], retries=4, wait=0.8)
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

            if target_rsu != current_rsu[i]:
                info(f'*** [{car.name}] handover: '
                     f'{current_rsu[i]} -> {target_rsu}\n')
                link_out = ensure_assoc(car, rsu_objs[target_rsu],
                                         retries=4, wait=0.8)
                if 'Connected to' not in link_out:
                    time.sleep(0.3)
                    link_out = ensure_assoc(car, rsu_objs[target_rsu],
                                             retries=2, wait=0.5)
                if 'Connected to' in link_out:
                    flush_host_state(car, server)
                    warmup_connectivity(car, server)
                    current_rsu[i] = target_rsu
                    time.sleep(HANDOVER_SETTLE_S)
                else:
                    # Association genuinely failed after all retries. Do NOT
                    # advance current_rsu[i] -- bookkeeping (throttle's
                    # per-RSU contention count, the network CSV's rsu
                    # column) must reflect where the vehicle actually is,
                    # not where it was trying to go. Since target_rsu will
                    # still differ from current_rsu[i], the next tick
                    # retries the handover automatically (possibly against
                    # a further-along target if the vehicle has since left
                    # that zone too).
                    info(f'*** [{car.name}] still not associated with '
                         f'{target_rsu} after retries; staying on '
                         f'{current_rsu[i]}, will retry next tick\n')
            else:
                link_out = get_link_info(car)
                if 'Connected to' not in link_out:
                    link_out = ensure_assoc(car, rsu_objs[target_rsu],
                                             retries=4, wait=0.8)
                    flush_host_state(car, server)
                    warmup_connectivity(car, server)

            _, sig = C.parse_link_info(link_out, last_signal[i])
            last_signal[i] = sig
            throttle.update_car_state(i, current_rsu[i], sig)
            loss_pct = loss_pollers[i].poll()
            alloc_rate = throttle.get_rate(i)

            net_rows[i].append({
                't': round(t, 2), 'x': round(x, 2), 'y': round(y, 2),
                'rsu': current_rsu[i], 'rssi_dbm': sig,
                'allocated_bw_mbps': alloc_rate if alloc_rate is not None else '',
                'icmp_loss_pct': loss_pct,
            })

    info('*** Platoon mobility completed.\n')

    for i in range(n_cars):
        path = os.path.join(out_dir, f'{run_id}_car{i+1}_network.csv')
        if net_rows[i]:
            with open(path, 'w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=list(net_rows[i][0].keys()))
                w.writeheader()
                w.writerows(net_rows[i])


# ===========================================================================
#  Main topology builder
# ===========================================================================
def build(n_cars=3, run_id=None, use_cli=False, run_client=False,
          out_dir='/tmp/platoon_logs', plot=False):
    setLogLevel('info')

    if run_id is None:
        run_id = f'situation1_{n_cars}cars'

    os.makedirs(out_dir, exist_ok=True)

    total_t = C.LAP_DURATION_S + 5   # small buffer, same pattern as baseline

    info('*** ============================================\n')
    info('*** Scenario     : Situation 1 - Traffic Density (SDN+DASH)\n')
    info(f'*** Vehicles     : {n_cars} (platoon, {C.SPACING_M:.0f} m gap, '
         f'{C.SPEED_KMH:.0f} km/h)\n')
    info(f'*** Wireless     : 802.11{C.WIFI_MODE} (PHY {C.PHY_RATE_MBPS:.0f} Mbps, '
         f'AP cap {C.AP_CAPACITY_MBPS:.0f} Mbps L7)\n')
    info(f'*** BW model     : hybrid step2h + contention-sharing\n')
    info(f'*** Route        : {C.ROUTE_LENGTH_M:.0f} m loop, ~{total_t:.0f}s\n')
    info('*** ============================================\n')

    net = Mininet_wifi(
        controller=RemoteController,
        link=wmediumd,
        wmediumd_mode=interference,
    )

    info('*** Adding SDN controller\n')
    c0 = net.addController('c0', controller=RemoteController,
                            ip=C.CTRL_IP, port=C.CTRL_PORT)

    info('*** Adding core OpenFlow switch\n')
    from mininet.node import OVSKernelSwitch
    sw1 = net.addSwitch('sw1', cls=OVSKernelSwitch, protocols='OpenFlow13')

    info('*** Adding origin server\n')
    server = net.addHost('server', ip=C.SERVER_IP + '/24',
                          mac='00:00:00:00:00:10')

    info(f'*** Adding 4 RSUs (mode={C.WIFI_MODE}, range={C.WIFI_RANGE_M}m)\n')
    rsu_objs = {}
    for rsu_name, cfg in C.RSU_LAYOUT.items():
        rsu = net.addAccessPoint(
            rsu_name, ssid='situation1-dash', mode=C.WIFI_MODE,
            channel=cfg['channel'], position=f"{cfg['x']},{cfg['y']},0",
            range=str(C.WIFI_RANGE_M), txpower=C.TX_POWER_DBM,
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

    if plot:
        # Live matplotlib window showing RSU + vehicle positions as the
        # platoon moves. Needs a real X display -- run this from a terminal
        # on pc1's own graphical desktop session (DISPLAY set), not a
        # non-interactive/headless SSH command.
        info('*** Opening live topology plot window\n')
        net.plotGraph(max_x=200, max_y=200)

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
    server.cmd('ip link set server-eth0 up')
    time.sleep(1)

    for car in cars:
        C.disable_mn_wifi_graph_updates(car)

    throttle = PlatoonThrottleController(rsu_objs, n_cars)

    info('*** Bringing up DASH origin server\n')
    server.cmd(
        f'python3 {DASH_SERVER_SCRIPT} '
        f'--dir {CONTENT_DIR} --port 8080 '
        f'--log /tmp/dash_server_{run_id}.log '
        f'> /tmp/dash_server_{run_id}_stdout.log 2>&1 &'
    )
    time.sleep(2)

    info('*** Topology is up.\n')

    if use_cli:
        info('*** ================================================\n')
        info('*** CLI mode. To start the platoon run manually:\n')
        info('***   py run_platoon(cars, server, rsu_objs, throttle, '
             f"'{out_dir}', '{run_id}', {n_cars}, {total_t:.1f})\n")
        info('*** ================================================\n')
        import builtins
        builtins.cars = cars
        builtins.server = server
        builtins.rsu_objs = rsu_objs
        builtins.throttle = throttle
        builtins.run_platoon = run_platoon
        CLI(net)

    elif run_client:
        mpd_url = f'http://{C.SERVER_IP}:8080/index.mpd'
        stream_duration = total_t + 5

        # Pre-associate + launch every vehicle's DASH client BEFORE the
        # mobility loop starts, so t=0 in the logs is the true start of
        # streaming for every vehicle (per the Situation 1 spec: data
        # collection begins at the very start of the mobility execution,
        # not after some location-based trigger).
        for i, car in enumerate(cars):
            x0, y0 = C.vehicle_position(i, 0.0)
            rsu_name = C.target_rsu_by_zone(x0, y0)
            info(f'*** Pre-associating {car.name} with {rsu_name}\n')
            ensure_assoc(car, rsu_objs[rsu_name], retries=4, wait=0.8)
            flush_host_state(car, server)
            warmup_connectivity(car, server)

        time.sleep(1)

        for i, car in enumerate(cars):
            car_run_id = f'{run_id}_car{i+1}'
            client_cmd = (
                f'nohup python3 {DASH_CLIENT_SCRIPT} '
                f'--url {mpd_url} --run-id {car_run_id} --out {out_dir} '
                f'--duration {stream_duration:.1f} '
                f'> /tmp/client_{car_run_id}.log 2>&1 &'
            )
            info(f'*** Launching DASH client on {car.name}: {car_run_id}\n')
            car.cmd(client_cmd)
        time.sleep(2)

        info('*** Starting hybrid bandwidth controller (step2h + contention)\n')
        throttle.start()

        info('*** Starting platoon mobility...\n')
        run_platoon(cars, server, rsu_objs, throttle, out_dir, run_id,
                    n_cars, total_t)
        throttle.stop()

        info('*** Mobility done. Waiting for clients to finish...\n')
        for _ in range(150):
            still_running = False
            for car in cars:
                if car.cmd('pgrep -f dash_client.py').strip():
                    still_running = True
                    break
            if not still_running:
                info('*** All clients finished.\n')
                break
            time.sleep(1)
        time.sleep(2)

        safe_dir = f'/home/pc1/sdn-cdn-dash-research/Situation1_DASH/results_raw/{run_id}'
        os.makedirs(safe_dir, exist_ok=True)
        for i, car in enumerate(cars):
            car_run_id = f'{run_id}_car{i+1}'
            car.cmd(f'cp {out_dir}/{car_run_id}*.csv {safe_dir}/ 2>/dev/null')
            car.cmd(f'cp {out_dir}/{car_run_id}*.json {safe_dir}/ 2>/dev/null')
            car.cmd(f'cp /tmp/client_{car_run_id}.log {safe_dir}/ 2>/dev/null')
        _saved = server.cmd(f'ls {safe_dir}/')
        info(f'*** Saved results to {safe_dir}:\n{_saved}\n')

    info('*** Stopping network\n')
    net.stop()


def parse_args():
    p = argparse.ArgumentParser(
        description='Situation 1 (Traffic Density) SDN+DASH platoon topology')
    p.add_argument('--cars', type=int, choices=C.CAR_COUNTS, default=3)
    p.add_argument('--run-id', type=str, default=None)
    p.add_argument('--cli', action='store_true')
    p.add_argument('--run-client', action='store_true')
    p.add_argument('--out-dir', type=str, default='/tmp/platoon_logs')
    p.add_argument('--plot', action='store_true',
                    help='open a live topology window (needs a real X '
                         'display -- run from pc1\'s own desktop terminal)')
    return p.parse_args()


if __name__ == '__main__':
    a = parse_args()
    build(n_cars=a.cars, run_id=a.run_id, use_cli=a.cli,
          run_client=a.run_client, out_dir=a.out_dir, plot=a.plot)
