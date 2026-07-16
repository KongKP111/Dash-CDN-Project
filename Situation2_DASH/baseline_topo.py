#!/usr/bin/python3
"""
baseline_topo.py  (v3 -- VLC, no controller, simple)
====================================================
Single vehicle + single RSU. VLC streams the real 3-rung BBB DASH ladder and
does the ABR; we record the rendition it actually fetches.

  topology + plotGraph (pops layout)  ->  HTTP server serves bbb_3rung
  ->  VLC pops & plays (as user, not root)
  ->  vehicle drives -300..+300 @ 1 m/s
  ->  distance -> real RSSI -> bandwidth (tc) -> VLC adapts
  ->  quality from HTTP access log (which chunk-streamN is fetched)
  ->  CSV -> plot_run.py

Run (ONE terminal, no Docker/Ryu, no xhost needed):
  cd ~/sdn-cdn-dash-research/dash-baseline
  sudo python3 baseline_topo.py
  python3 plot_run.py baseline_run.csv
"""

import os
import re
import sys
import time
import argparse

from mininet.log import setLogLevel, info
from mn_wifi.net import Mininet_wifi
from mn_wifi.node import OVSKernelAP
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baseline_model as M
import config

USER = config.USER
HOME = config.HOME
CONTENT_DIR = config.CONTENT_DIR
SRV_IP = config.SRV_IP
SRV_PORT = config.SRV_PORT
HTTP_LOG = "/tmp/dashsrv.log"

# rung index (chunk-streamN) -> label   (matches bbb_3rung/index.mpd)
RUNG_LABEL = {0: "360p", 1: "720p", 2: "1080p"}


def parse_rssi(output):
    m = re.search(r"signal:\s*(-?\d+)\s*dBm", output)
    if not m:
        return None
    try:
        v = int(m.group(1))
    except ValueError:
        return None
    if v > 0 or v < -100:
        return None
    return float(v)


def ensure_assoc(car1, ap, retries=3, wait=1.5):
    for attempt in range(1, retries + 1):
        try:
            car1.setAssociation(ap, intf="%s-wlan0" % car1.name)
        except Exception as e:
            info("*** setAssociation warning: %s\n" % e)
        time.sleep(wait)
        if "Connected to" in car1.cmd("iw dev %s-wlan0 link" % car1.name):
            info("*** Associated with %s (attempt %d)\n" % (ap.name, attempt))
            return True
    info("*** Could not confirm association with %s\n" % ap.name)
    return False


class QualityPoller:
    """current quality = last chunk-streamN seen in the HTTP access log.
    Also reports how many NEW segments were fetched in the window (for the
    rebuffering buffer model)."""
    def __init__(self, path):
        self.path = path; self.off = 0; self.cur = -1; self.last_seg = -1
    def poll(self):
        n_new = 0
        try:
            with open(self.path) as f:
                f.seek(self.off); new = f.read(); self.off = f.tell()
        except FileNotFoundError:
            return self.cur, self.last_seg, 0
        for m in re.finditer(r"chunk-stream(\d)-(\d+)\.m4s", new):
            self.cur = int(m.group(1)); self.last_seg = int(m.group(2))
            n_new += 1
        return self.cur, self.last_seg, n_new


class RebufferEstimator:
    """Standard playback-buffer model driven by REAL segment fetches.
    Each fetched segment adds SEG_DUR seconds of playable media; playback
    consumes dt per tick. Playback starts once START_BUFFER is reached. If the
    buffer empties while playing -> a rebuffering (stall) second is recorded."""
    SEG_DUR = 4.0          # ffmpeg seg_duration in bbb_3rung
    START_BUFFER = 8.0     # 2 segments buffered before playback begins
    MAX_BUFFER = 30.0      # players stop fetching once the buffer is full
    def __init__(self):
        self.buffer = 0.0; self.playing = False; self.total_stall = 0.0
    def update(self, n_new_segments, dt):
        self.buffer = min(self.buffer + n_new_segments * self.SEG_DUR,
                          self.MAX_BUFFER)
        if not self.playing:
            if self.buffer >= self.START_BUFFER:
                self.playing = True
            return 0, self.buffer            # startup phase, not a stall
        if self.buffer >= dt:
            self.buffer -= dt
            return 0, self.buffer            # smooth playback
        self.buffer = 0.0
        self.total_stall += dt
        return 1, 0.0                        # buffer underrun -> stall


class PingLossPoller:
    """Real ICMP packet loss on the wireless path, measured per 1s window.
    Reads the running `ping -O` log: each window counts replies vs misses."""
    LOG = "/tmp/ping.log"
    def __init__(self):
        self.off = 0; self.last = 0.0
    def poll(self):
        try:
            with open(self.LOG) as f:
                f.seek(self.off); new = f.read(); self.off = f.tell()
        except FileNotFoundError:
            return self.last
        recv = len(re.findall(r"bytes from", new))
        lost = len(re.findall(r"no answer", new))
        tot = recv + lost
        if tot == 0:               # no probes landed in this window
            return self.last       # carry previous value
        self.last = 100.0 * lost / tot
        return self.last


def setup_tc(server, iface):
    """Two HTB classes on the server downlink:
       1:10  video  -> shaped, changes with distance (the imposed profile)
       1:20  ICMP   -> protected, fixed rate so the loss probe is NEVER starved
                       by video congestion. => measured loss = pure wireless
                       reliability (Way 1), not queue contention."""
    server.cmd("tc qdisc del dev %s root 2>/dev/null" % iface)
    server.cmd("tc qdisc add dev %s root handle 1: htb default 10" % iface)
    server.cmd("tc class add dev %s parent 1: classid 1:10 htb rate 100mbit ceil 100mbit" % iface)
    server.cmd("tc class add dev %s parent 1: classid 1:20 htb rate 2mbit ceil 2mbit" % iface)
    # send all ICMP into the protected class 1:20
    server.cmd("tc filter add dev %s parent 1: protocol ip prio 1 u32 "
               "match ip protocol 1 0xff flowid 1:20" % iface)


def set_tc(server, iface, mbit):
    """Update ONLY the video class (1:10); the ICMP class stays protected."""
    mbit = max(mbit, 0.3)
    server.cmd("tc class change dev %s parent 1: classid 1:10 htb rate %.3fmbit ceil %.3fmbit"
               % (iface, mbit, mbit))


def topology(args):
    if not os.path.isfile(os.path.join(CONTENT_DIR, "index.mpd")):
        info("!!! %s/index.mpd not found (run the ffmpeg encode first)\n" % CONTENT_DIR)
        sys.exit(1)
    os.system("rm -f %s" % HTTP_LOG)

    # no controller -- AP forwards on its own (standalone)
    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)

    info("*** RSU at x=0 (coverage ~%dm), standalone\n" % int(M.COVERAGE_M))
    ap1 = net.addAccessPoint("ap1", ssid="rsu-ssid", mode="g", channel="1",
                             position="0,0,0", range=350,
                             failMode="standalone", cls=OVSKernelAP)

    info("*** Vehicle car1 at x=%d\n" % int(M.START_X))
    car1 = net.addStation("car1", ip="10.0.0.1/8",
                          position="%d,0,0" % int(M.START_X), range=350)

    info("*** Content server\n")
    server1 = net.addHost("server1", ip="%s/8" % SRV_IP)

    info("*** Propagation logDistance exp=%.2f\n" % M.PATHLOSS_N)
    net.setPropagationModel(model="logDistance", exp=M.PATHLOSS_N)

    try:
        net.configureWifiNodes()
    except AttributeError:
        net.configureNodes()

    net.addLink(ap1, server1)

    info("*** Build & start\n")
    net.build()
    ap1.start([])
    time.sleep(2)

    if not args.headless:
        net.plotGraph(min_x=-350, max_x=350, min_y=-150, max_y=150)

    ensure_assoc(car1, ap1)
    car1.cmd("ping -c2 -W2 %s >/dev/null 2>&1" % SRV_IP)

    # REAL packet-loss probe: continuous ICMP on the wireless path.
    # -O prints a 'no answer' line for every missed reply -> we count per window.
    os.system("rm -f /tmp/ping.log")
    car1.cmd("pkill -f 'ping -i 0.05' 2>/dev/null")
    car1.cmd("ping -i 0.05 -O %s > /tmp/ping.log 2>&1 &" % SRV_IP)

    srv_if = "server1-eth0"
    setup_tc(server1, srv_if)
    # CONSERVATIVE / LOWEST-FIRST STARTUP POLICY:
    # pin the link to a low bootstrap bandwidth BEFORE VLC boots, so its
    # rate-based ABR starts at the lowest rung (360p) instead of defaulting
    # to the highest. The drive loop then applies the real distance-based
    # bandwidth, and VLC ramps up/down from there.
    boot_bw = M.LADDER["360p"] * 1.2          # ~1.2 Mbps -> picks 360p only
    set_tc(server1, srv_if, boot_bw)
    info("*** Bootstrap bandwidth pinned to %.2f Mbps (start-low policy)\n" % boot_bw)

    info("*** HTTP server on %s:%d (log -> %s)\n" % (SRV_IP, SRV_PORT, HTTP_LOG))
    server1.cmd("cd %s && python3 -u -m http.server %d --bind %s >%s 2>&1 &"
                % (CONTENT_DIR, SRV_PORT, SRV_IP, HTTP_LOG))
    time.sleep(1)

    if args.headless:
        info("*** Launching cvlc (headless) as user '%s'\n" % USER)
        car1.cmd("sudo -u %s env HOME=%s "
                 "cvlc --intf dummy --no-audio "
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

    run_loop(car1, server1, srv_if, args)

    info("*** Cleaning up\n")
    car1.cmd("pkill -u %s vlc 2>/dev/null" % USER)
    car1.cmd("pkill -f 'ping -i 0.05' 2>/dev/null")
    server1.cmd("pkill -f http.server 2>/dev/null")
    if args.cli:
        CLI(net)
    net.stop()


def run_loop(car1, server1, srv_if, args):
    poller = QualityPoller(HTTP_LOG)
    loss_probe = PingLossPoller()
    rebuf = RebufferEstimator()
    f = open(args.out, "w")
    f.write("t,x,dist,rssi,rssi_src,bw_mbps,quality,quality_idx,seg,loss,stall,buffer_s\n")

    t = 0.0
    x = M.START_X
    total = (M.END_X - M.START_X) / M.SPEED_MPS
    info("*** Driving %d->%d m @ %.1f m/s (%.0fs)\n"
         % (M.START_X, M.END_X, M.SPEED_MPS, total))

    while x <= M.END_X + 1e-9:
        car1.setPosition("%.1f,0,0" % x)
        time.sleep(args.settle)
        d = abs(x - M.RSU_POS_X)

        rssi = parse_rssi(car1.cmd("iw dev %s-wlan0 link" % car1.name))
        src = "live"
        if rssi is None:
            rssi = M.rssi_from_distance(d); src = "model"

        bw = M.throughput_from_rssi(rssi)
        set_tc(server1, srv_if, bw)

        qidx, seg, n_new = poller.poll()
        qlabel = RUNG_LABEL.get(qidx, "buffering")
        loss = loss_probe.poll()                       # pure wireless ICMP loss
        stall, buf = rebuf.update(n_new, M.SAMPLE_DT)   # rebuffering buffer model

        f.write("%.1f,%.1f,%.1f,%.2f,%s,%.3f,%s,%d,%d,%.3f,%d,%.1f\n"
                % (t, x, d, rssi, src, bw, qlabel, qidx, seg, loss, stall, buf))
        f.flush()

        if int(t) % 20 == 0:
            info("  t=%4.0fs x=%+6.1f rssi=%6.1f(%s) bw=%4.1f -> %s%s\n"
                 % (t, x, rssi, src, bw, qlabel, "  [STALL]" if stall else ""))

        t += M.SAMPLE_DT
        x += M.SPEED_MPS * M.SAMPLE_DT
        extra = M.SAMPLE_DT - args.settle
        if extra > 0:
            time.sleep(extra)

    f.close()
    info("*** Total rebuffering: %.1f s\n" % rebuf.total_stall)
    info("*** CSV -> %s\n" % args.out)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="baseline_run.csv")
    p.add_argument("--settle", type=float, default=0.1)
    p.add_argument("--warmup", type=float, default=15)
    p.add_argument("--cli", action="store_true")
    p.add_argument("--headless", action="store_true",
                   help="no popups: use cvlc (dummy interface), skip plotGraph "
                        "(for unattended multi-run batches)")
    args = p.parse_args()
    setLogLevel("info")
    topology(args)
