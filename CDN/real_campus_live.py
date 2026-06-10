#!/usr/bin/python3
# ============================================================
# real_campus_live.py — Cooperative Edge CDN in SDN-VANET
#
# Topology:
#   car1 (10.0.0.1) —wifi— ap1/ap2/ap3/ap4 —— s1 —— server1 (10.0.0.100)
#                                                      origin nginx :8080
#                                                      edge   nginx :8081
# ============================================================

import time
import math
import re
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

from mobility_positions import positions


# =========================================================
# CONFIG
# =========================================================
REAL_WIFI_RANGE_M    = 250
RSSI_LOG_FILE        = 'rssi_real_campus_tuned_v5.csv'
PLOT_COVERAGE_RADIUS = 58
HANDOVER_SETTLE_TIME = 0.60

ORIGIN_IP   = '10.0.0.100'
EDGE_IP     = '10.0.0.100'
ORIGIN_PORT = 8080
EDGE_PORT   = 8081
CDN_VIDEO_PATH  = 'Video.mp4'
CDN_VIDEO2_PATH = 'Video2.mp4'


# =========================================================
# Utility
# =========================================================
def distance(x1, y1, x2, y2):
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def estimate_speed_kmh(prev_x, prev_y, x, y, step_time_s):
    return (distance(prev_x, prev_y, x, y) / step_time_s) * 3.6


def get_link_info(car1):
    return car1.cmd('iw dev car1-wlan0 link')


def parse_link_info(output, prev_signal=None):
    ap_mac = 'N/A'
    signal = None

    mac_match    = re.search(r'Connected to ([0-9a-f:]{17})', output)
    signal_match = re.search(r'signal:\s*(-?\d+)\s*dBm', output)

    if mac_match:
        ap_mac = mac_match.group(1)
    if signal_match:
        try:
            signal = int(signal_match.group(1))
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


def flush_host_state(car1, server1):
    info('*** Flushing ARP and route cache\n')
    car1.cmd('ip neigh flush dev car1-wlan0')
    car1.cmd('ip route flush cache')
    server1.cmd('ip neigh flush dev server1-eth0')
    server1.cmd('ip route flush cache')


def warmup_connectivity(car1, server1):
    car1.cmd(f'arping -c 2 -I car1-wlan0 {ORIGIN_IP} > /dev/null 2>&1')
    car1.cmd(f'ping -c 2 -W 1 {ORIGIN_IP} > /dev/null 2>&1')
    server1.cmd('arping -c 2 -I server1-eth0 10.0.0.1 > /dev/null 2>&1')
    server1.cmd('ping -c 2 -W 1 10.0.0.1 > /dev/null 2>&1')


def disable_mn_wifi_graph_updates(sta):
    def _noop(*args, **kwargs):
        return None
    sta.update_graph = _noop


# =========================================================
# Flow helpers
# =========================================================
def install_fallback_flows(ap_list, switch_list):
    info('*** Installing fallback flows (priority=100)\n')
    for node in ap_list + switch_list:
        result = node.cmd(
            f'ovs-ofctl -O OpenFlow13 add-flow {node.name} '
            '"priority=100,actions=normal"'
        )
        info(f'    {node.name}: {result.strip() or "OK"}\n')


def set_static_arp(car1, server1):
    info('*** Setting static ARP entries\n')
    server_mac = server1.cmd('cat /sys/class/net/server1-eth0/address').strip()
    car_mac    = car1.cmd('cat /sys/class/net/car1-wlan0/address').strip()

    if server_mac:
        car1.cmd(f'arp -s {ORIGIN_IP} {server_mac}')
        info(f'    car1    -> {ORIGIN_IP} = {server_mac}\n')
    if car_mac:
        server1.cmd(f'arp -s 10.0.0.1 {car_mac}')
        info(f'    server1 -> 10.0.0.1 = {car_mac}\n')


def verify_connectivity(car1, server1):
    info('*** Verifying car1 -> server1 connectivity\n')
    result = car1.cmd(f'ping -c 3 -W 2 {ORIGIN_IP}')
    info(result)
    if '0 received' in result or 'Unreachable' in result:
        info('*** WARNING: car1 cannot reach server1\n')
    else:
        info('*** Connectivity OK\n')

    r1 = car1.cmd(
        f'curl -o /dev/null -s -w "HTTP %{{http_code}} in %{{time_total}}s\\n" '
        f'--max-time 5 http://{ORIGIN_IP}:{ORIGIN_PORT}/'
    )
    info(f'    origin : {r1.strip()}\n')

    r2 = car1.cmd(
        f'curl -o /dev/null -s -w "HTTP %{{http_code}} in %{{time_total}}s\\n" '
        f'--max-time 5 http://{EDGE_IP}:{EDGE_PORT}/'
    )
    info(f'    edge   : {r2.strip()}\n')


# =========================================================
# CDN Cache Warmup
# =========================================================
def prewarm_cdn_cache(server1):
    info(f'*** Pre-warming CDN edge cache for: {CDN_VIDEO_PATH}\n')

    check = server1.cmd(
        f'test -f /home/kongpop/PSU_Project/cdn/origin/{CDN_VIDEO_PATH} '
        f'&& echo EXISTS || echo MISSING'
    ).strip()
    if 'MISSING' in check:
        info(f'*** WARNING: {CDN_VIDEO_PATH} not found\n')
        return

    server1.cmd('rm -rf /home/kongpop/PSU_Project/cdn/cache/*')
    server1.cmd('nginx -s reload')
    time.sleep(0.5)
    info('*** Cache cleared\n')

    # warm cache ด้วยไฟล์เล็ก test.mp4 ก่อน
    test_check = server1.cmd(
        'test -f /home/kongpop/PSU_Project/cdn/origin/test.mp4 '
        '&& echo EXISTS || echo MISSING'
    ).strip()

    if 'MISSING' in test_check:
        info('*** Creating test.mp4 (100KB) for cache warmup\n')
        server1.cmd(
            'dd if=/dev/urandom bs=1K count=100 '
            'of=/home/kongpop/PSU_Project/cdn/origin/test.mp4 2>/dev/null'
        )

    # warm cache ด้วย test.mp4 (เล็ก เร็ว)
    for i in range(1, 4):
        code = server1.cmd(
            f'curl -s -o /dev/null -w "%{{http_code}}" '
            f'--max-time 10 http://localhost:{EDGE_PORT}/test.mp4'
        ).strip()
        info(f'    warmup test.mp4 req {i}/3: HTTP {code}\n')
        time.sleep(0.3)

    cache_line = server1.cmd(
        f'curl -s -o /dev/null -D - --max-time 10 '
        f'http://localhost:{EDGE_PORT}/test.mp4 | grep -i x-cache'
    ).strip()
    info(f'    test.mp4 cache: {cache_line}\n')

    # warm cache Video.mp4 ด้วย -r 0-0 หลาย request
    info(f'*** Warming cache for {CDN_VIDEO_PATH}\n')
    for i in range(1, 6):
        code = server1.cmd(
            f'curl -s -o /dev/null -r 0-1 -w "%{{http_code}}" '
            f'--max-time 10 http://localhost:{EDGE_PORT}/{CDN_VIDEO_PATH}'
        ).strip()
        info(f'    warmup {CDN_VIDEO_PATH} req {i}/5: HTTP {code}\n')
        time.sleep(0.2)

    cache_line2 = server1.cmd(
        f'curl -s -o /dev/null -r 0-1 -D - --max-time 10 '
        f'http://localhost:{EDGE_PORT}/{CDN_VIDEO_PATH} | grep -i x-cache'
    ).strip()
    info(f'    {CDN_VIDEO_PATH} cache: {cache_line2}\n')

    if 'HIT' in cache_line2.upper():
        info('*** Cache warm — HIT confirmed\n')
    else:
        info('*** Cache not HIT yet — use test.mp4 for demo\n')


# =========================================================
# Handover zones
# =========================================================
def target_ap_by_zone(x, y):
    if y >= 145:
        return 'ap1' if x < 108 else 'ap2'
    if x >= 145 and 95 <= y < 145:
        return 'ap2'
    if y < 95:
        return 'ap3' if x >= 92 else 'ap4'
    if x < 98 and 95 <= y < 145:
        return 'ap1'
    if x >= 120 and y >= 120:
        return 'ap2'
    if x >= 120 and y < 120:
        return 'ap3'
    if x < 120 and y < 120:
        return 'ap4'
    return 'ap1'


# =========================================================
# Live plot
# =========================================================
class RealRoadLivePlot:
    def __init__(self, road_positions, aps, coverage_radius=58):
        self.road_positions  = road_positions
        self.aps             = aps
        self.coverage_radius = coverage_radius
        self.fig = self.ax = self.car_marker = None
        self.path_trace_done = self.path_trace_future = self.info_text = None
        self.all_x = [x for _, x, y in road_positions]
        self.all_y = [y for _, x, y in road_positions]

    def setup(self):
        plt.ion()
        self.fig, self.ax = plt.subplots(figsize=(10, 8))
        xs, ys = self.all_x, self.all_y

        self.path_trace_future, = self.ax.plot(
            xs + [xs[0]], ys + [ys[0]],
            linewidth=2.5, marker='o', markersize=3, color='#1482c5'
        )
        self.path_trace_done, = self.ax.plot(
            [], [], linewidth=3.0, marker='o', markersize=3, color='orange'
        )
        self.ax.text(xs[0]+1, ys[0]+2, 'START/END', fontsize=10, fontweight='bold')

        for ap_name, ap_data in self.aps.items():
            apx, apy = ap_data['x'], ap_data['y']
            self.ax.scatter(apx, apy, s=140, marker='s')
            self.ax.text(apx+2, apy+2, f'{ap_name.upper()} (R=250m)',
                         fontsize=10, fontweight='bold')
            self.ax.add_patch(Circle(
                (apx, apy), radius=self.coverage_radius,
                fill=True, facecolor='skyblue', edgecolor='red',
                linewidth=2, alpha=0.18
            ))

        _, sx, sy = self.road_positions[0]
        self.car_marker = self.ax.scatter(sx, sy, s=160, marker='o')
        self.info_text = self.ax.text(
            0.02, 0.98, 't=0.0s | speed=0.00 km/h | AP=N/A | car=(0,0)',
            transform=self.ax.transAxes, verticalalignment='top',
            fontsize=10, bbox=dict(boxstyle='round', alpha=0.3)
        )

        self.ax.set_title('Real Campus Road Live View with CDN')
        self.ax.set_xlabel('X')
        self.ax.set_ylabel('Y')
        self.ax.grid(True)
        self.ax.set_aspect('equal', adjustable='box')

        ap_xs = [a['x'] for a in self.aps.values()]
        ap_ys = [a['y'] for a in self.aps.values()]
        r = self.coverage_radius
        self.ax.set_xlim(min(min(xs), min(ap_xs)-r)-5,
                         max(max(xs), max(ap_xs)+r)+5)
        self.ax.set_ylim(min(min(ys), min(ap_ys)-r)-5,
                         max(max(ys), max(ap_ys)+r)+5)

        try:
            self.fig.canvas.manager.set_window_title('Real Campus Road Live View')
        except Exception:
            pass

        plt.show(block=False)
        plt.pause(0.1)

    def update(self, t, x, y, speed_kmh, current_ap):
        idx = min(range(len(self.road_positions)),
                  key=lambda i: (self.road_positions[i][1]-x)**2
                              + (self.road_positions[i][2]-y)**2)
        self.path_trace_done.set_data(self.all_x[:idx+1], self.all_y[:idx+1])
        self.car_marker.set_offsets([[x, y]])
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
# Mobility replay
# =========================================================
def move_car1_real_trace(car1, server1, ap_layout, live_plot=None):
    step_time_s       = 1.0
    last_valid_signal = None
    current_target    = None

    with open(RSSI_LOG_FILE, 'w') as f:
        f.write('time,x,y,target_ap,ap_mac,signal_dBm,speed_kmh\n')

        prev_t, prev_x, prev_y = positions[0]
        start_target = target_ap_by_zone(prev_x, prev_y)

        link_output = ensure_assoc(car1, ap_layout[start_target]['obj'],
                                   retries=4, wait=0.8)
        flush_host_state(car1, server1)
        warmup_connectivity(car1, server1)

        current_target    = start_target
        ap_mac, signal    = parse_link_info(link_output, last_valid_signal)
        last_valid_signal = signal
        car1.setPosition(f'{prev_x},{prev_y},0')

        if live_plot:
            live_plot.update(prev_t, prev_x, prev_y, 0.0, current_target)

        f.write(f'{prev_t},{prev_x},{prev_y},{current_target},{ap_mac},{signal},0.00\n')
        f.flush()

        for t, x, y in positions[1:]:
            time.sleep(max(0, t - prev_t))
            car1.setPosition(f'{x},{y},0')

            speed_kmh = estimate_speed_kmh(prev_x, prev_y, x, y, step_time_s)
            target_ap = target_ap_by_zone(x, y)

            if target_ap != current_target:
                info(f'*** Handover detected: {current_target} -> {target_ap}\n')
                link_output = ensure_assoc(car1, ap_layout[target_ap]['obj'],
                                           retries=4, wait=0.8)
                if 'Connected to' not in link_output:
                    time.sleep(0.3)
                    link_output = ensure_assoc(car1, ap_layout[target_ap]['obj'],
                                               retries=2, wait=0.5)
                flush_host_state(car1, server1)
                warmup_connectivity(car1, server1)
                current_target = target_ap
                time.sleep(HANDOVER_SETTLE_TIME)
            else:
                link_output = get_link_info(car1)
                if 'Connected to' not in link_output:
                    link_output = ensure_assoc(car1, ap_layout[target_ap]['obj'],
                                               retries=4, wait=0.8)
                    flush_host_state(car1, server1)
                    warmup_connectivity(car1, server1)

            ap_mac, signal    = parse_link_info(link_output, last_valid_signal)
            last_valid_signal = signal

            info(f'*** car1 moved to ({x}, {y}, 0) at t={t}\n')
            info(f'*** Target AP: {current_target} | AP MAC: {ap_mac} | '
                 f'Signal: {signal} dBm | Speed: {speed_kmh:.2f} km/h\n')

            if live_plot:
                live_plot.update(t, x, y, speed_kmh, current_target)

            f.write(f'{t},{x},{y},{current_target},{ap_mac},{signal},{speed_kmh:.2f}\n')
            f.flush()
            prev_t, prev_x, prev_y = t, x, y

        _, sx, sy = positions[0]
        car1.setPosition(f'{sx},{sy},0')
        if live_plot:
            live_plot.update(t+1, sx, sy, 0.0, target_ap_by_zone(sx, sy))

    info('*** Real campus mobility finished\n')
    if live_plot:
        live_plot.close()


# =========================================================
# Main topology
# =========================================================
def topology():
    net = Mininet_wifi(
        controller=RemoteController,
        link=wmediumd,
        wmediumd_mode=interference
    )

    info('*** Creating nodes\n')

    c0 = net.addController('c0', controller=RemoteController,
                            ip='127.0.0.1', port=6653)

    _, start_x, start_y = positions[0]

    car1    = net.addStation('car1', ip='10.0.0.1/8',
                             position=f'{start_x},{start_y},0')
    server1 = net.addHost('server1', ip='10.0.0.100/8')

    ap_layout = {
        'ap1': {'x': 58,  'y': 160},
        'ap2': {'x': 142, 'y': 160},
        'ap3': {'x': 138, 'y': 64},
        'ap4': {'x': 56,  'y': 66},
    }

    ap1 = net.addAccessPoint('ap1', ssid='vanet-ssid', mode='g', channel='1',
                             position=f"{ap_layout['ap1']['x']},{ap_layout['ap1']['y']},0",
                             range=str(REAL_WIFI_RANGE_M), protocols='OpenFlow13')
    ap2 = net.addAccessPoint('ap2', ssid='vanet-ssid', mode='g', channel='6',
                             position=f"{ap_layout['ap2']['x']},{ap_layout['ap2']['y']},0",
                             range=str(REAL_WIFI_RANGE_M), protocols='OpenFlow13')
    ap3 = net.addAccessPoint('ap3', ssid='vanet-ssid', mode='g', channel='11',
                             position=f"{ap_layout['ap3']['x']},{ap_layout['ap3']['y']},0",
                             range=str(REAL_WIFI_RANGE_M), protocols='OpenFlow13')
    ap4 = net.addAccessPoint('ap4', ssid='vanet-ssid', mode='g', channel='3',
                             position=f"{ap_layout['ap4']['x']},{ap_layout['ap4']['y']},0",
                             range=str(REAL_WIFI_RANGE_M), protocols='OpenFlow13')

    ap_layout['ap1']['obj'] = ap1
    ap_layout['ap2']['obj'] = ap2
    ap_layout['ap3']['obj'] = ap3
    ap_layout['ap4']['obj'] = ap4

    s1 = net.addSwitch('s1', cls=OVSKernelSwitch, protocols='OpenFlow13')

    net.setPropagationModel(model='logDistance', exp=3)

    info('*** Configuring WiFi nodes\n')
    net.configureWifiNodes()

    info('*** Creating links\n')
    net.addLink(ap1, s1)
    net.addLink(ap2, s1)
    net.addLink(ap3, s1)
    net.addLink(ap4, s1)
    net.addLink(s1, server1)

    info('*** Starting network\n')
    net.build()
    c0.start()
    ap1.start([c0])
    ap2.start([c0])
    ap3.start([c0])
    ap4.start([c0])
    s1.start([c0])

    info('*** Setting interface defaults\n')
    car1.cmd('ip link set car1-wlan0 up')
    server1.cmd('ip link set server1-eth0 up')
    server1.cmd('ip route add default dev server1-eth0')
    info('*** server1 default route set\n')

    info('*** Waiting for RYU (5s)...\n')
    time.sleep(5)

    install_fallback_flows([ap1, ap2, ap3, ap4], [s1])
    set_static_arp(car1, server1)

    # ── nginx บน server1 ───────────────────────────────────────────────────
    info('*** Starting nginx on server1\n')
    server1.cmd('pkill -f nginx > /dev/null 2>&1; sleep 0.5')
    server1.cmd('mkdir -p /run/nginx')
    r = server1.cmd('nginx -c /etc/nginx/nginx.conf 2>&1')
    info(f'*** nginx: {r.strip() or "OK"}\n')
    time.sleep(1)

    # ── WAN delay บน origin port 8080 ─────────────────────────────────────────
    info('*** Adding 200ms delay on origin port 8080\n')
    server1.cmd('tc qdisc add dev lo root handle 1: prio')
    server1.cmd('tc qdisc add dev lo parent 1:3 handle 30: netem delay 200ms')
    server1.cmd('tc filter add dev lo parent 1:0 protocol ip u32 '
                'match ip dport 8080 0xffff flowid 1:3')
    info('*** Origin delay OK\n')
    oc = server1.cmd(f'curl -s -o /dev/null -w "%{{http_code}}" --max-time 3 http://localhost:{ORIGIN_PORT}/')
    ec = server1.cmd(f'curl -s -o /dev/null -w "%{{http_code}}" --max-time 3 http://localhost:{EDGE_PORT}/')
    info(f'*** origin (:{ORIGIN_PORT}): HTTP {oc.strip()}\n')
    info(f'*** edge   (:{EDGE_PORT}):   HTTP {ec.strip()}\n')

    # ── Pre-warm cache ─────────────────────────────────────────────────────
    prewarm_cdn_cache(server1)

    time.sleep(1)
    verify_connectivity(car1, server1)

    try:
        max_x = max(x for _, x, y in positions) + 30
        max_y = max(y for _, x, y in positions) + 30
        net.plotGraph(max_x=max_x, max_y=max_y)
    except Exception as e:
        info(f'*** plotGraph warning: {e}\n')

    disable_mn_wifi_graph_updates(car1)

    live_plot = RealRoadLivePlot(positions, ap_layout,
                                 coverage_radius=PLOT_COVERAGE_RADIUS)
    live_plot.setup()

    def start_mobility():
        info('*** Manual real-road campus mobility started\n')
        move_car1_real_trace(car1, server1, ap_layout, live_plot)

    net.start_mobility = start_mobility

    info('*** Running CLI\n')
    info('*** Commands:\n')
    info('***   py net.start_mobility()\n')
    info(f'***   [MISS] server1 rm -rf /home/kongpop/PSU_Project/cdn/cache/* && server1 nginx -s reload\n')
    info(f'***   [test Video.mp4]  car1 curl -s -o /dev/null -r 0-1 -D - -w"time=%{{time_total}}s\\n" http://{EDGE_IP}:{EDGE_PORT}/{CDN_VIDEO_PATH} | grep -iE "x-cache|time="\n')
    info(f'***   [test Video2.mp4] car1 curl -s -o /dev/null -r 0-1 -D - -w"time=%{{time_total}}s\\n" http://{EDGE_IP}:{EDGE_PORT}/{CDN_VIDEO2_PATH} | grep -iE "x-cache|time="\n')
    info(f'***   [test test.mp4]   car1 curl -s -o /dev/null -D - -w"time=%{{time_total}}s\\n" http://{EDGE_IP}:{EDGE_PORT}/test.mp4 | grep -iE "x-cache|time="\n')
    CLI(net)

    info('*** Stopping network\n')
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    topology()
