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

    if args.headless:
        info("*** Launching VLC headless (xvfb) as user '%s'\n" % USER)
        # -extension GLX (Xvfb) + --avcodec-hw=none/--vout=x11 (VLC): this
        # machine has an NVIDIA GPU whose nouveau driver hangs (D-state,
        # unkillable even by SIGKILL) if VLC's video output probes GLX/DRI.
        # Keeping a real (non-GL) X11 vout is still required -- true
        # headless (no X at all) leaves VLC's ABR stuck at 360p (see README).
        car1.cmd("sudo -u %s env HOME=%s "
                 "xvfb-run -a --server-args='-screen 0 1280x1024x24 -ac -extension GLX' "
                 "vlc -I dummy --no-audio --avcodec-hw=none --vout=x11 --play-and-exit "
                 "--adaptive-logic=rate --network-caching=3000 "
                 "'http://%s:%d/index.mpd' >/tmp/vlc.log 2>&1 &"
                 % (USER, HOME, SRV_IP, SRV_PORT))
    else:
        info("*** Launching VLC as user '%s' (popup)\n" % USER)
        car1.cmd("sudo -u %s env DISPLAY=:0 HOME=%s "
                 "vlc --no-qt-privacy-ask --no-video-title-show "
                 "--adaptive-logic=rate --network-caching=3000 "
                 "'http://%s:%d/index.mpd' >/tmp/vlc.log 2>&1 &"
                 % (USER, HOME, SRV_IP, SRV_PORT))

    info("*** Warmup %ds (let VLC buffer)\n" % args.warmup)
    time.sleep(args.warmup)

    run_loop(car1, server1, srv_if, aps, args)

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


def run_loop(car1, server1, srv_if, aps, args):
    poller = QualityPoller(HTTP_LOG)
    loss_probe = PingLossPoller()
    rebuf = RebufferEstimator()
    f = open(args.out, "w")
    f.write("t,x,dist,rsu,rssi,rssi_src,bw_mbps,quality,quality_idx,seg,loss,stall,buffer_s,handover\n")

    t = 0.0
    x = M4.START_X
    total = (M4.END_X - M4.START_X) / M4.SPEED_MPS
    info("*** Driving %d->%d m @ %.2f m/s (%.0fs), 4 RSUs\n"
         % (M4.START_X, M4.END_X, M4.SPEED_MPS, total))

    cur_rsu = 0   # car1 already associated to aps[0] before warmup
    n_handovers = 0

    while x <= M4.END_X + 1e-9:
        car1.setPosition("%.1f,0,0" % x)
        time.sleep(args.settle)

        nearest, _ = M4.nearest_rsu(x)
        handover_flag = 0
        if nearest != cur_rsu:
            info("*** Handover: rsu%d -> rsu%d @ x=%.1f (t=%.1f)\n"
                 % (cur_rsu + 1, nearest + 1, x, t))
            t_ho = time.time()
            ok = ensure_assoc(car1, aps[nearest])
            if ok:
                info("*** Handover confirmed in %.2fs\n" % (time.time() - t_ho))
                cur_rsu = nearest
                n_handovers += 1
                handover_flag = 1
            else:
                info("*** WARNING: handover to rsu%d failed, staying on rsu%d\n"
                     % (nearest + 1, cur_rsu + 1))

        d = abs(x - M4.RSU_X[cur_rsu])
        rssi = parse_rssi(car1.cmd("iw dev %s-wlan0 link" % car1.name))
        src = "live"
        if rssi is None:
            rssi = M4.rssi_from_distance(d); src = "model"

        bw = M4.throughput_from_rssi(rssi)
        set_tc(server1, srv_if, bw)

        qidx, seg, n_new = poller.poll()
        qlabel = RUNG_LABEL.get(qidx, "buffering")
        loss = loss_probe.poll()
        stall, buf = rebuf.update(n_new, M4.SAMPLE_DT)

        f.write("%.1f,%.1f,%.1f,%d,%.2f,%s,%.3f,%s,%d,%d,%.3f,%d,%.1f,%d\n"
                % (t, x, d, cur_rsu + 1, rssi, src, bw, qlabel, qidx, seg,
                   loss, stall, buf, handover_flag))
        f.flush()

        if int(t) % 20 == 0:
            info("  t=%5.0fs x=%+7.1f rsu%d rssi=%6.1f(%s) bw=%4.1f -> %s%s\n"
                 % (t, x, cur_rsu + 1, rssi, src, bw, qlabel,
                    "  [STALL]" if stall else ""))

        t += M4.SAMPLE_DT
        x += M4.SPEED_MPS * M4.SAMPLE_DT
        extra = M4.SAMPLE_DT - args.settle
        if extra > 0:
            time.sleep(extra)

    f.close()
    info("*** Total rebuffering: %.1f s, handovers executed: %d\n"
         % (rebuf.total_stall, n_handovers))
    info("*** CSV -> %s\n" % args.out)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="baseline_4rsu_run.csv")
    p.add_argument("--settle", type=float, default=0.1)
    p.add_argument("--warmup", type=float, default=15)
    p.add_argument("--cli", action="store_true")
    p.add_argument("--headless", action="store_true",
                   help="no popups: use xvfb+vlc, skip plotGraph "
                        "(for unattended multi-run batches)")
    p.add_argument("--run-id", dest="run_id", default="run_01",
                   help="tag written to %s so the Ryu controller attributes "
                        "handover_times.csv rows to this run" % RUN_ID_FILE)
    args = p.parse_args()
    setLogLevel("info")
    topology(args)
