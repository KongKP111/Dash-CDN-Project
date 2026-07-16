#!/usr/bin/python3
"""
baseline_4rsu_topo.py  (v1 -- VLC + Ryu, real handover between 4 RSUs)
=======================================================================
One vehicle drives past 4 RSUs in a straight line while a custom Ryu
controller (sdn_controller.py) handles OpenFlow forwarding AND handover
(deletes stale MAC flows so traffic follows the vehicle to its new RSU).

  4x RSU (OVSKernelAP, distinct SSID/channel) -- backbone switch -- server1
  RemoteController (Ryu, custom learning switch w/ handover-aware flows)
  car1 drives START_X -> END_X; at each RSU-overlap crossing we re-associate
  (via fix_assoc.ensure_assoc, NOT mn_wifi's broken setAssociation) to the
  nearest RSU -- a REAL 802.11 handover, not a simulated one.

Same metric collection as the 1-RSU baseline (baseline_topo.py): real RSSI,
real rendition (HTTP access log), protected-ICMP loss, buffer-model
rebuffering, imposed bandwidth. Reuses that file's QualityPoller /
RebufferEstimator / PingLossPoller / tc helpers verbatim (imported, not
duplicated).

Prereqs beyond the 1-RSU baseline:
  - Ryu controller running & reachable at CTRL_IP:CTRL_PORT (see
    run_4rsu_multi.sh for the docker lifecycle, or start it manually):
      sudo docker run -d --restart=always --name ryu-ctrl --network host \\
          -v /tmp:/tmp -v $(pwd)/../Ryu-SDN-Controller/sdn_controller.py:/sdn_controller.py \\
          osrg/ryu ryu-manager /sdn_controller.py --ofp-tcp-listen-port 6653
  - xvfb-run + vlc installed (apt), NOT snap vlc.

Run (headless, unattended):
  cd ~/sdn-cdn-dash-research/dash-baseline
  sudo python3 baseline_4rsu_topo.py --headless --run-id run_01
  python3 plot_4rsu_run.py baseline_4rsu_run.csv

RSSI->bandwidth mapping (--bw-mapping, default "step2h", the finalized
mapping -- see baseline_model.py): pass "linear"/"step"/"step2" for the
earlier candidates instead.

=== Situation 2 (highway mobility speed) changes, copied out of
dash-baseline/ into Situation2_DASH/ so this file could be edited without
touching the frozen baseline -- mirrors CDN_SIT2/cdn_sdn_hight_speed.py's
same fixes so a DASH-vs-CDN comparison at highway speed is apples-to-apples
(see Situation2_DASH/HANDOFF_FROM_CDN_REFERENCE.md):
  - --speed {20,80,100,120} km/h (was hardcoded 20 km/h only)
  - run_loop() drives x/t from real elapsed wall-clock time instead of a
    fixed +=SAMPLE_DT increment, so a handover that can't finish before the
    car leaves an RSU's range now shows up as a measured outage instead of
    being structurally impossible (the car was never actually "moving"
    while reconnecting in the old fixed-increment version)
  - explicit outage/cum_outage_s tracking, one association attempt per
    tick via ensure_assoc(..., retries=1) bounded by --handover-timeout,
    same semantics as the CDN arm's outage/cum_outage_s columns
"""

import os
import sys
import time
import argparse

from mininet.log import setLogLevel, info
from mininet.node import RemoteController
from mn_wifi.net import Mininet_wifi
from mn_wifi.node import OVSKernelAP
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baseline_model as M
import baseline_4rsu_model as M4
import config
from fix_assoc import ensure_assoc
from baseline_topo import (
    QualityPoller, RebufferEstimator, PingLossPoller,
    setup_tc, set_tc, parse_rssi, RUNG_LABEL, HTTP_LOG,
)

USER = config.USER
HOME = config.HOME
CONTENT_DIR = config.CONTENT_DIR
SRV_IP = config.SRV_IP
SRV_PORT = config.SRV_PORT

CTRL_IP = "127.0.0.1"
CTRL_PORT = 6653

# shared with the Ryu controller via the bind-mounted /tmp: tells it which
# run_id to tag rows with in /tmp/handover_times.csv (see sdn_controller.py)
RUN_ID_FILE = "/tmp/current_run_id.txt"

RANGE_M = int(M4.COVERAGE_M) + 50   # small buffer over the nominal coverage radius


def topology(args):
    # computed here (not stored back onto M4) and threaded down as a plain
    # parameter, same pattern as CDN_SIT2's topology()/run_loop_sdn() --
    # keeps baseline_4rsu_model.py's SPEED_KMH/SPEED_MPS constants as pure
    # defaults, never mutated at runtime.
    speed_mps = args.speed / 3.6

    if not os.path.isfile(os.path.join(CONTENT_DIR, "index.mpd")):
        info("!!! %s/index.mpd not found (run the ffmpeg encode first)\n" % CONTENT_DIR)
        sys.exit(1)
    os.system("rm -f %s" % HTTP_LOG)

    net = Mininet_wifi(controller=RemoteController, link=wmediumd,
                        wmediumd_mode=interference)

    info("*** Remote controller (Ryu) at %s:%d\n" % (CTRL_IP, CTRL_PORT))
    c0 = net.addController("c0", controller=RemoteController,
                            ip=CTRL_IP, port=CTRL_PORT)

    aps = []
    for i, x in enumerate(M4.RSU_X):
        name = "ap%d" % (i + 1)
        info("*** RSU %s at x=%d (coverage ~%dm) ssid=%s ch=%s\n"
             % (name, int(x), int(M4.COVERAGE_M), M4.RSU_SSIDS[i], M4.RSU_CHANNELS[i]))
        ap = net.addAccessPoint(name, ssid=M4.RSU_SSIDS[i], mode="g",
                                 channel=M4.RSU_CHANNELS[i],
                                 position="%d,0,0" % int(x), range=RANGE_M,
                                 protocols="OpenFlow13", cls=OVSKernelAP)
        aps.append(ap)

    info("*** Vehicle car1 at x=%d\n" % int(M4.START_X))
    car1 = net.addStation("car1", ip="10.0.0.1/8",
                          position="%d,0,0" % int(M4.START_X), range=RANGE_M)

    info("*** Content server\n")
    server1 = net.addHost("server1", ip="%s/8" % SRV_IP)

    info("*** Backbone switch\n")
    s1 = net.addSwitch("s1", protocols="OpenFlow13")

    info("*** Propagation logDistance exp=%.2f\n" % M4.PATHLOSS_N)
    net.setPropagationModel(model="logDistance", exp=M4.PATHLOSS_N)

    try:
        net.configureWifiNodes()
    except AttributeError:
        net.configureNodes()

    net.addLink(s1, server1)
    for ap in aps:
        net.addLink(s1, ap)

    info("*** Build & start\n")
    net.build()
    c0.start()
    for ap in aps:
        ap.start([c0])
    s1.start([c0])
    time.sleep(3)

    if not args.headless:
        net.plotGraph(min_x=-350, max_x=1850, min_y=-150, max_y=150)

    info("*** Initial association to ap1\n")
    if not ensure_assoc(car1, aps[0]):
        info("!!! Could not associate car1 with ap1 -- aborting\n")
        net.stop()
        sys.exit(1)
    car1.cmd("ping -c2 -W2 %s >/dev/null 2>&1" % SRV_IP)

    os.system("rm -f /tmp/ping.log")
    car1.cmd("pkill -f 'ping -i 0.05' 2>/dev/null")
    car1.cmd("ping -i 0.05 -O %s > /tmp/ping.log 2>&1 &" % SRV_IP)

    srv_if = "server1-eth0"
    setup_tc(server1, srv_if)
    boot_bw = M.LADDER["360p"] * 1.2
    set_tc(server1, srv_if, boot_bw)
    info("*** Bootstrap bandwidth pinned to %.2f Mbps (start-low policy)\n" % boot_bw)

    info("*** HTTP server on %s:%d (log -> %s)\n" % (SRV_IP, SRV_PORT, HTTP_LOG))
    server1.cmd("cd %s && python3 -u -m http.server %d --bind %s >%s 2>&1 &"
                % (CONTENT_DIR, SRV_PORT, SRV_IP, HTTP_LOG))
    time.sleep(1)

    # tag this run for the Ryu controller's handover_times.csv (shared /tmp)
    with open(RUN_ID_FILE, "w") as f:
        f.write(args.run_id)

    # --vlc-verbose: diagnostic only, off by default so normal/batch runs are
    # unaffected. Adds -vvv so VLC's adaptive-demux module logs its actual
    # representation-switch decisions to /tmp/vlc.log (does NOT touch the
    # bandwidth model, ABR thresholds, or anything else experimental).
    verbose_flag = "-vvv " if args.vlc_verbose else ""

    if args.headless:
        info("*** Launching VLC headless (xvfb) as user '%s'%s\n"
             % (USER, " [verbose]" if args.vlc_verbose else ""))
        # -extension GLX (Xvfb) + --avcodec-hw=none/--vout=x11 (VLC): this
        # machine has an NVIDIA GPU whose nouveau driver hangs (D-state,
        # unkillable even by SIGKILL) if VLC's video output probes GLX/DRI.
        # Keeping a real (non-GL) X11 vout is still required -- true
        # headless (no X at all) leaves VLC's ABR stuck at 360p (see README).
        car1.cmd("sudo -u %s env HOME=%s "
                 "xvfb-run -a --server-args='-screen 0 1280x1024x24 -ac -extension GLX' "
                 "vlc -I dummy --no-audio --avcodec-hw=none --vout=x11 --play-and-exit "
                 "%s--adaptive-logic=rate --network-caching=3000 "
                 "'http://%s:%d/index.mpd' >/tmp/vlc.log 2>&1 &"
                 % (USER, HOME, verbose_flag, SRV_IP, SRV_PORT))
    else:
        info("*** Launching VLC as user '%s' (popup)%s\n"
             % (USER, " [verbose]" if args.vlc_verbose else ""))
        car1.cmd("sudo -u %s env DISPLAY=:0 HOME=%s "
                 "vlc --no-qt-privacy-ask --no-video-title-show "
                 "%s--adaptive-logic=rate --network-caching=3000 "
                 "'http://%s:%d/index.mpd' >/tmp/vlc.log 2>&1 &"
                 % (USER, HOME, verbose_flag, SRV_IP, SRV_PORT))

    info("*** Warmup %ds (let VLC buffer)\n" % args.warmup)
    time.sleep(args.warmup)

    run_loop(car1, server1, srv_if, aps, args, speed_mps)

    info("*** Cleaning up\n")
    # plain SIGTERM (pkill's default) does not reliably terminate vlc/Xvfb
    # here -- force-kill both, and give the kernel a moment before net.stop()
    # tears down the netns they were using (an orphaned socket into a
    # just-deleted namespace is how the D-state GPU hangs got triggered).
    car1.cmd("pkill -9 -u %s -f 'vlc -I dummy' 2>/dev/null" % USER)
    car1.cmd("pkill -9 -u %s -x Xvfb 2>/dev/null" % USER)
    car1.cmd("pkill -f 'ping -i 0.05' 2>/dev/null")
    time.sleep(1)
    server1.cmd("pkill -f http.server 2>/dev/null")
    if args.cli:
        CLI(net)
    net.stop()


def run_loop(car1, server1, srv_if, aps, args, speed_mps):
    poller = QualityPoller(HTTP_LOG)
    loss_probe = PingLossPoller()
    rebuf = RebufferEstimator()
    f = open(args.out, "w")
    f.write("t,x,dist,rsu,rssi,rssi_src,bw_mbps,quality,quality_idx,seg,loss,"
            "stall,buffer_s,handover,vehicle_speed_kmh,outage,cum_outage_s\n")

    total = (M4.END_X - M4.START_X) / speed_mps
    info("*** Driving %d->%d m @ %.1f km/h (%.2f m/s, %.0fs), 4 RSUs, bw-mapping=%s\n"
         % (M4.START_X, M4.END_X, args.speed, speed_mps, total, args.bw_mapping))

    # step2h is stateful (Schmitt-trigger dead-band around each step2
    # boundary) -- instantiate once per run, everything else stays a plain
    # per-sample function call via M4.throughput_from_rssi().
    hyst_mapper = M.Step2HysteresisMapper() if args.bw_mapping == "step2h" else None

    cur_rsu = 0   # car1 already associated to aps[0] before warmup
    n_handovers = 0
    cum_outage_s = 0.0
    prev_t = 0.0

    # Handover-in-progress state, carried ACROSS loop iterations -- one
    # association attempt per tick (ensure_assoc(..., retries=1), NOT the
    # old single blocking multi-retry call), so a struggling handover
    # produces one real measurement sample per attempt instead of the car
    # "teleporting" from the last good sample straight to wherever it
    # finally resolves. Same shape as CDN_SIT2's run_loop_sdn().
    handover_active = False
    handover_start_wall = 0.0
    handover_attempts = 0
    handover_from_rsu = -1

    t_start = time.monotonic()

    while True:
        tick_wall_start = time.monotonic()

        # position/time derive from real elapsed wall-clock time, not a
        # fixed per-tick increment -- keeps advancing even while a
        # handover attempt below is still in progress, so a handover that
        # can't complete before the car leaves an RSU's range now shows up
        # as a real, measured outage instead of being structurally
        # impossible (this is the key fix from CDN_SIT2's Step 4).
        t = time.monotonic() - t_start
        x = M4.START_X + t * speed_mps
        if x > M4.END_X:
            break
        dt = t - prev_t
        prev_t = t

        car1.setPosition("%.1f,0,0" % x)
        time.sleep(0.05)

        nearest, _ = M4.nearest_rsu(x)
        outage = False
        handover_flag = 0

        # Trigger a new handover attempt not just when the nearest RSU
        # changed, but also when the link spontaneously dropped while
        # still nominally on the same RSU. Reactive-only trigger, matching
        # Situation1_DASH/CDN_SIT1's own reactive handover (no proactive
        # early-trigger) so every arm in this project uses the same
        # handover-timing methodology.
        if not handover_active:
            needs_handover = (nearest != cur_rsu)
            link_ok = True
            if not needs_handover:
                link = car1.cmd("iw dev %s-wlan0 link" % car1.name)
                link_ok = "Connected to" in link
            if needs_handover or not link_ok:
                handover_active = True
                handover_start_wall = time.monotonic()
                handover_attempts = 0
                handover_from_rsu = cur_rsu
                if needs_handover:
                    info("*** Handover: rsu%d -> rsu%d @ x=%.1f (t=%.1f)\n"
                         % (cur_rsu + 1, nearest + 1, x, t))
                else:
                    info("*** Link lost -- re-associating with rsu%d @ x=%.1f (t=%.1f)\n"
                         % (cur_rsu + 1, x, t))

        if handover_active:
            handover_attempts += 1
            # re-target fresh every attempt -- the car may have drifted to
            # a different RSU's zone by the time attempt #N runs
            target, _ = M4.nearest_rsu(x)
            ok = ensure_assoc(car1, aps[target], retries=1, wait=0.8)
            elapsed = time.monotonic() - handover_start_wall

            if ok:
                handover_active = False
                info("*** Handover confirmed in %.2fs (%d attempts)\n"
                     % (elapsed, handover_attempts))
                if handover_from_rsu != -1 and handover_from_rsu != target:
                    n_handovers += 1
                    handover_flag = 1
                cur_rsu = target
            elif elapsed >= args.handover_timeout:
                handover_active = False
                outage = True
                info("*** OUTAGE: gave up after %.1fs (%d attempts, x=%.1f)\n"
                     % (elapsed, handover_attempts, x))
                # cur_rsu deliberately left unchanged -- next tick
                # re-evaluates nearest_rsu(x) at the further-along position
                # and starts a fresh attempt sequence against whichever RSU
                # that is.
            else:
                # still trying, not yet past the giveup threshold -- every
                # attempt starts with a real disconnect, so this tick is
                # genuinely disconnected too, just not a final give-up yet
                outage = True

        if outage:
            cum_outage_s += dt

        d = abs(x - M4.RSU_X[cur_rsu])
        rssi = parse_rssi(car1.cmd("iw dev %s-wlan0 link" % car1.name))
        src = "live"
        if rssi is None:
            rssi = M4.rssi_from_distance(d); src = "model"

        if outage:
            # verified outage -- don't let the synthetic distance-model
            # RSSI fallback above quietly imply a plausible signal the car
            # isn't actually receiving.
            src = "none"
            bw = 0.0
        elif hyst_mapper is not None:
            bw = hyst_mapper.update(rssi)
        else:
            bw = M4.throughput_from_rssi(rssi, mode=args.bw_mapping)
        set_tc(server1, srv_if, bw)

        qidx, seg, n_new = poller.poll()
        qlabel = RUNG_LABEL.get(qidx, "buffering")
        loss = loss_probe.poll()
        if outage:
            loss = 100.0
        # dt (real elapsed time since the previous sample, from wall-clock
        # t above) drives buffer consumption instead of the old constant
        # M4.SAMPLE_DT -- same wall-clock-realism fix as the position/time
        # derivation above, so a tick stretched by handover retries drains
        # the buffer by how long it actually took, not a fixed nominal step.
        stall, buf = rebuf.update(n_new, dt)
        if outage:
            stall = 1

        f.write("%.1f,%.1f,%.1f,%d,%.2f,%s,%.3f,%s,%d,%d,%.3f,%d,%.1f,%d,%.1f,%d,%.3f\n"
                % (t, x, d, cur_rsu + 1, rssi, src, bw, qlabel, qidx, seg,
                   loss, stall, buf, handover_flag, args.speed,
                   int(outage), cum_outage_s))
        f.flush()

        outage_tag = ""
        if outage:
            outage_tag = "  [OUTAGE-RETRYING]" if handover_active else "  [OUTAGE-GAVEUP]"
        if int(t) % 20 == 0 or outage:
            info("  t=%5.0fs x=%+7.1f rsu%d rssi=%6.1f(%s) bw=%4.1f -> %s%s%s\n"
                 % (t, x, cur_rsu + 1, rssi, src, bw, qlabel,
                    "  [STALL]" if stall and not outage else "", outage_tag))

        used = time.monotonic() - tick_wall_start
        remaining = M4.SAMPLE_DT - used
        if remaining > 0.02:
            time.sleep(remaining)

    f.close()
    info("*** Total rebuffering: %.1f s, outage: %.1f s (%.1f%% of run), "
         "handovers executed: %d\n"
         % (rebuf.total_stall, cum_outage_s, 100.0 * cum_outage_s / max(t, 1e-9),
            n_handovers))
    info("*** CSV -> %s\n" % args.out)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="baseline_4rsu_run.csv")
    p.add_argument("--speed", type=int, default=20, choices=[20, 80, 100, 120],
                   help="vehicle speed in km/h -- same sweep as CDN_SIT2 "
                        "(20 = low-speed baseline point, 80/100/120 = "
                        "highway speeds) so both arms cover identical "
                        "points")
    p.add_argument("--handover-timeout", dest="handover_timeout", type=float,
                   default=8.0,
                   help="max real seconds a handover may keep retrying "
                        "before it's charged as a give-up outage and the "
                        "loop starts a fresh attempt sequence against "
                        "whichever RSU is nearest by then -- same default "
                        "as CDN_SIT2 so neither arm gives up 'easier'")
    p.add_argument("--warmup", type=float, default=15)
    p.add_argument("--cli", action="store_true")
    p.add_argument("--headless", action="store_true",
                   help="no popups: use xvfb+vlc, skip plotGraph "
                        "(for unattended multi-run batches)")
    p.add_argument("--run-id", dest="run_id", default="run_01",
                   help="tag written to %s so the Ryu controller attributes "
                        "handover_times.csv rows to this run" % RUN_ID_FILE)
    p.add_argument("--vlc-verbose", dest="vlc_verbose", action="store_true",
                   help="add -vvv to the VLC command so its adaptive-demux "
                        "module logs representation-switch decisions to "
                        "/tmp/vlc.log -- diagnostic only, off by default, "
                        "does not touch the bandwidth model or ABR logic")
    p.add_argument("--bw-mapping", dest="bw_mapping", default="step2h",
                   choices=["linear", "step", "step2", "step2h"],
                   help="RSSI->bandwidth mapping: 'linear' (original, "
                        "continuous ramp), 'step' (discrete tiers on equal "
                        "RSSI steps -- narrow near the RSU), 'step2' "
                        "(discrete tiers on equal DISTANCE bands, >=18s "
                        "dwell per tier), or 'step2h' (step2 + Schmitt-"
                        "trigger hysteresis around each boundary, damps "
                        "switches caused by live RSSI jitter at an edge) "
                        "-- see baseline_model.py imposed_bandwidth() / "
                        "Step2HysteresisMapper")
    args = p.parse_args()
    setLogLevel("info")
    topology(args)
