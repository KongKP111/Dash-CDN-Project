#!/usr/bin/env python3
"""
cdn_baseline_topo_sdn.py — CDN Baseline WITH Ryu SDN Controller
================================================================
Identical to cdn_baseline_topo.py EXCEPT:
  - Uses RemoteController (Ryu) instead of standalone APs
  - All APs connected to Ryu via OpenFlow13
  - SDN warmup: pre-touches every AP to prime Ryu flow rules
  - Logs handover_exec_ms to handover_times.csv

Run (start Ryu first in separate terminal):
  ryu-manager cdn_switch_13.py --ofp-tcp-listen-port 6653

Then:
  sudo python3 cdn_baseline_topo_sdn.py --sit 1 --speed 20 --round 1 --auto --no-gui

Or use run_baseline_sdn.sh which handles Ryu startup automatically.

Note: this script's own cleanup step (equivalent to `mn -c`, see
mininet_cleanup_preserving_ryu()) deliberately does NOT kill ryu-manager,
unlike plain `mn -c` — see that function's docstring for why. This is what
makes starting Ryu manually via the plain `ryu-manager` command (above)
safe to use directly, without needing a special invocation.
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
    write_nginx_configs, setup_tc, set_tc,
    PingLossPoller, measure_cdn,
    flush_host_state, warmup_connectivity,
    set_static_arp, verify_connectivity,
    disable_mn_wifi_graph_updates,
    BaselineLivePlot,
    EDGE_PORTS,
)

USER        = config.USER
HOME        = config.HOME
CONTENT_DIR = config.CONTENT_DIR
ORIGIN_IP   = config.ORIGIN_IP
EDGE_IP     = config.EDGE_IP
ORIGIN_PORT = config.ORIGIN_PORT
EDGE_PORT   = config.EDGE_PORT
VIDEO       = {1: config.VIDEO_HIT, 2: config.VIDEO_MISS}

VLC_PLAYER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vlc_player.py')

HANDOVER_SETTLE_TIME = 0.60


def cooperative_warm(server, ap_idx, video_file, block=False):
    """Pre-warm edge{ap_idx+1} via the edge-to-edge cooperative channel.

    Uses the /coop_warm/ nginx location which (for edges 2/3/4) proxies to
    edge1:8081 — already warm, serves the 355 MB from local cache with no
    WAN hop and no TC delay penalty.  This is the true cooperative CDN:
    AP1's edge cache shares content with AP2/3/4's edge caches directly.

    WAN delay (dport 8080) is fully preserved for all edge→origin traffic.
    The rewrite strips /coop_warm so cache key = /$file, identical to the
    regular location → car1's GET /$file → HIT immediately.

    block=False: background; writes done-file for _wait_for_coop_warm gate.
    block=True: blocking; used for AP1 prime at startup.
    """
    port      = EDGE_PORTS[ap_idx]
    done_file = '/tmp/sdn_coop_warm_%d_done' % (ap_idx + 1)
    url       = 'http://127.0.0.1:%d/coop_warm/%s' % (port, video_file)
    info('*** [SDN-COOP] Warming edge%d (port %d) via coop channel for %s%s\n'
         % (ap_idx + 1, port, video_file, '' if block else ' (bg)'))
    if block:
        server.cmd('rm -f %s' % done_file)
        server.cmd(
            'curl -s -o /dev/null --max-time 30 %s '
            '&& echo 1 > %s || echo 0 > %s' % (url, done_file, done_file)
        )
    else:
        server.cmd('rm -f %s' % done_file)
        server.cmd(
            '(curl -s -o /dev/null --max-time 30 %s '
            '&& echo 1 > %s || echo 0 > %s) '
            '> /tmp/sdn_coop_warm_%d.log 2>&1 &'
            % (url, done_file, done_file, ap_idx + 1)
        )
        time.sleep(0.1)


def _wait_for_coop_warm(server, ap_idx, timeout_s=15):
    """Wait for cooperative warm done-file (written by cooperative_warm bg process).

    With the cooperative channel (~3 s warm), this should return in 0-1 iterations.
    Timeout of 15 s is a safety net only.  Called inside the handover pause so
    drive_time does not advance during the wait.
    """
    done_file = '/tmp/sdn_coop_warm_%d_done' % (ap_idx + 1)
    for i in range(timeout_s):
        result = server.cmd('cat %s 2>/dev/null' % done_file).strip()
        if result == '1':
            info('*** [SDN-COOP] edge%d warm complete (%ds)\n' % (ap_idx + 1, i))
            return True
        if result == '0':
            info('*** [SDN-COOP] WARNING edge%d warm curl failed\n' % (ap_idx + 1))
            return False
        info('*** [SDN-COOP] Waiting for edge%d warm... (%ds)\n' % (ap_idx + 1, i))
        time.sleep(1)
    info('*** [SDN-COOP] edge%d warm timed out after %ds\n' % (ap_idx + 1, timeout_s))
    return False


def _vlc_paths(out_dir, run_id):
    """Shared path convention for the VLC control file + telemetry outputs
    of one run — used by vlc_start()/vlc_switch()/vlc_stop()."""
    return {
        'ctrl':  '/tmp/cdn_vlc_ctrl_%s' % run_id,
        'tel':   os.path.join(out_dir, 'vlc_playback_%s.csv' % run_id),
        'evt':   os.path.join(out_dir, 'vlc_events_%s.csv' % run_id),
        'log':   '/tmp/vlc_%s.log' % run_id,
    }


def vlc_start(car1, out_dir, run_id, initial_url, show=False):
    """Launch vlc_player.py inside car1's netns for real playback + buffer/
    stall telemetry, alongside (not instead of) the existing curl-based
    measure_cdn() HIT/MISS probe. Mininet hosts share the real filesystem
    (only network namespaces differ — see PingLossPoller's use of
    /tmp/cdn_baseline_ping.log for the same pattern), so the ctrl file and
    CSV outputs are directly readable/writable from either side.

    show=True opens a real video window (manual/demo runs only — never
    set from run_baseline_sdn.sh/run_baseline_multi_sdn.sh, which stay
    headless). Needs a reachable X display: the DISPLAY this process was
    launched with is forwarded verbatim, and the target X server must
    already trust this process's user (typically root, since this script
    runs under sudo) — e.g. `xhost +si:localuser:root` run once beforehand
    in the owning desktop session. That xhost call is a local X11
    access-control change with real (if narrow) security implications, so
    it's left as a manual step for you to opt into, not something this
    script does automatically.
    """
    paths = _vlc_paths(out_dir, run_id)
    car1.cmd("pkill -f vlc_player.py 2>/dev/null; true")
    if os.path.exists(paths['ctrl']):
        os.remove(paths['ctrl'])
    show_flag = '--show' if show else ''
    env_prefix = 'DISPLAY=%s ' % os.environ['DISPLAY'] if show and os.environ.get('DISPLAY') else ''
    if show and not os.environ.get('DISPLAY'):
        info('*** [VLC] WARNING: --vlc-show requested but no DISPLAY set in this '
             'shell — the video window will likely fail to open\n')
    info('*** [VLC] Starting real playback on car1%s: %s\n'
         % (' (with video window)' if show else '', initial_url))
    car1.cmd(
        '%spython3 %s --run-id %s --initial-ap 1 --initial-url %s '
        '--ctrl-file %s --telemetry-csv %s --events-csv %s %s '
        '> %s 2>&1 &'
        % (env_prefix, VLC_PLAYER_SCRIPT, run_id, initial_url,
           paths['ctrl'], paths['tel'], paths['evt'], show_flag, paths['log'])
    )
    time.sleep(0.3)
    return paths


def vlc_switch(car1, paths, ap_idx, url):
    """Signal vlc_player.py (via the atomic ctrl-file write convention) to
    switch to the new edge's URL. vlc_player.py itself captures/restores
    playback position — this call does not compute or pass position."""
    tmp = paths['ctrl'] + '.tmp'
    with open(tmp, 'w') as f:
        f.write('%d|%s\n' % (ap_idx + 1, url))
    os.replace(tmp, paths['ctrl'])


def vlc_stop(car1):
    """Graceful SIGTERM so vlc_player.py flushes its CSVs before exiting."""
    car1.cmd("pkill -TERM -f vlc_player.py 2>/dev/null; true")
    time.sleep(0.5)


def mininet_cleanup_preserving_ryu():
    """Equivalent to shelling out to `mn -c`, except it does NOT kill any
    process named "ryu-manager".

    `mn -c` (the mininet-wifi `mn` CLI's --clean flag) runs two things in
    sequence: `mininet.clean.cleanup()` then `mn_wifi.clean.cleanup_wifi()`
    (confirmed by reading the installed `mn` script directly). Only the
    first one is the problem: `mininet.clean.Cleanup.cleanup()` hardcodes
    a `killall controller ofprotocol ... ryu-manager` call as part of
    "removing excess controllers" — which matches (and kills) any process
    whose comm name is literally "ryu-manager", i.e. exactly what running
    the plain `ryu-manager` command produces. Since this script always ran
    that cleanup as its first action, it was killing a manually-started
    Ryu controller the instant it launched — confirmed empirically.

    Fix: call `mininet.clean.cleanup()` directly (not via `os.system('mn -c')`)
    with its own `sh()` helper temporarily wrapped to strip "ryu-manager"
    out of any `killall` command before it runs, then call
    `mn_wifi.clean.cleanup_wifi()` unchanged (it never touches ryu-manager
    at all — confirmed by reading its source). Every other part of the
    normal `mn -c` cleanup (stale OVS bridges/datapaths, /tmp junk, X11
    tunnels, mac80211_hwsim, wmediumd, hostapd, etc.) still runs exactly
    as before.
    """
    import mininet.clean as _clean
    import mn_wifi.clean as _clean_wifi

    orig_sh = _clean.sh

    def _sh_preserving_ryu(cmd):
        if 'killall' in cmd and 'ryu-manager' in cmd:
            cmd = cmd.replace('ryu-manager', '')
        return orig_sh(cmd)

    _clean.sh = _sh_preserving_ryu
    try:
        _clean.cleanup()
    finally:
        _clean.sh = orig_sh

    _clean_wifi.cleanup_wifi()


def ensure_assoc_sdn(car1, ap, ap_idx, retries=8, wait=1.0):
    """
    Associate car1 with target AP and verify BSSID matches.
    Checks actual BSSID so we don't claim success when still on the old AP.
    """
    target_mac = ''
    try:
        target_mac = ap.cmd(
            'cat /sys/class/net/%s-wlan1/address' % ap.name).strip().lower()
    except Exception:
        pass

    intf = car1.wintfs[0]
    for attempt in range(1, retries + 1):
        intf.associatedTo = None
        try:
            car1.setAssociation(ap, intf='car1-wlan0')
        except Exception as e:
            info('*** setAssoc warning: %s\n' % e)
        time.sleep(wait)
        link = car1.cmd('iw dev car1-wlan0 link')
        if 'Connected to' in link:
            if not target_mac:
                info('*** Associated with ap%d (attempt %d)\n' % (ap_idx+1, attempt))
                intf.associatedTo = ap.wintfs[0]
                return True
            m = re.search(r'Connected to ([0-9a-f:]{17})', link)
            if m and m.group(1).lower() == target_mac:
                info('*** Associated with ap%d (attempt %d)\n' % (ap_idx+1, attempt))
                intf.associatedTo = ap.wintfs[0]
                return True
            info('*** Attempt %d: connected to wrong AP, retrying\n' % attempt)
        else:
            info('*** Association attempt %d to ap%d failed\n' % (attempt, ap_idx+1))

    # Fallback poll
    info('*** Polling for background association (ap%d)...\n' % (ap_idx+1,))
    for _ in range(8):
        time.sleep(0.5)
        link = car1.cmd('iw dev car1-wlan0 link')
        if 'Connected to' in link:
            info('*** Background association confirmed (ap%d)\n' % (ap_idx+1,))
            intf.associatedTo = ap.wintfs[0]
            return True
    info('*** Could not associate with ap%d\n' % (ap_idx+1,))
    return False


def topology(args):
    sit        = args.sit
    speed_kmh  = args.speed
    round_id   = args.round
    video_file = VIDEO[sit]
    speed_mps  = speed_kmh / 3.6
    run_id     = 'cdn_baseline_sdn_sit%d_spd%d_r%d' % (sit, speed_kmh, round_id)
    out_dir    = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    out_csv    = os.path.join(out_dir, '%s.csv' % run_id)
    ho_csv_path = os.path.join(out_dir, 'topology_ho_%s.csv' % run_id)

    info('=' * 60 + '\n')
    info('  CDN Baseline SDN: %s\n' % run_id)
    info('  Situation %d — %s (%s)\n' % (
        sit, video_file, 'Cache HIT' if sit == 1 else 'Cache MISS'))
    info('  Speed: %d km/h (%.3f m/s)\n' % (speed_kmh, speed_mps))
    info('=' * 60 + '\n')

    info('*** Cleaning up leftover Mininet state (preserving any running Ryu controller)\n')
    mininet_cleanup_preserving_ryu()
    time.sleep(1)

    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)

    info('*** Adding Ryu remote controller\n')
    c0 = net.addController('c0', controller=RemoteController,
                           ip='127.0.0.1', port=6653)

    info('*** Adding 4 APs (OpenFlow13, SDN, non-overlapping channels)\n')
    aps = []
    for i, xpos in enumerate(M.AP_POSITIONS):
        ap = net.addAccessPoint(
            'ap%d' % (i + 1),
            ssid      = 'cdn-baseline',
            mode      = 'g',
            channel   = str([1, 6, 11, 3][i]),   # non-overlapping, same as no-SDN
            position  = '%.1f,0,0' % xpos,
            range     = int(M.AP_COVERAGE * 1.5),
            protocols = 'OpenFlow13',
            cls       = OVSKernelAP,
        )
        aps.append(ap)

    info('*** Adding central switch s1\n')
    s1 = net.addSwitch('s1', protocols='OpenFlow13')

    # car1 starts at START_X (coverage edge of AP1, same as DASH scenario)
    info('*** Adding car1 (starting at AP1 coverage edge)\n')
    car1 = net.addStation('car1', ip='10.0.0.1/8',
                          position='%.1f,0,0' % M.START_X, range=int(M.AP_COVERAGE * 1.5))

    info('*** Adding server1 (origin + edge cache)\n')
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

    # Patch update_graph() to no-op — prevents crash when plotGraph() not called
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

    # ── Wait for Ryu controller ────────────────────────────────────────────
    info('*** Waiting for Ryu controller to be ready...\n')
    ready = False
    for i in range(30):
        conn = s1.cmd('ovs-vsctl get controller s1 is_connected 2>/dev/null').strip()
        if conn == 'true':
            info('*** Ryu connected after %ds\n' % (i + 1))
            ready = True
            break
        time.sleep(1)
    if not ready:
        info('*** WARNING: Ryu may not be ready — proceeding with fallback flows\n')
        for node in aps + [s1]:
            node.cmd('ovs-ofctl -O OpenFlow13 add-flow %s '
                     '"priority=1,actions=normal"' % node.name)
    time.sleep(1)

    # ── SDN warmup: pre-touch every AP to prime Ryu flow rules ────────────
    info('*** [WARMUP] Pre-touching every AP to prime Ryu flow rules\n')
    for i, (ap, xpos) in enumerate(zip(aps, M.AP_POSITIONS)):
        info('*** [WARMUP] ap%d at x=%.0f\n' % (i + 1, xpos))
        car1.setPosition('%.1f,0,0' % xpos)
        time.sleep(0.3)
        try:
            car1.setAssociation(ap, intf='car1-wlan0')
        except Exception:
            pass
        time.sleep(0.8)
        car1.cmd('ping -c 1 -W 1 %s > /dev/null 2>&1' % ORIGIN_IP)
    car1.setPosition('%.1f,0,0' % M.START_X)
    time.sleep(0.5)
    info('*** [WARMUP] Done — Ryu flow rules primed\n')

    # ── Initial association ────────────────────────────────────────────────
    info('*** Connecting car1 to ap1\n')
    ensure_assoc_sdn(car1, aps[0], 0, retries=10, wait=1.0)
    warmup_connectivity(car1, server)
    time.sleep(1.0)

    set_static_arp(car1, server)

    # ── nginx + tc ────────────────────────────────────────────────────────
    srv_if = 'server1-eth0'
    car1.cmd('ip link set car1-wlan0 up')
    server.cmd('ip link set server1-eth0 up')
    server.cmd('ip route add default dev server1-eth0')

    write_nginx_configs(server)
    setup_tc(server, srv_if)
    boot_bw = 1.2
    set_tc(server, srv_if, boot_bw)
    info('*** Bootstrap BW: %.2f Mbps\n' % boot_bw)

    # ── SDN cooperative: prime ALL 4 edges BEFORE WAN delay (sit 1 only) ──
    # Video.mp4 is "popular" content — a real CDN would already have it
    # distributed to every edge PoP, not just the vehicle's starting zone.
    # Safe to warm all 4 before the WAN-delay tc rule below: edge1 proxies
    # to origin (fast, pre-WAN-delay) and edge2-4 proxy to edge1 over
    # loopback (cooperative_warm()), never touching origin/WAN delay either.
    # sit 2 = unpopular content (Video2.mp4, min_uses=1000) — never cached,
    # cooperative warm must NOT run or it would bypass min_uses via /coop_warm/.
    if sit == 1:
        info('*** [SDN-COOP] Pre-warming all 4 edges (popular content) before WAN delay...\n')
        for warm_idx in range(4):
            cooperative_warm(server, warm_idx, VIDEO[sit], block=True)
            warm_check = server.cmd(
                'curl -s -o /dev/null -r 0-65535 -D - --max-time 10 '
                'http://127.0.0.1:%d/%s | grep -i X-Cache-Status'
                % (EDGE_PORTS[warm_idx], VIDEO[sit])
            ).strip()
            info('*** edge%d warm status: %s\n' % (warm_idx + 1, warm_check))
            if 'HIT' not in warm_check.upper():
                info('*** WARNING: edge%d not HIT after pre-warm\n' % (warm_idx + 1))
    else:
        info('*** [SDN-COOP] sit 2 — skipping cooperative warm (unpopular content)\n')

    # ── 200ms WAN delay on origin (MISS path) ─────────────────────────────
    server.cmd('tc qdisc del dev lo root 2>/dev/null; true')
    server.cmd('tc qdisc add dev lo root handle 1: prio')
    server.cmd('tc qdisc add dev lo parent 1:3 handle 30: netem delay 200ms')
    server.cmd(
        'tc filter add dev lo parent 1:0 protocol ip u32 '
        'match ip dport %d 0xffff flowid 1:3' % ORIGIN_PORT)
    info('*** 200ms WAN delay on origin port %d\n' % ORIGIN_PORT)

    # ── Verify car1 can reach edge1 ───────────────────────────────────────
    # sit 1: edge1 was pre-warmed by SDN cooperative warm above → expect HIT
    # sit 2: Video2.mp4 min_uses=1000, never cached → always MISS
    video_check = 'Video.mp4' if sit == 1 else 'Video2.mp4'
    warm_hdr = car1.cmd(
        'curl -s -o /dev/null -r 0-65535 -D - --max-time 8 '
        'http://%s:%d/%s | grep -i X-Cache-Status' % (EDGE_IP, EDGE_PORTS[0], video_check)
    ).strip()
    info('*** car1 edge1 check (%s): %s\n' % (video_check, warm_hdr))
    if sit == 1 and 'HIT' not in warm_hdr.upper():
        info('*** WARNING: car1 not getting HIT from edge1 (SDN coop)\n')
    elif sit == 2 and 'MISS' not in warm_hdr.upper():
        info('*** WARNING: car1 not getting MISS for sit 2\n')

    verify_connectivity(car1, server)

    # ── ICMP loss probe ────────────────────────────────────────────────────
    os.system('rm -f /tmp/cdn_baseline_ping.log')
    car1.cmd("pkill -f 'ping -i 0.05' 2>/dev/null")
    car1.cmd('ping -i 0.05 -O %s > /tmp/cdn_baseline_ping.log 2>&1 &' % ORIGIN_IP)
    loss_probe = PingLossPoller()
    car1.cmd("pkill -f vlc_player.py 2>/dev/null; true")  # clear any stale process from a crashed prior run

    # ── Register mobility starter ──────────────────────────────────────────
    def start_mobility():
        info('*** start_mobility() called — vehicle now moving\n')
        run_loop_sdn(car1, server, srv_if, aps, video_file, sit,
                     speed_mps, loss_probe, out_csv, ho_csv_path, run_id, args,
                     live_plot)

    net.start_mobility = start_mobility

    if args.auto:
        info('*** Auto mode: starting mobility immediately\n')
        start_mobility()
    else:
        info('*** Ready. Call  py net.start_mobility()  in CLI to start.\n')
        from mn_wifi.cli import CLI
        CLI(net)

    # ── Cleanup ────────────────────────────────────────────────────────────
    car1.cmd("pkill -f 'ping -i 0.05' 2>/dev/null")
    car1.cmd("pkill -TERM -f vlc_player.py 2>/dev/null")  # safety net for non-auto/CLI exit
    server.cmd('pkill -f nginx_baseline 2>/dev/null')
    if live_plot:
        live_plot.close()
    net.stop()


def run_loop_sdn(car1, server, srv_if, aps, video_file, sit,
                 speed_mps, loss_probe, out_csv, ho_csv_path, run_id, args,
                 live_plot=None):
    """Main measurement loop — wall-clock timing (same as no-SDN) + wifi_assoc_ms logging."""
    speed_kmh = args.speed
    do_coop = (sit == 1)  # cooperative warm only for popular content

    ho_csv = open(ho_csv_path, 'w')
    ho_csv.write('run_id,t,x,ap_from,ap_to,wifi_assoc_ms\n')

    with open(out_csv, 'w') as f:
        f.write('t,x,ap,rssi,bw_mbps,cache,latency_s,'
                'speed_bps,loss_pct,qoe,handover,vehicle_speed_kmh\n')

        prev_ap      = -1
        total_paused = 0.0
        total        = (M.END_X - M.START_X) / speed_mps

        info('*** Drive %.0f→%.0f m @ %.1f km/h (%.0fs total)\n'
             % (M.START_X, M.END_X, speed_kmh, total))

        out_dir = os.path.dirname(out_csv)
        vlc_paths = vlc_start(
            car1, out_dir, run_id,
            'http://%s:%d/%s' % (EDGE_IP, EDGE_PORTS[0], video_file),
            show=args.vlc_show)

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

                # SDN cooperative: pre-warm next edge NOW, before association.
                # Background curl completes in ~220 ms (200 ms WAN + loopback).
                # WiFi re-association takes ~5.7 s → cache is hot before car1
                # makes its first request at the new AP zone.
                if handover and do_coop:
                    cooperative_warm(server, ap_idx, video_file, block=False)

                if prev_ap != -1:  # skip on first tick — topology() already ensured AP1
                    ensure_assoc_sdn(car1, aps[ap_idx], ap_idx, retries=4, wait=0.8)
                ho_exec_ms = (time.monotonic() - ho_start) * 1000.0
                if handover:
                    ho_csv.write('%s,%.1f,%.1f,ap%d,ap%d,%.3f\n' % (
                        run_id, t, x, prev_ap+1, ap_idx+1, ho_exec_ms))
                    ho_csv.flush()
                    flush_host_state(car1, server)
                    warmup_connectivity(car1, server)
                    time.sleep(HANDOVER_SETTLE_TIME)
                    if do_coop:
                        _wait_for_coop_warm(server, ap_idx)
                    vlc_switch(car1, vlc_paths, ap_idx,
                               'http://%s:%d/%s' % (EDGE_IP, EDGE_PORTS[ap_idx], video_file))
                total_paused += time.monotonic() - ho_start
                prev_ap = ap_idx
            else:
                link = car1.cmd('iw dev car1-wlan0 link')
                if 'Connected to' not in link:
                    info('*** Link lost — re-associating with ap%d\n' % (ap_idx+1,))
                    ho_start = time.monotonic()
                    ensure_assoc_sdn(car1, aps[ap_idx], ap_idx, retries=4, wait=0.8)
                    flush_host_state(car1, server)
                    warmup_connectivity(car1, server)
                    total_paused += time.monotonic() - ho_start

            bw = M.throughput_from_rssi(rssi)
            set_tc(server, srv_if, bw)

            cache, latency, speed_bps = measure_cdn(
                car1, video_file, EDGE_IP, EDGE_PORTS[ap_idx])

            loss  = loss_probe.poll()
            stall = (latency >= 3.0 or cache == 'UNKNOWN')
            qoe   = M.cdn_qoe(cache, latency, handover, stall)

            f.write('%.1f,%.1f,ap%d,%.2f,%.3f,%s,%.4f,%.0f,%.3f,%.3f,%d,%d\n' % (
                t, x, ap_idx+1, rssi, bw, cache,
                latency, speed_bps, loss, qoe, int(handover), speed_kmh))
            f.flush()

            if live_plot:
                live_plot.update(t, x, ap_idx, rssi, bw, cache, latency)

            info('  t=%4.0fs x=%+6.1f AP=ap%d rssi=%6.2f bw=%5.2fMbps '
                 '%s lat=%.3fs loss=%.1f%% QoE=%.2f%s\n'
                 % (t, x, ap_idx+1, rssi, bw, cache.ljust(7),
                    latency, loss, qoe,
                    '  [HO]' if handover else ''))

            used = time.monotonic() - t_start - total_paused - drive_time
            remaining = M.SAMPLE_DT - used
            if remaining > 0.05:
                time.sleep(remaining)

    vlc_stop(car1)
    ho_csv.close()
    info('*** CSV saved: %s\n' % out_csv)
    info('*** Topology handover log: %s\n' % ho_csv_path)
    info('*** VLC playback telemetry: %s\n' % vlc_paths['tel'])
    info('*** VLC events log: %s\n' % vlc_paths['evt'])


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='CDN Baseline Topology WITH Ryu SDN')
    p.add_argument('--sit',     type=int, default=1, choices=[1, 2])
    p.add_argument('--speed',   type=int, default=20, choices=[20, 25, 30])
    p.add_argument('--round',   type=int, default=1)
    p.add_argument('--out-dir', type=str,
                   default='/tmp/cdn_baseline_sdn_results')
    p.add_argument('--auto',    action='store_true')
    p.add_argument('--no-gui',  action='store_true')
    p.add_argument('--vlc-show', action='store_true',
                    help='Open a real video window for the VLC playback '
                         '(manual/demo runs only — needs a reachable X '
                         'display; run_baseline_sdn.sh never passes this, '
                         'batch runs stay headless).')
    args = p.parse_args()
    setLogLevel('info')
    topology(args)
