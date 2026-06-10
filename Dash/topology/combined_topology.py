#!/usr/bin/env python3
"""
============================================================================
  Combined SDN-CDN / SDN-DASH Topology  (Mininet-WiFi)
----------------------------------------------------------------------------
  Project : Comparative Analysis of SDN-CDN and SDN-DASH for Video
            Streaming in Mobile Networks
  Team    : Hadis Rodpradit (DASH)  |  Kongpop Tipmontree (CDN)
  Advisor : Asst. Prof. Dr. Kuljaree Tantayakul
----------------------------------------------------------------------------
  One topology, every test:
    --arch   dash | cdn          which architecture to run
    --sit    1..6                situation (controls bandwidth/throttle)
    --speed  20 | 25 | 30        car speed in km/h
    --round  1..10               round number (for log naming)
    --cli                        drop into Mininet CLI after build
============================================================================
"""
 
import sys
import os
import argparse
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.log import setLogLevel, info
from mininet.link import TCLink
from mn_wifi.net import Mininet_wifi
from mn_wifi.node import OVSKernelAP
from mn_wifi.cli import CLI
from mn_wifi.link import wmediumd
from mn_wifi.wmediumdConnector import interference
 
 
# ---------------------------------------------------------------------------
#  Layout constants  (sim units = meters)
# ---------------------------------------------------------------------------
RSU_Y          = 200          # all RSUs sit on the same horizontal line
RSU_X          = [100, 250, 400, 550]   # 150 m spacing
RSU_RANGE      = 140          # >75 -> adjacent cells OVERLAP (handover region)
CAR_Y          = 250          # car drives just below the RSU line
CAR_START_X    = 80
CAR_END_X      = 580
CAR_RANGE      = 60
 
CTRL_IP        = '127.0.0.1'  # Ryu runs in Docker on host network
CTRL_PORT      = 6653
 
# server / node addressing  (single /24)
SERVER_IP      = '10.0.0.10'   # DASH/CDN origin video server
RSU_IPS        = ['10.0.0.101', '10.0.0.102', '10.0.0.103', '10.0.0.104']
EDGE_IPS       = ['10.0.0.111', '10.0.0.112', '10.0.0.113', '10.0.0.114']
CAR_IP         = '10.0.0.200'
 
 
# ---------------------------------------------------------------------------
#  Situation profiles  (link bandwidth in Mbps + throttle behaviour)
#  Throttling itself is applied by the run script via tc; here we expose
#  the base bandwidth so the topology reflects the right starting state.
# ---------------------------------------------------------------------------
SITUATION = {
    1: {'name': 'Normal / Baseline',        'base_bw': 3.0},
    2: {'name': 'Light Handover (Urban)',   'base_bw': 2.0},
    3: {'name': 'Heavy Handover (Suburban)','base_bw': 3.0},
    4: {'name': 'Sudden Drop (Dead Zone)',  'base_bw': 3.0},
    5: {'name': 'High Mobility (Highway)',  'base_bw': 3.0},
    6: {'name': 'Combined Stress',          'base_bw': 3.0},
}
 
 
def kmh_to_traveltime(speed_kmh, distance_m):
    """Convert a target speed into the travel time the car needs to cross
    the whole RSU line, so mobility produces an exact constant speed."""
    speed_ms = speed_kmh * 1000.0 / 3600.0
    return distance_m / speed_ms
 
 
def build(arch='dash', sit=1, speed=20, rnd=1, use_cli=False):
    setLogLevel('info')
 
    profile  = SITUATION[sit]
    base_bw  = profile['base_bw']
    distance = CAR_END_X - CAR_START_X
    travel_t = kmh_to_traveltime(speed, distance)
 
    info('*** ============================================\n')
    info(f"*** Architecture : {arch.upper()}\n")
    info(f"*** Situation    : {sit} - {profile['name']}\n")
    info(f"*** Car speed    : {speed} km/h\n")
    info(f"*** Round        : {rnd}\n")
    info(f"*** Base BW      : {base_bw} Mbps\n")
    info(f"*** Travel time  : {travel_t:.1f} s over {distance} m\n")
    info('*** ============================================\n')
 
    net = Mininet_wifi(
        controller=RemoteController,
        accessPoint=OVSKernelAP,
        switch=OVSKernelSwitch,
        link=wmediumd,
        wmediumd_mode=interference,
    )
 
    # ---- Controller (Control Plane) -------------------------------------
    info('*** Adding controller (Ryu via Docker)\n')
    c0 = net.addController('c0', controller=RemoteController,
                           ip=CTRL_IP, port=CTRL_PORT)
 
    # ---- Central switch (Data Plane) ------------------------------------
    info('*** Adding core switch SW1\n')
    sw1 = net.addSwitch('sw1', cls=OVSKernelSwitch, protocols='OpenFlow13')
 
    # ---- DASH/CDN Origin video server -----------------------------------
    info('*** Adding DASH/CDN video server\n')
    server = net.addHost('server', ip=SERVER_IP + '/24',
                         mac='00:00:00:00:00:10')
 
    # ---- RSU access points (overlapping coverage) -----------------------
    info('*** Adding RSU access points with overlapping coverage\n')
    channels = ['1', '6', '11', '1']
    rsus = []
    for i in range(4):
        rsu = net.addAccessPoint(
            f'rsu{i+1}',
            ssid=f'RSU{i+1}',
            mode='g',
            channel=channels[i],
            position=f'{RSU_X[i]},{RSU_Y},0',
            range=RSU_RANGE,
            ip=RSU_IPS[i] + '/24',
        )
        rsus.append(rsu)
 
    # ---- CDN Edge nodes co-located at each RSU (MEC concept) ------------
    # Only meaningfully used by the CDN architecture, but always present so
    # the topology stays identical across both runs (fair comparison).
    edges = []
    info('*** Adding CDN Edge nodes (co-located with each RSU)\n')
    for i in range(4):
        edge = net.addHost(f'edge{i+1}', ip=EDGE_IPS[i] + '/24',
                           mac=f'00:00:00:00:01:1{i+1}')
        edges.append(edge)
 
    # ---- Car1 (mobile client) -------------------------------------------
    info('*** Adding mobile client Car1\n')
    car1 = net.addStation('car1', ip=CAR_IP + '/24',
                          mac='00:00:00:00:02:00',
                          position=f'{CAR_START_X},{CAR_Y},0',
                          range=CAR_RANGE)
 
    # ---- Propagation model ----------------------------------------------
    net.setPropagationModel(model='logDistance', exp=4)
 
    info('*** Configuring wifi nodes\n')
    net.configureWifiNodes()
 
    # ---- Wired links (Data Plane backbone) ------------------------------
    info('*** Wiring backbone links\n')
    # server <-> SW1
    net.addLink(server, sw1, bw=100)
    # SW1 <-> each RSU  (this is the throttle point for situations)
    for rsu in rsus:
        net.addLink(sw1, rsu, bw=base_bw)
    # Each CDN Edge attaches to its co-located RSU
    for i in range(4):
        net.addLink(edges[i], rsus[i], bw=100)
 
    # ---- Mobility: straight drive RSU1 -> RSU4 at constant speed --------
    info('*** Configuring mobility\n')
    net.startMobility(time=0)
    net.mobility(car1, 'start', time=1,
                 position=f'{CAR_START_X},{CAR_Y},0')
    net.mobility(car1, 'stop', time=int(travel_t),
                 position=f'{CAR_END_X},{CAR_Y},0')
    net.stopMobility(time=int(travel_t) + 2)
 
    # optional live plot (comment out for headless lab runs)
    # net.plotGraph(max_x=700, max_y=400)
 
    # ---- Start everything ------------------------------------------------
    info('*** Building and starting network\n')
    net.build()
    c0.start()
    sw1.start([c0])
    for rsu in rsus:
        rsu.start([c0])
 
    # ---- Architecture-specific server bring-up --------------------------
    info(f'*** Bringing up {arch.upper()} services\n')
    if arch == 'dash':
        # Hadis fills the real DASH server logic in servers/dash_server.py.
        # For now we serve the DASH content folder over HTTP on the origin.
          server.cmd(
            'python3 /home/diz/sdn-cdn-dash-research/servers/dash_server.py '
            '--dir /home/diz/sdn-vanet-project/bbb_multi '
            '--port 8080 '
            '--log /tmp/dash_server.log '
            '> /tmp/dash_server_stdout.log 2>&1 &'
          )
    elif arch == 'cdn':
        # Kongpop fills cdn_origin.py / cdn_edge.py.
        server.cmd('python3 /root/servers/cdn_origin.py '
                   '> /tmp/cdn_origin.log 2>&1 &')
        for i in range(4):
            edges[i].cmd(f'python3 /root/servers/cdn_edge.py --id {i+1} '
                         f'> /tmp/cdn_edge{i+1}.log 2>&1 &')
 
    info('*** Topology is up.\n')
 
    if use_cli:
        CLI(net)
 
    info('*** Stopping network\n')
    net.stop()
 
 
def parse_args():
    p = argparse.ArgumentParser(description='Combined SDN-CDN/DASH topology')
    p.add_argument('--arch',  choices=['dash', 'cdn'], default='dash')
    p.add_argument('--sit',   type=int, choices=range(1, 7), default=1)
    p.add_argument('--speed', type=int, choices=[20, 25, 30], default=20)
    p.add_argument('--round', type=int, default=1, dest='rnd')
    p.add_argument('--cli',   action='store_true',
                   help='drop into Mininet CLI after build')
    return p.parse_args()
 
 
if __name__ == '__main__':
    a = parse_args()
    build(arch=a.arch, sit=a.sit, speed=a.speed, rnd=a.rnd, use_cli=a.cli)
