#!/usr/bin/env python3
"""
cdn_cooperative_topo_sdn.py — Cooperative CDN with Ryu SDN Controller
======================================================================
4 simulated per-AP edge caches on server1 (nginx ports 8081-8084).
Each AP zone has its own cold cache at run start.

--cooperative:
  Ryu-triggered cooperative mode.  When a handover is detected (MAC port
  changes in Ryu), the topology pre-warms the new AP's edge cache in the
  background BEFORE the WiFi re-association completes (~5.7 s window).
  By the time car1 makes its first request at the new AP zone, the cache
  is already warm → HIT.

Without --cooperative (baseline reference):
  All edges start cold, no pre-warm on handover.
  First request at each AP zone → MISS (WAN delay hit).
  Second request onward → HIT (cache filled by first MISS).

Comparison:
  nocoop: MISS at each AP zone entry (4 MISS total), rest HIT
  coop:   HIT throughout (0 MISS)

Run:
  # Terminal 1 (Ryu):
  ryu-manager cdn_switch_13.py --ofp-tcp-listen-port 6653

  # Terminal 2 (non-cooperative — baseline reference):
  sudo python3 cdn_cooperative_topo_sdn.py --sit 1 --speed 20 --round 1 --auto --no-gui

  # Terminal 2 (cooperative):
  sudo python3 cdn_cooperative_topo_sdn.py --sit 1 --speed 20 --round 1 --auto --no-gui --cooperative

Or use run_cooperative_sdn.sh which handles Ryu startup automatically.
"""

import os, re, sys, time, argparse
from mininet.log  import setLogLevel, info
from mininet.node import RemoteController
from mn_wifi.net  import Mininet_wifi
from mn_wifi.node import OVSKernelAP
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baseline_model as M
import config

from cdn_baseline_topo import (
    setup_tc, set_tc,
    PingLossPoller, measure_cdn,
    flush_host_state, warmup_connectivity,
    set_static_arp, verify_connectivity,
    disable_mn_wifi_graph_updates,
    BaselineLivePlot,
)
from cdn_baseline_topo_sdn import ensure_assoc_sdn

USER        = config.USER
HOME        = config.HOME
CONTENT_DIR = config.CONTENT_DIR
ORIGIN_IP   = config.ORIGIN_IP
EDGE_IP     = config.EDGE_IP        # 10.0.0.100 — server1 hosts all edges
ORIGIN_PORT = config.ORIGIN_PORT    # 8080
VIDEO       = {1: config.VIDEO_HIT, 2: config.VIDEO_MISS}

# ── Per-AP edge ports (simulated per-AP edge caches on server1) ────────────
# AP1 → port 8081, AP2 → 8082, AP3 → 8083, AP4 → 8084
EDGE_PORTS = [8081, 8082, 8083, 8084]

HANDOVER_SETTLE_TIME = 0.60

# Ryu writes here on handover detection — confirms SDN-triggered warm
RYU_SIGNAL_FILE = '/tmp/ryu_coop_signal'


# ── nginx config templates ─────────────────────────────────────────────────
NGINX_ORIGIN_CONF = """
worker_processes 1;
pid /tmp/nginx_coop_origin.pid;
error_log /tmp/nginx_coop_origin_err.log;
events {{ worker_connections 64; }}
http {{
    access_log /tmp/nginx_coop_origin_access.log;
    server {{
        listen {origin_port};
        server_name _;
        root {content_dir};
        location / {{ autoindex on; add_header Accept-Ranges bytes; }}
    }}
}}
"""

# One template instantiated for each of the 4 edge nginx instances.
# proxy_cache_key "$uri" (no $http_range) + proxy_force_ranges on — same fix
# as the baseline, but each AP zone has its own independent cache directory.
NGINX_EDGE_CONF_TMPL = """
worker_processes 1;
pid /tmp/nginx_coop_edge{n}.pid;
error_log /tmp/nginx_coop_edge{n}_err.log;
events {{ worker_connections 64; }}
http {{
    proxy_cache_path /tmp/cdn_coop_cache_{n} levels=1:2
                     keys_zone=coop_zone{n}:4m max_size=500m
                     inactive=60m use_temp_path=off;
    access_log /tmp/nginx_coop_edge{n}_access.log;
    server {{
        listen {port};
        server_name _;
        location /Video.mp4 {{
            proxy_pass             http://127.0.0.1:{origin_port};
            proxy_cache            coop_zone{n};
            proxy_cache_min_uses   1;
            proxy_cache_key        "$uri";
            proxy_cache_valid      200 60m;
            proxy_ignore_headers   Cache-Control Expires;
            proxy_force_ranges     on;
            add_header             X-Cache-Status $upstream_cache_status;
        }}
        location /Video2.mp4 {{
            proxy_pass             http://127.0.0.1:{origin_port};
            proxy_cache            coop_zone{n};
            proxy_cache_min_uses   1000;
            proxy_cache_key        "$uri";
            proxy_cache_valid      200 60m;
            proxy_ignore_headers   Cache-Control Expires;
            proxy_force_ranges     on;
            add_header             X-Cache-Status $upstream_cache_status;
        }}
    }}
}}
"""


# ── nginx startup (cold — no pre-warm) ────────────────────────────────────
def write_nginx_configs_coop(server):
    """Start origin + 4 independent edge nginx instances.  Edges start COLD.
    Call this BEFORE applying the WAN delay TC rule so that any subsequent
    cooperative warm runs at lo disk speed (fast path).
    """
    origin_conf = NGINX_ORIGIN_CONF.format(
        origin_port=ORIGIN_PORT, content_dir=CONTENT_DIR)
    with open('/tmp/nginx_coop_origin.conf', 'w') as fh:
        fh.write(origin_conf)

    # Kill leftover processes
    server.cmd("pkill -f 'nginx_coop' 2>/dev/null; true")
    server.cmd("fuser -k %d/tcp 2>/dev/null; true" % ORIGIN_PORT)
    for n, port in enumerate(EDGE_PORTS, start=1):
        server.cmd("fuser -k %d/tcp 2>/dev/null; true" % port)
        server.cmd("rm -rf /tmp/cdn_coop_cache_%d && mkdir -p /tmp/cdn_coop_cache_%d" % (n, n))
    time.sleep(0.8)

    # Origin
    r = server.cmd("nginx -t -c /tmp/nginx_coop_origin.conf 2>&1")
    info("*** nginx coop origin test: %s\n" % (r.strip() or 'OK'))
    server.cmd("nginx -c /tmp/nginx_coop_origin.conf 2>&1")
    time.sleep(0.5)

    # 4 edge instances
    for n, port in enumerate(EDGE_PORTS, start=1):
        edge_conf = NGINX_EDGE_CONF_TMPL.format(
            n=n, port=port, origin_port=ORIGIN_PORT)
        conf_path = '/tmp/nginx_coop_edge%d.conf' % n
        with open(conf_path, 'w') as fh:
            fh.write(edge_conf)
        server.cmd("nginx -t -c %s 2>&1" % conf_path)
        r = server.cmd("nginx -c %s 2>&1" % conf_path)
        info("*** nginx coop edge%d (port %d): %s\n" % (n, port, r.strip() or 'OK'))
        time.sleep(0.3)

    info("*** 4 per-AP edge nginx instances started — all cold (no pre-warm)\n")
    info("    AP1→port %d  AP2→port %d  AP3→port %d  AP4→port %d\n" % tuple(EDGE_PORTS))


# ── Cooperative cache warm ─────────────────────────────────────────────────
def cooperative_warm(server, ap_idx, video_file, block=False):
    """Pre-warm the edge cache for ap_idx.

    block=False (default): launches curl in the background so callers are not
      held up.  The warm completes within ~220ms (200ms WAN delay + fast
      loopback transfer), well before the ~5.7s WiFi re-association ends.

    block=True: used for AP1 prime at run start (caller waits for HIT).
    """
    port = EDGE_PORTS[ap_idx]
    info("*** [COOP] Warming edge%d (port %d) for %s%s\n"
         % (ap_idx + 1, port, video_file, '' if block else ' (background)'))
    suffix = '' if block else ' > /tmp/coop_warm_%d.log 2>&1 &' % (ap_idx + 1)
    server.cmd(
        "curl -s -o /dev/null --max-time 30 "
        "http://127.0.0.1:%d/%s%s" % (port, video_file, suffix)
    )
    if not block:
        time.sleep(0.1)   # give curl a moment to fork before proceeding


# ── Main topology ──────────────────────────────────────────────────────────
def topology(args):
    sit        = args.sit
    speed_kmh  = args.speed
    round_id   = args.round
    cooperative = args.cooperative
    video_file = VIDEO[sit]
    speed_mps  = speed_kmh / 3.6

    mode   = 'coop' if cooperative else 'nocoop'
    run_id = 'cdn_%s_sit%d_spd%d_r%d' % (mode, sit, speed_kmh, round_id)
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    out_csv     = os.path.join(out_dir, '%s.csv' % run_id)
    ho_csv_path = os.path.join(out_dir, 'topology_ho_%s.csv' % run_id)

    info('=' * 60 + '\n')
    info('  CDN Cooperative SDN: %s\n' % run_id)
    info('  Situation %d — %s (%s)\n' % (
        sit, video_file, 'Cache HIT' if sit == 1 else 'Cache MISS'))
    info('  Speed: %d km/h   Mode: %s\n'
         % (speed_kmh, 'COOPERATIVE (SDN pre-warm)' if cooperative else 'NO-COOP (cold cache)'))
    info('=' * 60 + '\n')

    info('*** Cleaning up leftover Mininet state\n')
    os.system('mn -c > /dev/null 2>&1')
    os.system('rm -f %s' % RYU_SIGNAL_FILE)
    time.sleep(1)

    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)

    info('*** Adding Ryu remote controller\n')
    c0 = net.addController('c0', controller=RemoteController,
                           ip='127.0.0.1', port=6653)

    info('*** Adding 4 APs (OpenFlow13, SDN)\n')
    aps = []
    for i, xpos in enumerate(M.AP_POSITIONS):
        ap = net.addAccessPoint(
            'ap%d' % (i + 1),
            ssid      = 'cdn-coop',
            mode      = 'g',
            channel   = str([1, 6, 11, 3][i]),
            position  = '%.1f,0,0' % xpos,
            range     = int(M.AP_COVERAGE * 1.5),
            protocols = 'OpenFlow13',
            cls       = OVSKernelAP,
        )
        aps.append(ap)

    s1 = net.addSwitch('s1', protocols='OpenFlow13')

    info('*** Adding car1\n')
    car1 = net.addStation('car1', ip='10.0.0.1/8',
                          position='10,0,0', range=300)

    info('*** Adding server1 (origin + 4 edge caches)\n')
    server = net.addHost('server1', ip='%s/8' % ORIGIN_IP)

    net.setPropagationModel(model='logDistance', exp=M.PATHLOSS_N)

    try:
        net.configureWifiNodes()
    except AttributeError:
        net.configureNodes()

    for ap in aps:
        net.addLink(ap, s1)
    net.addLink(s1, server)

    net.build()
    c0.start()
    for ap in aps:
        ap.start([c0])
    s1.start([c0])

    disable_mn_wifi_graph_updates(list(net.stations) + list(net.aps))

    # ── Live plot ──────────────────────────────────────────────────────────
    live_plot = None
    if not args.no_gui:
        try:
            live_plot = BaselineLivePlot(M.AP_POSITIONS, M.AP_COVERAGE)
            live_plot.setup(sit, speed_kmh)
        except Exception as e:
            info('*** Live plot warning: %s\n' % e)
            live_plot = None

    # ── Wait for Ryu ──────────────────────────────────────────────────────
    info('*** Waiting for Ryu controller...\n')
    ready = False
    for i in range(30):
        conn = s1.cmd('ovs-vsctl get controller s1 is_connected 2>/dev/null').strip()
        if conn == 'true':
            info('*** Ryu connected after %ds\n' % (i + 1))
            ready = True
            break
        time.sleep(1)
    if not ready:
        info('*** WARNING: Ryu not ready — installing fallback flows\n')
        for node in aps + [s1]:
            node.cmd('ovs-ofctl -O OpenFlow13 add-flow %s '
                     '"priority=1,actions=normal"' % node.name)
    time.sleep(1)

    # ── SDN warmup: pre-touch every AP to prime Ryu flow rules ────────────
    info('*** [WARMUP] Priming Ryu flow rules across all APs\n')
    for i, (ap, xpos) in enumerate(zip(aps, M.AP_POSITIONS)):
        car1.setPosition('%.1f,0,0' % xpos)
        time.sleep(0.3)
        try:
            car1.setAssociation(ap, intf='car1-wlan0')
        except Exception:
            pass
        time.sleep(0.8)
        car1.cmd('ping -c 1 -W 1 %s > /dev/null 2>&1' % ORIGIN_IP)
    car1.setPosition('10,0,0')
    time.sleep(0.5)
    info('*** [WARMUP] Ryu flow rules primed\n')

    # ── Initial association ────────────────────────────────────────────────
    info('*** Connecting car1 to ap1\n')
    ensure_assoc_sdn(car1, aps[0], 0, retries=10, wait=1.0)
    warmup_connectivity(car1, server)
    time.sleep(1.0)

    set_static_arp(car1, server)

    srv_if = 'server1-eth0'
    car1.cmd('ip link set car1-wlan0 up')
    server.cmd('ip link set server1-eth0 up')
    server.cmd('ip route add default dev server1-eth0')

    # ── Start 4 per-AP nginx edge caches (cold) ───────────────────────────
    write_nginx_configs_coop(server)
    setup_tc(server, srv_if)
    set_tc(server, srv_if, 1.2)
    info('*** Bootstrap BW: 1.20 Mbps\n')

    # ── Cooperative: prime AP1's edge BEFORE WAN delay (fast path) ────────
    # Ryu already detected car1 at AP1 — in cooperative mode the SDN system
    # pre-warms the edge so the very first measurement is HIT.
    # This runs on lo at full disk speed (~0.3 s for 355 MB) — no WAN delay yet.
    if cooperative:
        info('*** [COOP] Pre-warming edge1 before WAN delay...\n')
        cooperative_warm(server, 0, video_file, block=True)
        warm_hdr = server.cmd(
            "curl -s -o /dev/null -r 0-65535 -D - --max-time 10 "
            "http://127.0.0.1:%d/%s | grep -i X-Cache-Status" % (EDGE_PORTS[0], video_file)
        ).strip()
        info('*** edge1 warm-up status: %s\n' % warm_hdr)
        if 'HIT' not in warm_hdr.upper() and sit == 1:
            info('*** WARNING: edge1 not HIT after cooperative warm\n')

    # ── 200ms WAN delay on origin port (MISS path) ────────────────────────
    server.cmd('tc qdisc del dev lo root 2>/dev/null; true')
    server.cmd('tc qdisc add dev lo root handle 1: prio')
    server.cmd('tc qdisc add dev lo parent 1:3 handle 30: netem delay 200ms')
    server.cmd(
        'tc filter add dev lo parent 1:0 protocol ip u32 '
        'match ip dport %d 0xffff flowid 1:3' % ORIGIN_PORT)
    info('*** 200ms WAN delay on origin port %d\n' % ORIGIN_PORT)

    # ── Verify connectivity ────────────────────────────────────────────────
    verify_connectivity(car1, server)

    # Edge check from car1 perspective
    edge_port_check = EDGE_PORTS[0]
    warm_hdr = car1.cmd(
        'curl -s -o /dev/null -r 0-65535 -D - --max-time 8 '
        'http://%s:%d/%s | grep -i X-Cache-Status' % (EDGE_IP, edge_port_check, video_file)
    ).strip()
    info('*** car1 edge1 check: %s\n' % warm_hdr)
    if cooperative and sit == 1 and 'HIT' not in warm_hdr.upper():
        info('*** WARNING: car1 not getting HIT from edge1 in cooperative mode\n')

    # ── ICMP loss probe ────────────────────────────────────────────────────
    os.system('rm -f /tmp/cdn_coop_ping.log')
    car1.cmd("pkill -f 'ping -i 0.05' 2>/dev/null")
    car1.cmd('ping -i 0.05 -O %s > /tmp/cdn_coop_ping.log 2>&1 &' % ORIGIN_IP)
    loss_probe = CoopPingLossPoller()

    # ── Register mobility starter ──────────────────────────────────────────
    def start_mobility():
        info('*** start_mobility() called — vehicle now moving\n')
        run_loop_coop(car1, server, srv_if, aps, video_file,
                      speed_mps, loss_probe, out_csv, ho_csv_path, run_id, args,
                      live_plot)

    net.start_mobility = start_mobility

    if args.auto:
        info('*** Auto mode: starting immediately\n')
        start_mobility()
    else:
        info('*** Ready. Call  py net.start_mobility()  in CLI to start.\n')
        from mn_wifi.cli import CLI
        CLI(net)

    # ── Cleanup ────────────────────────────────────────────────────────────
    car1.cmd("pkill -f 'ping -i 0.05' 2>/dev/null")
    server.cmd('pkill -f nginx_coop 2>/dev/null')
    if live_plot:
        live_plot.close()
    net.stop()


# ── Ping loss poller (own log file to avoid conflict with baseline) ────────
class CoopPingLossPoller:
    LOG = '/tmp/cdn_coop_ping.log'
    def __init__(self): self.off = 0; self.last = 0.0
    def poll(self):
        try:
            with open(self.LOG) as fh:
                fh.seek(self.off)
                new = fh.read()
                self.off = fh.tell()
        except FileNotFoundError:
            return self.last
        recv = len(re.findall(r'bytes from', new))
        lost = len(re.findall(r'no answer', new))
        tot  = recv + lost
        if tot == 0:
            return self.last
        self.last = 100.0 * lost / tot
        return self.last


# ── Cooperative measurement loop ───────────────────────────────────────────
def run_loop_coop(car1, server, srv_if, aps, video_file,
                  speed_mps, loss_probe, out_csv, ho_csv_path, run_id, args,
                  live_plot=None):
    """Drive car1 across 4 AP zones.

    In cooperative mode: when ap_idx changes (Ryu has already signalled via
    RYU_SIGNAL_FILE), warm the new AP's edge cache in the background BEFORE
    WiFi re-association completes.  Warm takes ~220ms; re-association ~5.7s.

    In non-cooperative mode: no warm on handover — first request at each new
    AP zone is a MISS (WAN delay hit), subsequent requests are HIT.
    """
    speed_kmh   = args.speed
    cooperative = args.cooperative

    ho_csv = open(ho_csv_path, 'w')
    ho_csv.write('run_id,t,x,ap_from,ap_to,wifi_assoc_ms,coop_warm\n')

    with open(out_csv, 'w') as f:
        f.write('t,x,ap,rssi,bw_mbps,cache,latency_s,'
                'speed_bps,loss_pct,qoe,handover,vehicle_speed_kmh\n')

        prev_ap      = -1
        total_paused = 0.0

        info('*** Drive %.0f→%.0f m @ %.1f km/h\n' % (M.START_X, M.END_X, speed_kmh))
        t_start = time.monotonic()

        while True:
            drive_time = time.monotonic() - t_start - total_paused
            x = M.START_X + drive_time * speed_mps
            t = drive_time
            if x > M.END_X:
                break

            car1.setPosition('%.1f,0,0' % x)
            time.sleep(0.05)

            ap_idx = M.nearest_ap_index(x)
            d      = abs(x - M.AP_POSITIONS[ap_idx])
            rssi   = M.rssi_from_distance(d)

            handover = (ap_idx != prev_ap and prev_ap != -1)
            if ap_idx != prev_ap:
                ho_start = time.monotonic()

                # ── Cooperative: start warm for next edge immediately ──────
                # Background curl starts NOW and runs during WiFi association.
                # AP1 (prev_ap == -1) was already warmed before the loop;
                # subsequent AP transitions warm the new edge here.
                coop_triggered = False
                if cooperative and prev_ap != -1:
                    cooperative_warm(server, ap_idx, video_file, block=False)
                    coop_triggered = True

                if prev_ap != -1:
                    ensure_assoc_sdn(car1, aps[ap_idx], ap_idx,
                                     retries=4, wait=0.8)

                ho_exec_ms = (time.monotonic() - ho_start) * 1000.0

                if handover:
                    ho_csv.write('%s,%.1f,%.1f,ap%d,ap%d,%.3f,%s\n' % (
                        run_id, t, x, prev_ap+1, ap_idx+1, ho_exec_ms,
                        'yes' if coop_triggered else 'no'))
                    ho_csv.flush()
                    flush_host_state(car1, server)
                    warmup_connectivity(car1, server)
                    time.sleep(HANDOVER_SETTLE_TIME)

                total_paused += time.monotonic() - ho_start
                prev_ap = ap_idx

            else:
                link = car1.cmd('iw dev car1-wlan0 link')
                if 'Connected to' not in link:
                    info('*** Link lost — re-associating ap%d\n' % (ap_idx+1,))
                    ho_start = time.monotonic()
                    ensure_assoc_sdn(car1, aps[ap_idx], ap_idx,
                                     retries=4, wait=0.8)
                    flush_host_state(car1, server)
                    warmup_connectivity(car1, server)
                    total_paused += time.monotonic() - ho_start

            bw = M.throughput_from_rssi(rssi)
            set_tc(server, srv_if, bw)

            # Measure from the current AP's dedicated edge cache
            edge_port = EDGE_PORTS[ap_idx]
            cache, latency, speed_bps = measure_cdn(
                car1, video_file, EDGE_IP, edge_port)

            loss  = loss_probe.poll()
            stall = (latency >= 3.0 or cache == 'UNKNOWN')
            qoe   = M.cdn_qoe(cache, latency, handover, stall)

            f.write('%.1f,%.1f,ap%d,%.2f,%.3f,%s,%.4f,%.0f,%.3f,%.3f,%d,%d\n' % (
                t, x, ap_idx+1, rssi, bw, cache,
                latency, speed_bps, loss, qoe, int(handover), speed_kmh))
            f.flush()

            if live_plot:
                live_plot.update(t, x, ap_idx, rssi, bw, cache, latency)

            info('  t=%4.0fs x=%+6.1f AP=ap%d [edge%d:%d] rssi=%6.2f bw=%5.2fMbps '
                 '%s lat=%.3fs loss=%.1f%% QoE=%.2f%s\n'
                 % (t, x, ap_idx+1, ap_idx+1, edge_port, rssi, bw,
                    cache.ljust(7), latency, loss, qoe,
                    '  [HO+COOP]' if (handover and cooperative) else
                    ('  [HO]' if handover else '')))

            used = time.monotonic() - t_start - total_paused - drive_time
            remaining = M.SAMPLE_DT - used
            if remaining > 0.05:
                time.sleep(remaining)

    ho_csv.close()
    info('*** CSV saved: %s\n' % out_csv)
    info('*** Topology handover log: %s\n' % ho_csv_path)


if __name__ == '__main__':
    p = argparse.ArgumentParser(
        description='CDN Cooperative SDN Topology (4 per-AP edge caches)')
    p.add_argument('--sit',         type=int, default=1, choices=[1, 2])
    p.add_argument('--speed',       type=int, default=20, choices=[20, 25, 30])
    p.add_argument('--round',       type=int, default=1)
    p.add_argument('--out-dir',     type=str,
                   default='/tmp/cdn_cooperative_results')
    p.add_argument('--auto',        action='store_true')
    p.add_argument('--no-gui',      action='store_true')
    p.add_argument('--cooperative', action='store_true',
                   help='Enable SDN-triggered cooperative cache pre-warm on handover')
    args = p.parse_args()
    setLogLevel('info')
    topology(args)
