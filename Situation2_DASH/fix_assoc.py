#!/usr/bin/env python3
"""
fix_assoc.py -- robust station<->AP association for this hwsim setup.

mn_wifi's sta.setAssociation() gets desynced on this machine's mac80211_hwsim
+ wmediumd stack: mn_wifi's own bookkeeping says "associated" while the kernel
(`iw dev <intf> link`) says "Not connected", so packets never actually flow.

FIX: drive the kernel directly with `iw dev <intf> connect <ssid> <freq>` and
confirm success from `iw ... link`, instead of trusting mn_wifi's state.

Used by baseline_4rsu_topo.py for the initial association and for every
handover between RSUs. Run this file directly (`sudo python3 fix_assoc.py`)
for a standalone smoke test of a single station/AP pair.
"""
import re
import time


def channel_to_freq(channel, band="2.4"):
    """2.4 GHz: 2407 + ch*5 (ch 1-13, ch14 -> 2484). 5 GHz: 5000 + ch*5."""
    ch = int(channel)
    if band == "5":
        return 5000 + ch * 5
    if ch == 14:
        return 2484
    return 2407 + ch * 5


def _unwrap(v, default):
    """mn_wifi sometimes stores ap.params values as a list (one per radio)."""
    if v is None:
        return default
    if isinstance(v, (list, tuple)):
        return v[0] if v else default
    return v


def _linked_bssid(sta, intf):
    out = sta.cmd("iw dev %s link" % intf)
    if "Not connected" in out or not out.strip():
        return None
    m = re.search(r"Connected to ([0-9a-fA-F:]{17})", out)
    return m.group(1).lower() if m else None


def ensure_assoc(sta, ap, intf=None, retries=5, wait=1.0, band="2.4"):
    """Associate `sta` to `ap` via a direct `iw connect`, bypassing mn_wifi's
    setAssociation(). Returns True once the kernel confirms the link."""
    intf = intf or "%s-wlan0" % sta.name
    ssid = _unwrap(ap.params.get("ssid"), "default")
    channel = _unwrap(ap.params.get("channel"), "1")
    freq = channel_to_freq(channel, band=band)

    for attempt in range(1, retries + 1):
        sta.cmd("iw dev %s disconnect 2>/dev/null" % intf)
        sta.cmd("iw dev %s connect %s %d" % (intf, ssid, freq))
        time.sleep(wait)
        bssid = _linked_bssid(sta, intf)
        if bssid is not None:
            return True
    return False


if __name__ == "__main__":
    from mininet.log import setLogLevel, info
    from mn_wifi.net import Mininet_wifi
    from mn_wifi.node import OVSKernelAP
    from mn_wifi.link import wmediumd
    from mn_wifi.wmediumdConnector import interference

    setLogLevel("info")
    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)
    ap = net.addAccessPoint("ap1", ssid="fixtest-ssid", mode="g", channel="1",
                             position="0,0,0", range=300,
                             failMode="standalone", cls=OVSKernelAP)
    sta = net.addStation("sta1", ip="10.0.0.9/8", position="50,0,0", range=300)
    net.setPropagationModel(model="logDistance", exp=1.9)
    try:
        net.configureWifiNodes()
    except AttributeError:
        net.configureNodes()
    net.build()
    ap.start([])
    time.sleep(2)

    ok = ensure_assoc(sta, ap)
    info("*** ensure_assoc result: %s\n" % ok)
    info(sta.cmd("iw dev sta1-wlan0 link"))
    net.stop()
