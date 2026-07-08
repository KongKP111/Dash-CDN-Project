#!/usr/bin/env python3
"""
cdn_baseline_topo.py — CDN Baseline (No-SDN)
=============================================
Single vehicle + 4 APs on a straight 600m road.
APs in standalone mode (no Ryu). nginx edge cache determines HIT/MISS.
Bandwidth profile and measurement loop identical to DASH baseline for
fair comparison.

Network pattern mirrors real_campus_live.py exactly:
  - flush_host_state + warmup_connectivity after every handover
  - set_static_arp before mobility
  - verify_connectivity before mobility
  - prewarm uses -r 0-1 range requests (not full file)
  - update_graph patched to no-op (same disable_mn_wifi_graph_updates)
  - install_fallback_flows on all APs (ovs-ofctl normal, replaces Ryu)
  - ensure_assoc uses setAssociation() (same as CDN main), followed by
    flush + warmup instead of iw connect (which breaks in standalone mode)

Topology:
  AP1(x=0) --- AP2(x=200) --- AP3(x=400) --- AP4(x=600)
               all linked to server1 directly

Situations:
  1 — Video.mp4  (proxy_cache_min_uses=1)    → always HIT after warmup
  2 — Video2.mp4 (proxy_cache_min_uses=1000) → always MISS

Speeds: 20, 25, 30 km/h

Run:
  sudo python3 cdn_baseline_topo.py --sit 1 --speed 20 --round 1
"""

import os, re, sys, time, argparse, types
from mininet.log import setLogLevel, info
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from mn_wifi.net import Mininet_wifi
from mininet.node import OVSKernelSwitch
from mn_wifi.node import OVSKernelAP
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baseline_model as M
import config

USER        = config.USER
HOME        = config.HOME
CONTENT_DIR = config.CONTENT_DIR
ORIGIN_IP   = config.ORIGIN_IP
EDGE_IP     = config.EDGE_IP        # same host as origin (10.0.0.100)
ORIGIN_PORT = config.ORIGIN_PORT
EDGE_PORT   = config.EDGE_PORT
EDGE_PORTS = [8081, 8082, 8083, 8084]  # per-AP edge ports: AP1→8081 … AP4→8084

VIDEO = {1: config.VIDEO_HIT, 2: config.VIDEO_MISS}

HANDOVER_SETTLE_TIME = 0.60   # same as CDN main


# ── nginx config templates ─────────────────────────────────────────────────
NGINX_ORIGIN_CONF = """
worker_processes 1;
pid /tmp/nginx_cdn_baseline_origin.pid;
error_log /tmp/nginx_baseline_origin_err.log;
events {{ worker_connections 64; }}
http {{
    access_log /tmp/nginx_baseline_origin_access.log;
    server {{
        listen {origin_port};
        server_name _;
        root {content_dir};
        location / {{ autoindex on; add_header Accept-Ranges bytes; }}
    }}
}}
"""

# One template instantiated for each of the 4 per-AP edge nginx instances.
# Each AP zone gets its own independent cache directory so caches are isolated
# exactly as they would be on separate edge servers.
# proxy_cache_key "$uri" + proxy_force_ranges: same fix as before (no Range in key).
# /coop_warm/ location: edge-to-edge cooperative CDN channel.
# edge2/3/4: proxy to edge1:8081 (already warm, serves from local cache — no
#   origin access, no WAN delay).  WAN delay rule (dport 8080) is preserved.
# edge1: proxy to origin:8080 (fallback; only used before WAN delay is active).
# rewrite strips /coop_warm so $uri = /{file}, same cache key as regular
# location → car1's GET /{file} finds the staged cache entry → HIT.
NGINX_EDGE_CONF_TMPL = """
worker_processes 1;
pid /tmp/nginx_baseline_edge{n}.pid;
error_log /tmp/nginx_baseline_edge{n}_err.log;
events {{ worker_connections 64; }}
http {{
    proxy_cache_path /tmp/cdn_baseline_cache_{n} levels=1:2
                     keys_zone=baseline_zone{n}:4m max_size=500m
                     inactive=60m use_temp_path=off;
    access_log /tmp/nginx_baseline_edge{n}_access.log;
    server {{
        listen {port};
        server_name _;
        location /Video.mp4 {{
            proxy_pass              http://127.0.0.1:{origin_port};
            proxy_cache             baseline_zone{n};
            proxy_cache_min_uses    1;
            proxy_cache_key         "$uri";
            proxy_cache_valid       200 60m;
            proxy_ignore_headers    Cache-Control Expires;
            proxy_force_ranges      on;
            proxy_cache_lock        on;
            proxy_cache_lock_timeout 60s;
            add_header              X-Cache-Status $upstream_cache_status;
        }}
        location /Video2.mp4 {{
            proxy_pass              http://127.0.0.1:{origin_port};
            proxy_cache             baseline_zone{n};
            proxy_cache_min_uses    1000;
            proxy_cache_key         "$uri";
            proxy_cache_valid       200 60m;
            proxy_ignore_headers    Cache-Control Expires;
            proxy_force_ranges      on;
            proxy_cache_lock        on;
            proxy_cache_lock_timeout 60s;
            add_header              X-Cache-Status $upstream_cache_status;
        }}
        location /coop_warm/ {{
            rewrite ^/coop_warm(.*)$ $1 break;
            proxy_pass              http://127.0.0.1:{coop_upstream};
            proxy_cache             baseline_zone{n};
            proxy_cache_min_uses    1;
            proxy_cache_key         "$uri";
            proxy_cache_valid       200 60m;
            proxy_ignore_headers    Cache-Control Expires;
            proxy_force_ranges      on;
            proxy_cache_lock        on;
            proxy_cache_lock_timeout 60s;
            add_header              X-Cache-Status $upstream_cache_status;
        }}
    }}
}}
"""


# ── nginx startup + prewarm ────────────────────────────────────────────────
def write_nginx_configs(server):
    """Start origin + 4 per-AP edge nginx instances.  Edges start COLD (no pre-warm).

    Each AP zone gets its own cache directory so caches are fully isolated:
      AP1 → port 8081, /tmp/cdn_baseline_cache_1
      AP2 → port 8082, /tmp/cdn_baseline_cache_2
      AP3 → port 8083, /tmp/cdn_baseline_cache_3
      AP4 → port 8084, /tmp/cdn_baseline_cache_4

    No pre-warm here: the no-SDN baseline has no intelligence to proactively
    cache content.  First request at each AP zone = MISS; subsequent = HIT.
    The SDN version (cdn_baseline_topo_sdn.py) adds Ryu-triggered pre-warm
    so car1 always sees HIT.
    """
    origin_conf = NGINX_ORIGIN_CONF.format(
        origin_port=ORIGIN_PORT, content_dir=CONTENT_DIR)
    with open("/tmp/nginx_baseline_origin.conf", "w") as f:
        f.write(origin_conf)

    server.cmd("pkill -f 'nginx_baseline' 2>/dev/null; true")
    server.cmd("pkill -f nginx_cdn_baseline 2>/dev/null; true")
    server.cmd("fuser -k %d/tcp 2>/dev/null; true" % ORIGIN_PORT)
    for n, port in enumerate(EDGE_PORTS, start=1):
        server.cmd("fuser -k %d/tcp 2>/dev/null; true" % port)
        server.cmd(
            "rm -rf /tmp/cdn_baseline_cache_%d && "
            "mkdir -p /tmp/cdn_baseline_cache_%d" % (n, n))
    time.sleep(0.8)

    r = server.cmd("nginx -t -c /tmp/nginx_baseline_origin.conf 2>&1")
    info("*** nginx origin config test: %s\n" % (r.strip() or "OK"))
    server.cmd("nginx -c /tmp/nginx_baseline_origin.conf 2>&1")
    time.sleep(0.5)

    for n, port in enumerate(EDGE_PORTS, start=1):
        # edge1 → origin (before WAN delay is active); edge2/3/4 → edge1 (P2P)
        coop_upstream = ORIGIN_PORT if n == 1 else EDGE_PORTS[0]
        edge_conf = NGINX_EDGE_CONF_TMPL.format(
            n=n, port=port, origin_port=ORIGIN_PORT, coop_upstream=coop_upstream)
        conf_path = "/tmp/nginx_baseline_edge%d.conf" % n
        with open(conf_path, "w") as f:
            f.write(edge_conf)
        server.cmd("nginx -t -c %s 2>&1" % conf_path)
        r = server.cmd("nginx -c %s 2>&1" % conf_path)
        info("*** nginx edge%d (port %d): %s\n" % (n, port, r.strip() or "OK"))
        time.sleep(0.3)

    info("*** 4 per-AP edge nginx instances started\n")
    info("    AP1→%d  AP2→%d  AP3→%d  AP4→%d\n" % tuple(EDGE_PORTS))

    # Pre-warm edge1 (AP1) for Video.mp4 — simulates popular content already
    # cached at the car's starting zone.  AP2-4 stay cold: no-SDN will MISS
    # there (no intelligence to pre-warm); the SDN version warms them on handover.
    info("*** Pre-warming edge1 (AP1, port %d) for Video.mp4...\n" % EDGE_PORTS[0])
    for i in range(1, 3):
        code = server.cmd(
            "curl -s -o /dev/null -w '%%{http_code}' "
            "--max-time 30 http://127.0.0.1:%d/Video.mp4" % EDGE_PORTS[0]
        ).strip()
        info("    warmup req %d/2: HTTP %s\n" % (i, code))
    warm_hdr = server.cmd(
        "curl -s -o /dev/null -r 0-65535 -D - --max-time 10 "
        "http://127.0.0.1:%d/Video.mp4 | grep -i X-Cache-Status" % EDGE_PORTS[0]
    ).strip()
    info("*** edge1 warm status: %s\n" % warm_hdr)
    if "HIT" not in warm_hdr.upper():
        info("*** WARNING: edge1 not HIT after warm — check nginx error logs\n")


# ── tc bandwidth shaping ───────────────────────────────────────────────────
def setup_tc(server, iface):
    """HTB with video class (1:10) + protected ICMP class (1:20)."""
    server.cmd("tc qdisc del dev %s root 2>/dev/null" % iface)
    server.cmd("tc qdisc add dev %s root handle 1: htb default 10" % iface)
    server.cmd("tc class add dev %s parent 1: classid 1:10 htb rate 100mbit ceil 100mbit" % iface)
    server.cmd("tc class add dev %s parent 1: classid 1:20 htb rate 2mbit  ceil 2mbit"   % iface)
    server.cmd("tc filter add dev %s parent 1: protocol ip prio 1 u32 "
               "match ip protocol 1 0xff flowid 1:20" % iface)


def set_tc(server, iface, mbit):
    mbit = max(mbit, 0.3)
    server.cmd("tc class change dev %s parent 1: classid 1:10 htb "
               "rate %.3fmbit ceil %.3fmbit" % (iface, mbit, mbit))


# ── Packet loss probe ──────────────────────────────────────────────────────
class PingLossPoller:
    """ICMP loss probe — identical to DASH baseline."""
    LOG = "/tmp/cdn_baseline_ping.log"
    def __init__(self): self.off = 0; self.last = 0.0
    def poll(self):
        try:
            with open(self.LOG) as f:
                f.seek(self.off); new = f.read(); self.off = f.tell()
        except FileNotFoundError:
            return self.last
        recv = len(re.findall(r"bytes from", new))
        lost = len(re.findall(r"no answer",  new))
        tot  = recv + lost
        if tot == 0: return self.last
        self.last = 100.0 * lost / tot
        return self.last


# ── CDN measurement ────────────────────────────────────────────────────────
def measure_cdn(car1, video_file, edge_ip, edge_port):
    """64 KB range request → (cache_status, latency_s, speed_bps).
    -r 0-65535 downloads only 64 KB so it completes well within max-time 3s
    even at low bandwidth (64KB / 0.3 Mbps = 1.7s).
    Cache key includes $http_range so warmup and measurement match exactly.
    """
    out = car1.cmd(
        "curl -s -o /dev/null -r 0-65535 -D - "
        "-w 'time=%%{time_total} size=%%{size_download}' "
        "--max-time 3 "
        "http://%s:%d/%s" % (edge_ip, edge_port, video_file)
    )
    cache  = "UNKNOWN"
    time_s = 3.0
    speed  = 0.0
    m = re.search(r"X-Cache-Status:\s*(\S+)", out, re.I)
    if m: cache = m.group(1).upper().strip()
    m = re.search(r"time=([\d.]+)", out)
    if m: time_s = float(m.group(1))
    m = re.search(r"size=(\d+)", out)
    if m and time_s > 0:
        speed = int(m.group(1)) * 8 / time_s
    return cache, time_s, speed


# ── Association + post-handover flush ─────────────────────────────────────
def flush_host_state(car1, server):
    """Flush ARP + route cache — identical to CDN main flush_host_state()."""
    info("*** Flushing ARP and route cache\n")
    car1.cmd("ip neigh flush dev car1-wlan0")
    car1.cmd("ip route flush cache")
    server.cmd("ip neigh flush dev server1-eth0")
    server.cmd("ip route flush cache")


def warmup_connectivity(car1, server):
    """ARP + ping warmup — identical to CDN main warmup_connectivity()."""
    car1.cmd("arping -c 2 -I car1-wlan0 %s > /dev/null 2>&1" % ORIGIN_IP)
    car1.cmd("ping -c 2 -W 1 %s > /dev/null 2>&1" % ORIGIN_IP)
    server.cmd("arping -c 2 -I server1-eth0 10.0.0.1 > /dev/null 2>&1")
    server.cmd("ping -c 2 -W 1 10.0.0.1 > /dev/null 2>&1")


def ensure_assoc(car1, ap, ap_idx, server, retries=6, wait=1.5):
    """Associate car1 with target AP, bypassing setAssociation()'s short-circuit.

    associate_infra() calls iw_connect() via pexec (non-blocking) then
    immediately calls setConnected() without waiting for the kernel to
    finish the 802.11 handshake.  So "Connected to" in iw link may not
    appear until 1-3 seconds after the call.

    Strategy:
      1. Force intf.associatedTo = None so associate_infra() always runs
      2. Call associate_infra() once per retry
      3. Wait up to wait=1.5s per attempt (total budget ~9s for 6 retries)
      4. If retries exhausted, poll for 3 more seconds — wmediumd often
         completes the handshake on its own even when iw_connect() timed out
    """
    intf    = car1.wintfs[0]   # car1-wlan0
    ap_intf = ap.wintfs[0]     # target AP radio interface

    link_output = ""
    for attempt in range(1, retries + 1):
        intf.associatedTo = None
        try:
            intf.associate_infra(ap_intf)
        except Exception as e:
            info("*** associate_infra warning: %s\n" % e)
        time.sleep(wait)
        link_output = car1.cmd("iw dev car1-wlan0 link")
        if "Connected to" in link_output:
            info("*** Associated with ap%d (attempt %d)\n" % (ap_idx+1, attempt))
            intf.associatedTo = ap_intf
            return link_output
        info("*** Association attempt %d to ap%d failed\n" % (attempt, ap_idx+1))

    # Fallback: poll for background wmediumd association
    info("*** Polling for background wmediumd association (ap%d)...\n" % (ap_idx+1,))
    for _ in range(8):
        time.sleep(0.5)
        link_output = car1.cmd("iw dev car1-wlan0 link")
        if "Connected to" in link_output:
            info("*** Background association confirmed (ap%d)\n" % (ap_idx+1,))
            intf.associatedTo = ap_intf
            return link_output

    info("*** Could not associate with ap%d\n" % (ap_idx+1,))
    return link_output


def set_static_arp(car1, server):
    """Static ARP entries — identical to CDN main set_static_arp().
    Prevents ARP timeouts causing 100% loss during long runs.
    Must be called AFTER ensure_assoc so car1-wlan0 has its real MAC
    (wmediumd assigns the real MAC only after first association).
    """
    info("*** Setting static ARP entries\n")
    server_mac = server.cmd("cat /sys/class/net/server1-eth0/address").strip()
    car_mac    = car1.cmd("cat /sys/class/net/car1-wlan0/address").strip()

    # Skip if MAC is placeholder (02:00:00:00:00:00) — wmediumd not ready
    if car_mac in ("02:00:00:00:00:00", "", "N/A"):
        info("    WARNING: car1 MAC not ready (%s) — skipping static ARP\n" % car_mac)
        info("    Will rely on dynamic ARP + warmup_connectivity\n")
        return

    if server_mac:
        car1.cmd("arp -s %s %s" % (ORIGIN_IP, server_mac))
        info("    car1    -> %s = %s\n" % (ORIGIN_IP, server_mac))
    if car_mac:
        server.cmd("arp -s 10.0.0.1 %s" % car_mac)
        info("    server1 -> 10.0.0.1 = %s\n" % car_mac)


def verify_connectivity(car1, server):
    """Ping + HTTP check before mobility — from CDN main verify_connectivity()."""
    info("*** Verifying car1 -> server1 connectivity\n")
    result = car1.cmd("ping -c 3 -W 2 %s" % ORIGIN_IP)
    info(result)
    if "0 received" in result or "Unreachable" in result:
        info("*** WARNING: car1 cannot reach server1\n")
    else:
        info("*** Connectivity OK\n")
    r1 = car1.cmd(
        "curl -o /dev/null -s -w 'HTTP %%{http_code} in %%{time_total}s' "
        "--max-time 5 http://%s:%d/" % (ORIGIN_IP, ORIGIN_PORT)
    )
    info("    origin : %s\n" % r1.strip())
    r2 = car1.cmd(
        "curl -o /dev/null -s -w 'HTTP %%{http_code} in %%{time_total}s' "
        "--max-time 5 http://%s:%d/" % (EDGE_IP, EDGE_PORTS[0])
    )
    info("    edge1  : %s\n" % r2.strip())


def install_fallback_flows(aps, switches):
    """ovs-ofctl normal flow on every AP + switch — replaces Ryu for standalone mode.
    Mirrors install_fallback_flows() from CDN main (which passes [s1] as switches).
    """
    info("*** Installing fallback flows (priority=100, action=normal)\n")
    for node in aps + switches:
        result = node.cmd(
            "ovs-ofctl -O OpenFlow13 add-flow %s "
            "\"priority=100,actions=normal\"" % node.name
        )
        info("    %s: %s\n" % (node.name, result.strip() or "OK"))


def disable_mn_wifi_graph_updates(nodes):
    """Patch update_graph() to no-op — from CDN main disable_mn_wifi_graph_updates().
    Prevents crash on plttxt/plt_node/circle when plotGraph() is not called.
    """
    def _noop(*args, **kwargs):
        return None
    for node in nodes:
        node.update_graph = _noop


# ── Live plot ──────────────────────────────────────────────────────────────
class BaselineLivePlot:
    """Straight-road live plot. Style mirrors RealRoadLivePlot from CDN main."""
    AP_COLORS = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']

    def __init__(self, ap_positions, coverage_m):
        self.ap_positions = ap_positions
        self.coverage_m   = coverage_m
        self.fig = self.ax = None
        self.car_marker = self.info_text = self.trav_line = None
        self.trav_xs = []

    def setup(self, sit, speed_kmh):
        self.speed_kmh = speed_kmh
        plt.ion()
        r      = self.coverage_m
        start_x = self.ap_positions[0] - r
        end_x   = self.ap_positions[-1] + r
        xmin = start_x - 20
        xmax = end_x   + 20
        ymin = -r - 40
        ymax =  r + 70
        data_w = xmax - xmin
        data_h = ymax - ymin
        fig_w  = 14.0
        fig_h  = round(fig_w * data_h / data_w, 2)

        self.fig, self.ax = plt.subplots(figsize=(fig_w, fig_h))
        ax = self.ax

        # limits BEFORE set_aspect (CDN main does same in setup())
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        ax.set_aspect('equal', adjustable='box')

        ax.plot([xmin + 10, xmax - 10], [0, 0],
                color='#aaaaaa', linewidth=4, solid_capstyle='round', zorder=1)

        for i, xpos in enumerate(self.ap_positions):
            color = self.AP_COLORS[i % len(self.AP_COLORS)]
            ax.add_patch(Circle(
                (xpos, 0), radius=r,
                fill=True, facecolor='skyblue', edgecolor='red',
                linewidth=2, alpha=0.18, zorder=2
            ))
            ax.scatter(xpos, 0, s=160, marker='s', color=color, zorder=5)
            ax.text(xpos, r + 8, 'AP%d (R=%dm)' % (i+1, int(r)),
                    ha='center', fontsize=9, fontweight='bold', color=color, zorder=6)

        ax.text(start_x, -r - 18, 'START',
                ha='center', fontsize=10, fontweight='bold')
        ax.text(end_x, -r - 18, 'END',
                ha='center', fontsize=10, fontweight='bold')
        ax.plot([start_x, end_x], [0, 0],
                color='#1482c5', linewidth=2.5, alpha=0.4, label='Route', zorder=3)

        self.trav_line, = ax.plot([], [], color='orange', linewidth=3.0,
                                  zorder=4, label='Traversed')
        self.car_marker = ax.scatter(start_x, 0,
                                     s=280, marker='v', color='black',
                                     zorder=7, label='Vehicle')
        self.info_text = ax.text(
            0.02, 0.98, 't=0.0s | speed=0.00 km/h | AP=N/A | car=(%.0f,0)' % start_x,
            transform=ax.transAxes, verticalalignment='top',
            fontsize=10, bbox=dict(boxstyle='round', alpha=0.3), zorder=8)
        ax.text(0.01, 0.03, 'Coverage: 100% of route',
                transform=ax.transAxes, fontsize=9, color='white',
                bbox=dict(boxstyle='round', facecolor='steelblue', alpha=0.8))

        sit_name = 'Cache HIT (Video.mp4)' if sit == 1 else 'Cache MISS (Video2.mp4)'
        ax.set_title('CDN Baseline | Sit %d: %s | Speed: %d km/h'
                     % (sit, sit_name, speed_kmh), fontsize=12)
        ax.set_xlabel('X (m)', fontsize=11)
        ax.set_ylabel('Y (m)', fontsize=11)
        ax.grid(True, alpha=0.25)
        ax.legend(loc='lower right', fontsize=9)
        try:
            self.fig.canvas.manager.set_window_title('CDN Baseline Live View')
        except Exception:
            pass
        plt.tight_layout()
        plt.show(block=False)
        plt.pause(0.1)

    def update(self, t, x, ap_idx, rssi, bw, cache, latency):
        if self.fig is None:
            return
        self.car_marker.set_offsets([[x, 0]])
        self.trav_xs.append(x)
        self.trav_line.set_data(self.trav_xs, [0] * len(self.trav_xs))
        self.info_text.set_text(
            't=%.1fs | speed=%d km/h | AP=ap%d | '
            'RSSI=%.1fdBm | BW=%.2fMbps | %s | lat=%.3fs'
            % (t, self.speed_kmh, ap_idx + 1, rssi, bw,
               cache if cache else '?', latency))
        try:
            self.fig.canvas.draw_idle()
            plt.pause(0.02)
        except Exception:
            pass

    def close(self):
        if self.fig:
            try:
                plt.ioff()
                plt.close('all')
            except Exception:
                pass
            self.fig = None


# ── Main topology ──────────────────────────────────────────────────────────
def topology(args):
    sit        = args.sit
    speed_kmh  = args.speed
    round_id   = args.round
    video_file = VIDEO[sit]
    speed_mps  = speed_kmh / 3.6
    run_id     = "cdn_baseline_sit%d_spd%d_r%d" % (sit, speed_kmh, round_id)
    out_dir    = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    out_csv    = os.path.join(out_dir, "%s.csv" % run_id)

    info("=" * 60 + "\n")
    info("  CDN Baseline: %s\n" % run_id)
    info("  Situation %d — %s (%s)\n" % (sit, video_file,
         "Cache HIT" if sit == 1 else "Cache MISS"))
    info("  Speed: %d km/h (%.3f m/s)\n" % (speed_kmh, speed_mps))
    info("=" * 60 + "\n")

    # Cleanup leftover interfaces from previous runs (equivalent of mn -c)
    # Without this, re-runs crash with "RTNETLINK: File exists"
    info("*** Cleaning up leftover Mininet state\n")
    os.system("mn -c > /dev/null 2>&1")
    time.sleep(1)

    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)

    info("*** Adding 4 APs (standalone, no SDN)\n")
    aps = []
    for i, xpos in enumerate(M.AP_POSITIONS):
        ap = net.addAccessPoint(
            "ap%d" % (i+1),
            ssid    = "cdn-baseline",
            mode    = "g",
            channel = str([1, 6, 11, 3][i]),   # non-overlapping channels
            position= "%.1f,0,0" % xpos,
            range   = int(M.AP_COVERAGE * 1.5),
            failMode= "standalone",
            cls     = OVSKernelAP,
        )
        aps.append(ap)

    # car1 starts at START_X (coverage edge of AP1, same as DASH scenario)
    info("*** Adding car1 (starting at AP1 coverage edge)\n")
    car1 = net.addStation(
        "car1", ip="10.0.0.1/8",
        position="%.1f,0,0" % M.START_X, range=int(M.AP_COVERAGE * 1.5))

    info("*** Adding server1 (origin + edge cache)\n")
    server = net.addHost("server1", ip="%s/8" % ORIGIN_IP)

    net.setPropagationModel(model="logDistance", exp=M.PATHLOSS_N)

    # addSwitch BEFORE configureWifiNodes — exact CDN main order
    s1 = net.addSwitch('s1', cls=OVSKernelSwitch, protocols='OpenFlow13')

    try:
        net.configureWifiNodes()
    except AttributeError:
        net.configureNodes()

    # addLink AFTER configureWifiNodes — exact CDN main order
    for ap in aps:
        net.addLink(ap, s1)
    net.addLink(s1, server)

    net.build()
    for ap in aps:
        ap.start([])
    s1.start([])   # standalone — no controller

    # Patch update_graph() to no-op on all nodes — same as CDN main
    # disable_mn_wifi_graph_updates(). Prevents crash on plttxt/plt_node/circle
    # when plotGraph() is not called.
    disable_mn_wifi_graph_updates(list(net.stations) + list(net.aps))

    # Install fallback flows (action=normal) so frames forward without Ryu
    time.sleep(1)
    install_fallback_flows(aps, [s1])

    # Interface setup — mirrors CDN main topology()
    car1.cmd("ip link set car1-wlan0 up")
    server.cmd("ip link set server1-eth0 up")
    server.cmd("ip route add default dev server1-eth0")

    # ── Live plot ──────────────────────────────────────────────────────────
    live_plot = None
    if not args.no_gui:
        try:
            live_plot = BaselineLivePlot(M.AP_POSITIONS, M.AP_COVERAGE)
            live_plot.setup(sit, speed_kmh)
        except Exception as e:
            info("*** Live plot warning: %s\n" % e)
            live_plot = None

    # ── Initial association + static ARP ──────────────────────────────────
    info("*** Connecting car1 to ap1\n")
    ensure_assoc(car1, aps[0], 0, server, retries=6, wait=1.0)
    # warmup after initial connect so car1 ARP is populated before nginx prewarm
    warmup_connectivity(car1, server)
    time.sleep(1.0)

    # Static ARP — prevents timeout-driven 100% loss mid-run (from CDN main)
    set_static_arp(car1, server)

    # ── nginx + tc ────────────────────────────────────────────────────────
    srv_if = "server1-eth0"
    write_nginx_configs(server)
    setup_tc(server, srv_if)
    boot_bw = 1.2
    set_tc(server, srv_if, boot_bw)
    info("*** Bootstrap BW: %.2f Mbps\n" % boot_bw)

    # ── WAN delay on origin port (MISS path) ──────────────────────────────
    # Mirrors CDN main: prio qdisc on lo with netem on band 3
    server.cmd("tc qdisc del dev lo root 2>/dev/null; true")
    server.cmd("tc qdisc add dev lo root handle 1: prio")
    server.cmd("tc qdisc add dev lo parent 1:3 handle 30: netem delay 200ms")
    server.cmd(
        "tc filter add dev lo parent 1:0 protocol ip u32 "
        "match ip dport %d 0xffff flowid 1:3" % ORIGIN_PORT)
    info("*** 200ms WAN delay on origin port %d\n" % ORIGIN_PORT)

    # ── Verify connectivity before mobility (from CDN main) ───────────────
    verify_connectivity(car1, server)

    # ── Verify car1 can reach edge cache ─────────────────────────────────
    # sit 1: Video.mp4 was cached as full 200 during nginx warmup;
    #        proxy_force_ranges serves the range → should be HIT
    # sit 2: Video2.mp4 has min_uses=1000 → never cached → always MISS
    # Edge check from car1 on AP1's edge
    # sit 1: edge1 was pre-warmed above → expect HIT
    # sit 2: Video2.mp4 min_uses=1000 → always MISS
    video_check = "Video.mp4" if sit == 1 else "Video2.mp4"
    warm_hdr = car1.cmd(
        "curl -s -o /dev/null -r 0-65535 -D - --max-time 8 "
        "http://%s:%d/%s | grep -i X-Cache-Status" % (EDGE_IP, EDGE_PORTS[0], video_check)
    ).strip()
    info("*** car1 edge1 check (%s): %s\n" % (video_check, warm_hdr))
    if sit == 1 and "HIT" not in warm_hdr.upper():
        info("*** WARNING: expected HIT on pre-warmed edge1 — got: %s\n" % warm_hdr)
    elif sit == 2 and "MISS" not in warm_hdr.upper():
        info("*** WARNING: expected MISS for sit 2 (min_uses=1000)\n")

    # ── ICMP loss probe ────────────────────────────────────────────────────
    os.system("rm -f /tmp/cdn_baseline_ping.log")
    car1.cmd("pkill -f 'ping -i 0.05' 2>/dev/null")
    car1.cmd("ping -i 0.05 -O %s > /tmp/cdn_baseline_ping.log 2>&1 &" % ORIGIN_IP)
    loss_probe = PingLossPoller()

    # ── Register mobility starter ──────────────────────────────────────────
    def start_mobility():
        info("*** start_mobility() called — vehicle now moving\n")
        run_loop(car1, server, srv_if, aps, video_file,
                 speed_mps, loss_probe, out_csv, run_id, args, live_plot)

    net.start_mobility = start_mobility

    if args.auto:
        info("*** Auto mode: starting immediately\n")
        start_mobility()
    else:
        info("*** Ready. Call  py net.start_mobility()  in CLI to start.\n")
        from mn_wifi.cli import CLI
        CLI(net)

    # ── Cleanup ────────────────────────────────────────────────────────────
    car1.cmd("pkill -f 'ping -i 0.05' 2>/dev/null")
    server.cmd("pkill -f nginx_cdn_baseline 2>/dev/null")
    if live_plot:
        live_plot.close()
    net.stop()


# ── Measurement loop ───────────────────────────────────────────────────────
def run_loop(car1, server, srv_if, aps, video_file,
             speed_mps, loss_probe, out_csv, run_id, args, live_plot=None):
    """Drive car1 from x=START_X to x=END_X, measure CDN every second."""
    sit       = args.sit
    speed_kmh = args.speed

    with open(out_csv, "w") as f:
        f.write("t,x,ap,rssi,bw_mbps,cache,latency_s,"
                "speed_bps,loss_pct,qoe,handover,vehicle_speed_kmh\n")

        prev_ap      = -1
        total_paused = 0.0   # seconds spent in handover — excluded from x calc
        total        = (M.END_X - M.START_X) / speed_mps

        info("*** Drive %.0f→%.0f m @ %.1f km/h (%.0fs total)\n"
             % (M.START_X, M.END_X, speed_kmh, total))

        # Wall-clock with pause: x = (elapsed - paused) × speed_mps
        # During handover we accumulate total_paused so the vehicle does NOT
        # advance while waiting for WiFi association — speed stays constant.
        t_start = time.monotonic()

        while True:
            drive_time = time.monotonic() - t_start - total_paused
            x = M.START_X + drive_time * speed_mps
            t = drive_time
            if x > M.END_X:
                break

            car1.setPosition("%.1f,0,0" % x)
            time.sleep(0.05)

            # Nearest AP + RSSI
            ap_idx = M.nearest_ap_index(x)
            d      = abs(x - M.AP_POSITIONS[ap_idx])
            rssi   = M.rssi_from_distance(d)

            # Handover — pause clock during association
            handover = (ap_idx != prev_ap and prev_ap != -1)
            if ap_idx != prev_ap:
                ho_start = time.monotonic()
                ensure_assoc(car1, aps[ap_idx], ap_idx, server,
                             retries=4, wait=0.8)
                if handover:
                    flush_host_state(car1, server)
                    warmup_connectivity(car1, server)
                    time.sleep(1.0)
                total_paused += time.monotonic() - ho_start
                prev_ap = ap_idx
            else:
                link = car1.cmd("iw dev car1-wlan0 link")
                if "Connected to" not in link:
                    info("*** Link lost — re-associating with ap%d\n" % (ap_idx+1,))
                    ho_start = time.monotonic()
                    ensure_assoc(car1, aps[ap_idx], ap_idx, server,
                                 retries=4, wait=0.8)
                    flush_host_state(car1, server)
                    warmup_connectivity(car1, server)
                    total_paused += time.monotonic() - ho_start

            # Impose bandwidth
            bw = M.throughput_from_rssi(rssi)
            set_tc(server, srv_if, bw)

            # CDN measurement — use current AP's dedicated edge cache
            cache, latency, speed_bps = measure_cdn(
                car1, video_file, EDGE_IP, EDGE_PORTS[ap_idx])

            # Packet loss
            loss = loss_probe.poll()

            # QoE
            stall = (latency >= 3.0 or cache == "UNKNOWN")
            qoe   = M.cdn_qoe(cache, latency, handover, stall)

            row = "%.1f,%.1f,ap%d,%.2f,%.3f,%s,%.4f,%.0f,%.3f,%.3f,%d,%d\n" % (
                t, x, ap_idx+1, rssi, bw, cache,
                latency, speed_bps, loss, qoe, int(handover), speed_kmh)
            f.write(row)
            f.flush()

            if live_plot:
                live_plot.update(t, x, ap_idx, rssi, bw, cache, latency)

            info("  t=%4.0fs x=%+6.1f AP=ap%d rssi=%6.2f bw=%5.2fMbps "
                 "%s lat=%.3fs loss=%.1f%% QoE=%.2f%s\n"
                 % (t, x, ap_idx+1, rssi, bw, cache.ljust(7),
                    latency, loss, qoe,
                    "  [HO]" if handover else ""))

            # Sleep for remainder of SAMPLE_DT
            used = time.monotonic() - t_start - total_paused - drive_time
            remaining = M.SAMPLE_DT - used
            if remaining > 0.05:
                time.sleep(remaining)

    info("*** CSV saved: %s\n" % out_csv)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="CDN Baseline Topology (No-SDN)")
    p.add_argument("--sit",     type=int, default=1, choices=[1, 2],
                   help="1=Video.mp4(HIT), 2=Video2.mp4(MISS)")
    p.add_argument("--speed",   type=int, default=20, choices=[20, 25, 30],
                   help="Vehicle speed km/h")
    p.add_argument("--round",   type=int, default=1,
                   help="Round number (for multi-run)")
    p.add_argument("--out-dir", type=str,
                   default="/tmp/cdn_baseline_results",
                   help="Output directory for CSV")
    p.add_argument("--auto",    action="store_true",
                   help="Start mobility immediately (no CLI)")
    p.add_argument("--no-gui",  action="store_true",
                   help="Skip live plot (for headless batch runs)")
    args = p.parse_args()
    setLogLevel("info")
    topology(args)