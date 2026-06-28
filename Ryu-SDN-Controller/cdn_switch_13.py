# ============================================================
# cdn_switch_13.py  —  RYU Controller for Cooperative Edge CDN
# Based on simple_switch_13.py with additions:
#   1. idle_timeout on learned flows  → stale handover entries clear fast
#   2. ARP packet learning (ip_to_mac table)
#   3. IPv4 src learning
#   4. Per-switch logging with DPID label
#   5. Flood filter for non-ARP broadcast (reduces unnecessary floods)
# ============================================================

import os
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, arp, ipv4


class CDNSwitch13(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    # ── Priority levels ─────────────────────────────────────
    PRIO_TABLE_MISS  = 0    # table-miss → send to controller
    PRIO_LEARNED     = 10   # unicast learned flow

    # ── Timeout (seconds) ───────────────────────────────────
    # ให้ flow หมดอายุเร็ว → หลัง handover ไม่มี stale entry ค้าง
    FLOW_IDLE_TIMEOUT = 15

    def __init__(self, *args, **kwargs):
        super(CDNSwitch13, self).__init__(*args, **kwargs)
        # {dpid: {mac: port}}  — per-switch MAC learning table
        self.mac_to_port = {}
        # {ip: mac}  — global ARP/IP learning table (ช่วย debug)
        self.ip_to_mac   = {}

        self.run_id     = os.environ.get('RUN_ID', 'unknown')
        self.ho_pending = {}   # barrier xid -> (t_send, dpid, mac)
        self.ho_start   = {}   # (dpid, mac) -> pending handover flag
        ho_csv_path = os.environ.get(
            'HANDOVER_CSV_PATH',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'handover_times.csv'))
        os.makedirs(os.path.dirname(ho_csv_path), exist_ok=True)
        self._ho_csv = open(ho_csv_path, 'w')
        self._ho_csv.write('run_id,wall_ts,dpid,mac,handover_exec_ms\n')
        self._ho_csv.flush()

    # ────────────────────────────────────────────────────────
    # Switch connects → install table-miss flow
    # ────────────────────────────────────────────────────────
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser

        # table-miss: forward ALL unmatched packets to controller
        match   = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, self.PRIO_TABLE_MISS, match, actions)
        self.logger.info("[DPID %s] connected — table-miss flow installed", datapath.id)

    # ────────────────────────────────────────────────────────
    # Helper: add flow mod
    # ────────────────────────────────────────────────────────
    def add_flow(self, datapath, priority, match, actions,
                 buffer_id=None, idle_timeout=0, hard_timeout=0):
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        kwargs = dict(
            datapath     = datapath,
            priority     = priority,
            match        = match,
            instructions = inst,
            idle_timeout = idle_timeout,
            hard_timeout = hard_timeout,
        )
        if buffer_id:
            kwargs['buffer_id'] = buffer_id

        datapath.send_msg(parser.OFPFlowMod(**kwargs))

    # ────────────────────────────────────────────────────────
    # Helper: delete all flows for a MAC (called on handover)
    # ────────────────────────────────────────────────────────
    def delete_flows_for_mac(self, datapath, mac):
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser

        for match in (parser.OFPMatch(eth_dst=mac), parser.OFPMatch(eth_src=mac)):
            mod = parser.OFPFlowMod(
                datapath   = datapath,
                command    = ofproto.OFPFC_DELETE,
                out_port   = ofproto.OFPP_ANY,
                out_group  = ofproto.OFPG_ANY,
                priority   = self.PRIO_LEARNED,
                match      = match,
            )
            datapath.send_msg(mod)

        self.logger.info("[DPID %s] deleted stale flows for MAC %s", datapath.id, mac)

    # ────────────────────────────────────────────────────────
    # Packet-In handler
    # ────────────────────────────────────────────────────────
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg      = ev.msg
        datapath = msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser
        in_port  = msg.match['in_port']
        dpid     = datapath.id

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        # ── Skip LLDP ────────────────────────────────────────
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst = eth.dst
        src = eth.src

        # ── MAC learning ─────────────────────────────────────
        self.mac_to_port.setdefault(dpid, {})
        old_port = self.mac_to_port[dpid].get(src)
        if old_port != in_port:
            self.mac_to_port[dpid][src] = in_port
            self.logger.info("[DPID %s] MAC learned: %s on port %s", dpid, src, in_port)

        # ── Handover detection (MAC moved to new port) ────────
        if old_port is not None and old_port != in_port:
            self.logger.info("[DPID %s] handover: %s port %s -> %s", dpid, src, old_port, in_port)
            self.ho_start[(dpid, src)] = True
            self.delete_flows_for_mac(datapath, src)

        # ── ARP learning (ip_to_mac) ─────────────────────────
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt:
            self.ip_to_mac[arp_pkt.src_ip] = arp_pkt.src_mac
            self.logger.info("[DPID %s] ARP: %s -> %s", dpid, arp_pkt.src_ip, arp_pkt.src_mac)

        # ── IPv4 src learning ─────────────────────────────────
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt and ip_pkt.src not in self.ip_to_mac:
            self.ip_to_mac[ip_pkt.src] = src
            self.logger.info("[DPID %s] IP learned: %s -> %s", dpid, ip_pkt.src, src)

        # ── Decide output port ────────────────────────────────
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # ── Install unicast flow (with idle_timeout) ──────────
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)

            self.logger.info(
                "[DPID %s] install flow: port %s -> %s -> port %s (idle=%ss)",
                dpid, in_port, dst, out_port, self.FLOW_IDLE_TIMEOUT
            )

            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, self.PRIO_LEARNED, match, actions,
                              buffer_id=msg.buffer_id,
                              idle_timeout=self.FLOW_IDLE_TIMEOUT)
                if (dpid, src) in self.ho_start:
                    self.ho_start.pop((dpid, src))
                    self._send_ho_barrier(datapath, parser, dpid, src)
                return
            else:
                self.add_flow(datapath, self.PRIO_LEARNED, match, actions,
                              idle_timeout=self.FLOW_IDLE_TIMEOUT)
                if (dpid, src) in self.ho_start:
                    self.ho_start.pop((dpid, src))
                    self._send_ho_barrier(datapath, parser, dpid, src)

        # ── Send packet out ───────────────────────────────────
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath  = datapath,
            buffer_id = msg.buffer_id,
            in_port   = in_port,
            actions   = actions,
            data      = data,
        )
        datapath.send_msg(out)

    # ────────────────────────────────────────────────────────
    # Handover timing helpers
    # ────────────────────────────────────────────────────────
    def _send_ho_barrier(self, datapath, parser, dpid, mac):
        t_send = time.time()
        req = parser.OFPBarrierRequest(datapath)
        datapath.send_msg(req)
        self.ho_pending[req.xid] = (t_send, dpid, mac)

    @set_ev_cls(ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER)
    def _barrier_reply_handler(self, ev):
        info = self.ho_pending.pop(ev.msg.xid, None)
        if info is None:
            return
        t_send, dpid, mac = info
        exec_ms = (time.time() - t_send) * 1000.0
        self._ho_csv.write('%s,%.6f,%s,%s,%.3f\n' % (
            self.run_id, time.time(), dpid, mac, exec_ms))
        self._ho_csv.flush()
        self.logger.info("[DPID %s] handover exec: %s -> %.3f ms", dpid, mac, exec_ms)
