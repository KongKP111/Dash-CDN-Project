#!/usr/bin/env python3
"""
============================================================================
  SDN-DASH Topology  (Mininet-WiFi)
  Real PSU-Phuket Campus Route Edition
----------------------------------------------------------------------------
  Project : Comparative Analysis of SDN-CDN and SDN-DASH for Video
            Streaming in Vehicular Networks
  Author  : Hadis Rodpradit (DASH side)
  Advisor : Asst. Prof. Dr. Kuljaree Tantayakul
----------------------------------------------------------------------------
  This is the DASH-only topology. It builds a 4-RSU vehicular network over
  the real PSU-Phuket campus road trace, drives a vehicle around the loop
  with zone-based handover, applies per-situation dynamic throttling, and
  streams adaptive DASH video from a central origin server.
 
  RSU Configuration (paper-grade, defensible parameters):
    - Wireless standard : IEEE 802.11p / WAVE (DSRC for V2I)
      Implemented as 802.11a OFDM at 5 GHz, which is the same physical
      layer as 802.11p except for the 10 MHz channel width vs 20 MHz.
      This is the standard approach in VANET simulation literature.
    - Frequency band    : 5 GHz (channels 36, 40, 44, 48 - non-overlapping)
    - RSU coverage      : 300 m (typical urban DSRC range; ETSI ITS-G5)
    - TX power          : 20 dBm (100 mW, FCC-compliant for RSU)
    - Propagation model : Log-distance, exponent=3 (urban with obstructions)
    - Backhaul          : 100 Mbps Ethernet fiber (modern SDN core)
    - V2I link rate     : 6 Mbps base (throttled per situation)
 
  RSU positions give 100% coverage of the campus route (verified).
 
  References for parameter choices:
    - IEEE Std 802.11p-2010
    - IEEE 1609.x (WAVE)
    - ETSI EN 302 663 (ITS-G5)
    - 3GPP TR 22.886 (V2X Service Requirements)
 
  Args:
    --sit        1..5                test situation (controls throttle)
    --speed      20 | 25 | 30        car speed in km/h
    --round      1..10               round number (for log naming)
    --cli                            interactive Mininet CLI after build
    --run-client                     auto-launch DASH client and wait
    --run-id                         unique run ID for log naming
    --out-dir                        output directory for client logs
============================================================================
"""
 
import sys
import os
import re
import time
import math
import random
import threading
import argparse
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel, info
from mininet.link import TCLink
from mn_wifi.net import Mininet_wifi
from mn_wifi.node import OVSKernelAP
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference
 
# Campus route waypoints (from PSU-Phuket campus SUMO/OSM mapping)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mobility_positions import positions
 
 
# ===========================================================================
#  RSU CONFIGURATION  (paper-defensible parameters)
# ===========================================================================
WIFI_MODE          = 'a'      # 802.11a OFDM at 5GHz (same PHY as 802.11p)
WIFI_RANGE_M       = 300      # 300 m urban DSRC range (ETSI ITS-G5)
TX_POWER_DBM       = 20       # 20 dBm = 100 mW (FCC RSU limit)
PROPAGATION_MODEL  = 'logDistance'
PROPAGATION_EXP    = 3        # urban with obstructions (typical VANET papers)
 
# RSU positions give 100% coverage of the campus route.
# Non-overlapping 5 GHz channels (802.11a/p band).
RSU_LAYOUT = {
    'rsu1': {'x': 58,  'y': 160, 'channel': '36'},
    'rsu2': {'x': 142, 'y': 160, 'channel': '40'},
    'rsu3': {'x': 138, 'y': 64,  'channel': '44'},
    'rsu4': {'x': 56,  'y': 66,  'channel': '48'},
}
 
# Backhaul / core network
BACKHAUL_BW_MBPS   = 100      # 100 Mbps fiber (RSU <-> SDN core)
 
# Controller (Ryu runs in Docker on host network)
CTRL_IP            = '127.0.0.1'
CTRL_PORT          = 6653
 
# Addressing (single /24)
SERVER_IP          = '10.0.0.10'
RSU_IPS = {
    'rsu1': '10.0.0.101', 'rsu2': '10.0.0.102',
    'rsu3': '10.0.0.103', 'rsu4': '10.0.0.104',
}
CAR_IP             = '10.0.0.200'
 
# Speed scaling. Waypoints are sampled at 25 km/h baseline (1.0 s/step),
# so step time is rescaled to hit the requested constant speed.
SPEED_SCALE = {
    20: 1.250,   # slower -> longer step time
    25: 1.000,   # baseline sampling rate
    30: 0.833,   # faster -> shorter step time
}
 
# Handover settle time
HANDOVER_SETTLE_S  = 0.60
 
 
# ===========================================================================
#  SITUATION PROFILES  (5 situations)
# ===========================================================================
# Each situation isolates a specific V2I impairment so performance can be
# attributed to a known cause.
#   base_bw       : steady-state wireless rate (Mbps) on the RSU->car link
#   handover_drop : fraction the rate is cut to for handover_dur seconds
#                   immediately after each handover (None = no handover dip)
#   handover_dur  : seconds the handover dip lasts
#   drops         : list of (start_s, duration_s, rate_mbps) hard bandwidth
#                   cuts at fixed times (emulates a coverage dead zone)
#   jitter        : +/- fraction the rate randomly varies every ~2 s
SITUATION = {
    1: {'name': 'Baseline',
        'base_bw': 8.0,
        'handover_drop': None, 'handover_dur': 0.0,
        'drops': [], 'jitter': 0.0},
 
    2: {'name': 'Light Handover',
        'base_bw': 5.0,
        'handover_drop': 0.50, 'handover_dur': 3.0,
        'drops': [], 'jitter': 0.0},
 
    3: {'name': 'Heavy Handover',
        'base_bw': 3.0,
        'handover_drop': 0.35, 'handover_dur': 5.0,
        'drops': [], 'jitter': 0.0},
 
    4: {'name': 'Sudden Drop (Dead Zone)',
        'base_bw': 6.0,
        'handover_drop': None, 'handover_dur': 0.0,
        'drops': [(30, 10, 1.0), (70, 10, 1.0)], 'jitter': 0.0},
 
    5: {'name': 'Combined Stress',
        'base_bw': 3.0,
        'handover_drop': 0.40, 'handover_dur': 4.0,
        'drops': [(45, 10, 1.0)], 'jitter': 0.30},
}
 
 
# ===========================================================================
#  HELPER FUNCTIONS
# ===========================================================================
def distance(x1, y1, x2, y2):
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
 
 
def estimate_speed_kmh(prev_x, prev_y, x, y, step_time_s):
    if step_time_s <= 0:
        return 0.0
    d = distance(prev_x, prev_y, x, y)
    return (d / step_time_s) * 3.6
 
 
def get_link_info(car1):
    return car1.cmd('iw dev car1-wlan0 link')
 
 
def parse_link_info(output, prev_signal=None):
    ap_mac = 'N/A'
    signal = None
    m = re.search(r'Connected to ([0-9a-f:]{17})', output)
    s = re.search(r'signal:\s*(-?\d+)\s*dBm', output)
    if m:
        ap_mac = m.group(1)
    if s:
        try:
            signal = int(s.group(1))
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
            info(f"*** Forced association to {ap.name}\n")
        except Exception as e:
            info(f"*** setAssociation warning: {e}\n")
        time.sleep(wait)
        last_output = get_link_info(car1)
        if 'Connected to' in last_output:
            info(f"*** Association success with {ap.name} on attempt {attempt}\n")
            return last_output
        info(f"*** Association attempt {attempt} to {ap.name} failed\n")
    info(f"*** Failed to associate with {ap.name}\n")
    return last_output
 
 
def flush_host_state(car1, server):
    car1.cmd('ip neigh flush dev car1-wlan0')
    car1.cmd('ip route flush cache')
    server.cmd('ip neigh flush dev server-eth0')
    server.cmd('ip route flush cache')
 
 
def warmup_connectivity(car1, server):
    car1.cmd(f'arping -c 2 -I car1-wlan0 {SERVER_IP} > /dev/null 2>&1')
    car1.cmd(f'ping -c 2 -W 1 {SERVER_IP} > /dev/null 2>&1')
 
 
def disable_mn_wifi_graph_updates(sta):
    def _noop(*args, **kwargs):
        return None
    sta.update_graph = _noop
 
 
# ===========================================================================
#  ZONE-BASED HANDOVER  (clockwise: rsu1 -> rsu2 -> rsu3 -> rsu4 -> rsu1)
# ===========================================================================
def target_rsu_by_zone(x, y):
    # Top strip
    if y >= 145:
        if x < 108:
            return 'rsu1'
        return 'rsu2'
    # Right strip
    if x >= 145 and 95 <= y < 145:
        return 'rsu2'
    # Bottom strip
    if y < 95:
        if x >= 92:
            return 'rsu3'
        return 'rsu4'
    # Left strip
    if x < 98 and 95 <= y < 145:
        return 'rsu1'
    # Fallback quadrants
    if x >= 120 and y >= 120:
        return 'rsu2'
    if x >= 120 and y < 120:
        return 'rsu3'
    if x < 120 and y < 120:
        return 'rsu4'
    return 'rsu1'
 
 
# ===========================================================================
#  DYNAMIC THROTTLE CONTROLLER
# ===========================================================================
# Applies time- and handover-driven bandwidth changes to the RSU wireless
# interfaces during a run, emulating realistic V2I channel impairments.
# All rate changes use `tc qdisc change ... tbf` on each RSU's wlan iface.
class ThrottleController:
    def __init__(self, rsu_objs, profile):
        self.rsu_objs   = rsu_objs
        self.base_bw    = profile['base_bw']
        self.ho_drop    = profile.get('handover_drop')
        self.ho_dur     = profile.get('handover_dur', 0.0)
        self.drops      = list(profile.get('drops', []))
        self.jitter     = profile.get('jitter', 0.0)
        self._stop      = threading.Event()
        self._lock      = threading.Lock()
        self._cur_rate  = self.base_bw
        self._ho_until  = 0.0    # wall-clock time the handover dip ends
        self._t0        = None
        self._threads   = []
 
    # ---- low-level: push a rate (Mbps) to every RSU wlan iface ----------
    def _apply_rate(self, rate_mbps):
        rate_mbps = max(rate_mbps, 0.1)   # never go to zero (tc needs > 0)
        for rsu_name, rsu in self.rsu_objs.items():
            iface = f'{rsu_name}-wlan1'
            rsu.cmd(f'tc qdisc change dev {iface} root tbf '
                    f'rate {rate_mbps:.3f}mbit burst 32kbit latency 50ms '
                    f'2>/dev/null')
 
    # ---- compute the rate that should currently be in effect -----------
    def _effective_rate(self, now):
        elapsed = now - self._t0
 
        # 1) fixed dead-zone drops take top priority
        for (start, dur, rate) in self.drops:
            if start <= elapsed < start + dur:
                return rate
 
        # 2) handover dip
        rate = self.base_bw
        if self.ho_drop is not None and now < self._ho_until:
            rate = self.base_bw * self.ho_drop
 
        # 3) jitter on top of the steady base
        if self.jitter > 0:
            factor = 1.0 + random.uniform(-self.jitter, self.jitter)
            rate = rate * factor
 
        return rate
 
    # ---- called by the mobility loop on every handover -----------------
    def notify_handover(self):
        if self.ho_drop is None:
            return
        with self._lock:
            self._ho_until = time.time() + self.ho_dur
 
    # ---- background loop: re-evaluate and apply the rate every 0.5 s ----
    def _run_loop(self):
        while not self._stop.is_set():
            now = time.time()
            with self._lock:
                rate = self._effective_rate(now)
                if abs(rate - self._cur_rate) > 0.05:
                    self._apply_rate(rate)
                    self._cur_rate = rate
            time.sleep(0.5)
 
    def start(self):
        self._t0 = time.time()
        self._apply_rate(self.base_bw)
        if not self.drops and self.ho_drop is None and self.jitter == 0:
            # Static situation (Baseline) - nothing dynamic to drive
            return
        t = threading.Thread(target=self._run_loop, daemon=True)
        t.start()
        self._threads.append(t)
 
    def stop(self):
        self._stop.set()
 
 
# ===========================================================================
#  MANUAL MOBILITY TRACE
# ===========================================================================
def move_car_along_route(car1, server, rsu_objs, step_time=1.0, throttle=None):
    last_valid_signal = -50
 
    prev_t, prev_x, prev_y = positions[0]
    start_target = target_rsu_by_zone(prev_x, prev_y)
 
    link_output = ensure_assoc(car1, rsu_objs[start_target], retries=4, wait=0.8)
    flush_host_state(car1, server)
    warmup_connectivity(car1, server)
 
    current_target = start_target
    ap_mac, signal = parse_link_info(link_output, last_valid_signal)
    last_valid_signal = signal
 
    car1.setPosition(f'{prev_x},{prev_y},0')
 
    info(f'*** Mobility started: {len(positions)} waypoints, '
         f'step={step_time:.3f}s\n')
 
    for t, x, y in positions[1:]:
        time.sleep(step_time)
        car1.setPosition(f'{x},{y},0')
 
        speed_kmh = estimate_speed_kmh(prev_x, prev_y, x, y, step_time)
        target_rsu = target_rsu_by_zone(x, y)
 
        if target_rsu != current_target:
            info(f'*** Handover: {current_target} -> {target_rsu}\n')
            if throttle is not None:
                throttle.notify_handover()
            link_output = ensure_assoc(
                car1, rsu_objs[target_rsu], retries=4, wait=0.8
            )
            if 'Connected to' not in link_output:
                time.sleep(0.3)
                link_output = ensure_assoc(
                    car1, rsu_objs[target_rsu], retries=2, wait=0.5
                )
            flush_host_state(car1, server)
            warmup_connectivity(car1, server)
            current_target = target_rsu
            time.sleep(HANDOVER_SETTLE_S)
        else:
            link_output = get_link_info(car1)
            if 'Connected to' not in link_output:
                link_output = ensure_assoc(
                    car1, rsu_objs[target_rsu], retries=4, wait=0.8
                )
                flush_host_state(car1, server)
                warmup_connectivity(car1, server)
 
        ap_mac, signal = parse_link_info(link_output, last_valid_signal)
        last_valid_signal = signal
 
        info(f'*** car1 ({x:.0f},{y:.0f}) t={t:.0f} | RSU={current_target} | '
             f'sig={signal}dBm | v={speed_kmh:.1f}km/h\n')
 
        prev_t, prev_x, prev_y = t, x, y
 
    info('*** Mobility completed.\n')
 
 
# ===========================================================================
#  MAIN TOPOLOGY BUILDER
# ===========================================================================
def build(sit=1, speed=20, rnd=1, use_cli=False,
          run_client=False, run_id=None, out_dir='/tmp/dash_logs'):
    setLogLevel('info')
 
    profile   = SITUATION[sit]
    base_bw   = profile['base_bw']
    step_time = SPEED_SCALE.get(speed, 1.0)
    total_t   = len(positions) * step_time
 
    info('*** ============================================\n')
    info(f"*** Architecture : DASH\n")
    info(f"*** Situation    : {sit} - {profile['name']}\n")
    info(f"*** Car speed    : {speed} km/h (step={step_time:.3f}s)\n")
    info(f"*** Round        : {rnd}\n")
    info(f"*** V2I rate     : {base_bw} Mbps (RSU<->Car wireless)\n")
    info(f"*** Backhaul     : {BACKHAUL_BW_MBPS} Mbps (fiber)\n")
    info(f"*** Route        : {len(positions)} waypoints, ~{total_t:.0f}s\n")
    info('*** ============================================\n')
 
    net = Mininet_wifi(
        controller=RemoteController,
        link=wmediumd,
        wmediumd_mode=interference,
    )
 
    # ---- Controller (Ryu via Docker) -----------------------------------
    info('*** Adding SDN controller\n')
    c0 = net.addController('c0', controller=RemoteController,
                           ip=CTRL_IP, port=CTRL_PORT)
 
    # ---- Core SDN switch ------------------------------------------------
    info('*** Adding core OpenFlow switch\n')
    sw1 = net.addSwitch('sw1', cls=OVSKernelSwitch, protocols='OpenFlow13')
 
    # ---- Origin video server --------------------------------------------
    info('*** Adding origin server\n')
    server = net.addHost('server', ip=SERVER_IP + '/24',
                         mac='00:00:00:00:00:10')
 
    # ---- RSUs (802.11p-class, 5 GHz, paper-realistic) -------------------
    info(f'*** Adding 4 RSUs (mode={WIFI_MODE}, range={WIFI_RANGE_M}m, '
         f'TX={TX_POWER_DBM}dBm)\n')
    rsu_objs = {}
    for rsu_name, cfg in RSU_LAYOUT.items():
        rsu = net.addAccessPoint(
            rsu_name,
            ssid='vanet-rsu',
            mode=WIFI_MODE,
            channel=cfg['channel'],
            position=f"{cfg['x']},{cfg['y']},0",
            range=str(WIFI_RANGE_M),
            txpower=TX_POWER_DBM,
            ip=RSU_IPS[rsu_name] + '/24',
        )
        rsu_objs[rsu_name] = rsu
 
    # ---- Mobile client (Vehicle / OBU) ----------------------------------
    start_t, start_x, start_y = positions[0]
    info('*** Adding vehicle (OBU)\n')
    car1 = net.addStation('car1', ip=CAR_IP + '/24',
                          mac='00:00:00:00:02:00',
                          position=f'{start_x},{start_y},0')
 
    # ---- Propagation model ----------------------------------------------
    net.setPropagationModel(model=PROPAGATION_MODEL, exp=PROPAGATION_EXP)
 
    info('*** Configuring wifi nodes\n')
    net.configureWifiNodes()
 
    # ---- Backbone (wired fiber) ----------------------------------------
    info('*** Building wired backbone (100 Mbps fiber)\n')
    net.addLink(server, sw1, bw=BACKHAUL_BW_MBPS)
    for rsu_name, rsu in rsu_objs.items():
        # Backhaul = fiber 100 Mbps; throttle happens on the wireless side.
        net.addLink(sw1, rsu, bw=BACKHAUL_BW_MBPS)
 
    # ---- Start network --------------------------------------------------
    info('*** Starting network\n')
    net.build()
    c0.start()
    sw1.start([c0])
    for rsu in rsu_objs.values():
        rsu.start([c0])
 
    car1.cmd('ip link set car1-wlan0 up')
    server.cmd('ip link set server-eth0 up')
    time.sleep(1)
 
    disable_mn_wifi_graph_updates(car1)
 
    # ---- Install base tc qdisc on each RSU wireless iface ---------------
    # The dynamic ThrottleController later mutates this rate during the run.
    info(f'*** Installing base tc qdisc ({base_bw} Mbps) on RSU wireless ifaces\n')
    for rsu_name, rsu in rsu_objs.items():
        iface = f'{rsu_name}-wlan1'
        rsu.cmd(f'tc qdisc del dev {iface} root 2>/dev/null')
        rsu.cmd(f'tc qdisc add dev {iface} root tbf rate {base_bw}mbit '
                f'burst 32kbit latency 50ms 2>/dev/null')
    throttle = ThrottleController(rsu_objs, profile)
 
    # ---- Bring up DASH origin server ------------------------------------
    info('*** Bringing up DASH origin server\n')
    server.cmd(
        'python3 /home/pc1/sdn-cdn-dash-research/Dash/servers/dash_server.py '
        '--dir /home/pc1/sdn-vanet-project/bbb_ladder '
        '--port 8080 '
        '--log /tmp/dash_server.log '
        '> /tmp/dash_server_stdout.log 2>&1 &'
    )
 
    info('*** Topology is up.\n')
    time.sleep(3)
 
    # ---- Mode select ----------------------------------------------------
    if use_cli:
        info('*** =========================================================\n')
        info('*** CLI mode. To start mobility manually:\n')
        info('***   py move_car_along_route(car1, server, rsu_objs)\n')
        info('*** =========================================================\n')
        import builtins
        builtins.car1 = car1
        builtins.server = server
        builtins.rsu_objs = rsu_objs
        builtins.move_car_along_route = move_car_along_route
        builtins.throttle = throttle
        CLI(net)
 
    elif run_client:
        if run_id is None:
            run_id = f'dash_sit{sit}_spd{speed}_r{rnd}'
        client_script = '/home/pc1/sdn-cdn-dash-research/Dash/client/dash_client.py'
        mpd_url = f'http://{SERVER_IP}:8080/index.mpd'
 
        # Duration = full mobility time + small buffer, so the client stops
        # and writes its logs exactly when the vehicle finishes the route.
        stream_duration = len(positions) * step_time + 5
        client_cmd = (
            f'nohup python3 {client_script} '
            f'--url {mpd_url} '
            f'--run-id {run_id} '
            f'--out {out_dir} '
            f'--duration {stream_duration:.1f} '
            f'> /tmp/client_{run_id}.log 2>&1 &'
        )
 
        # Associate with the first RSU BEFORE launching the client so it has
        # a working route on its first request (else: No route to host).
        start_rsu = target_rsu_by_zone(positions[0][1], positions[0][2])
        info('*** Pre-associating car1 with ' + start_rsu + ' before client\n')
        ensure_assoc(car1, rsu_objs[start_rsu], retries=4, wait=0.8)
        flush_host_state(car1, server)
        warmup_connectivity(car1, server)
        time.sleep(1)
 
        info(f'*** Launching client in car1 (background): {run_id}\n')
        car1.cmd(client_cmd)
        time.sleep(3)
 
        info('*** Starting dynamic throttle controller...\n')
        throttle.start()
        info('*** Starting mobility trace...\n')
        move_car_along_route(car1, server, rsu_objs, step_time=step_time,
                             throttle=throttle)
        throttle.stop()
 
        info('*** Mobility done. Waiting for client to finish...\n')
        for i in range(120):
            result = car1.cmd('pgrep -f dash_client.py')
            if not result.strip():
                info('*** Client process ended.\n')
                break
            time.sleep(1)
        # Give the client a moment to flush its CSV/JSON to disk.
        time.sleep(2)
 
        # CRITICAL: copy results to a SAFE location NOW, before net.stop()
        # and the script-level "sudo mn -c" wipe /tmp/*.log and namespaces.
        safe_dir = f'/home/pc1/sdn-cdn-dash-research/results_raw/{run_id}'
        car1.cmd(f'mkdir -p {safe_dir}')
        car1.cmd(f'cp {out_dir}/*.csv {safe_dir}/ 2>/dev/null')
        car1.cmd(f'cp {out_dir}/*.json {safe_dir}/ 2>/dev/null')
        car1.cmd(f'cp /tmp/client_{run_id}.log {safe_dir}/ 2>/dev/null')
        _saved = car1.cmd(f'ls {safe_dir}/')
        info('*** Saved results to ' + safe_dir + ':\n' + _saved + '\n')
        info('*** Client finished.\n')
 
    info('*** Stopping network\n')
    net.stop()
 
 
def parse_args():
    p = argparse.ArgumentParser(description='SDN-DASH topology (campus route)')
    p.add_argument('--sit',   type=int, choices=range(1, 6), default=1)
    p.add_argument('--speed', type=int, choices=[20, 25, 30], default=20)
    p.add_argument('--round', type=int, default=1, dest='rnd')
    p.add_argument('--cli',   action='store_true')
    p.add_argument('--run-client', action='store_true')
    p.add_argument('--run-id', type=str, default=None)
    p.add_argument('--out-dir', type=str, default='/tmp/dash_logs')
    return p.parse_args()
 
 
if __name__ == '__main__':
    a = parse_args()
    build(sit=a.sit, speed=a.speed, rnd=a.rnd, use_cli=a.cli,
          run_client=a.run_client, run_id=a.run_id, out_dir=a.out_dir)

