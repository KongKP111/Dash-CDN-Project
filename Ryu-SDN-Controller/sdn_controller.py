from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
import os
import time


class MobilityAwareFlowSwitch13(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    # Shared with the host over the bind-mounted /tmp (see run_4rsu_multi.sh /
    # baseline_4rsu_topo.py). The controller is a single long-running process
    # across all N runs of a batch (docker --restart=always), so instead of
    # restarting the container to change RUN_ID per run, the topology script
    # just (re)writes this file at the start of each run and we read it fresh
    # on every handover -- no container restart needed between runs.
    RUN_ID_FILE = '/tmp/current_run_id.txt'
    HO_CSV = '/tmp/handover_times.csv'

    def __init__(self, *args, **kwargs):
        super(MobilityAwareFlowSwitch13, self).__init__(*args, **kwargs)
        self.mac_to_port = {}

        # --- Handover Execution Time instrumentation ---
        self.ho_pending = {}          # barrier xid -> (t_start, dpid, mac)
        self.ho_start = {}            # (dpid, mac) -> t_start (pending handover)
        self._ensure_ho_csv_header()

    def _ensure_ho_csv_header(self):
        # Checked on every write (not just __init__): the host-side batch
        # script may `rm` this file between batches to start a clean dataset
        # while this long-running container keeps going -- so the header
        # must be re-created the next time it's missing, not just once.
        if not os.path.exists(self.HO_CSV) or os.path.getsize(self.HO_CSV) == 0:
            with open(self.HO_CSV, 'w') as f:
                f.write('run_id,wall_ts,dpid,mac,handover_exec_ms\n')

    def _current_run_id(self):
        try:
            with open(self.RUN_ID_FILE) as f:
                return f.read().strip() or 'unknown'
        except (IOError, OSError):
            return os.environ.get('RUN_ID', 'unknown')

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Fresh dict on every (re)connect: dpids get reused across `mn -c`
        # teardown/rebuild cycles within the same long-running controller,
        # so a prior run's port mappings must not leak into the next one.
        self.mac_to_port[datapath.id] = {}

        # table-miss: send unmatched packets to controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(
            ofproto.OFPP_CONTROLLER,
            ofproto.OFPCML_NO_BUFFER
        )]
        self.add_flow(datapath, priority=0, match=match, actions=actions, idle_timeout=0)

        self.logger.info("Switch connected: dpid=%s", datapath.id)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None, idle_timeout=8, hard_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions
        )]

        if buffer_id is not None and buffer_id != ofproto.OFP_NO_BUFFER:
            mod = parser.OFPFlowMod(
                datapath=datapath,
                buffer_id=buffer_id,
                priority=priority,
                match=match,
                idle_timeout=idle_timeout,
                hard_timeout=hard_timeout,
                instructions=inst
            )
        else:
            mod = parser.OFPFlowMod(
                datapath=datapath,
                priority=priority,
                match=match,
                idle_timeout=idle_timeout,
                hard_timeout=hard_timeout,
                instructions=inst
            )

        datapath.send_msg(mod)

    def delete_flows_for_mac(self, datapath, mac):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Delete flows where moved host is destination
        match_dst = parser.OFPMatch(eth_dst=mac)
        mod_dst = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            priority=1,
            match=match_dst
        )
        datapath.send_msg(mod_dst)

        # Delete flows where moved host is source
        match_src = parser.OFPMatch(eth_src=mac)
        mod_src = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            priority=1,
            match=match_src
        )
        datapath.send_msg(mod_src)

        self.logger.info("Deleted stale flows for MAC=%s on dpid=%s", mac, datapath.id)

    def _send_ho_barrier(self, datapath, parser, dpid, mac):
        # Flow-rule update time: from sending the new flow-mod until the
        # barrier reply confirms it is installed on the switch.
        t_send = time.time()
        req = parser.OFPBarrierRequest(datapath)
        datapath.send_msg(req)
        self.ho_pending[req.xid] = (t_send, dpid, mac)

    @set_ev_cls(ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER)
    def _barrier_reply_handler(self, ev):
        xid = ev.msg.xid
        info = self.ho_pending.pop(xid, None)
        if info is None:
            return
        t_start, dpid, mac = info
        exec_ms = (time.time() - t_start) * 1000.0
        run_id = self._current_run_id()
        self.logger.info("Handover exec time: run=%s dpid=%s mac=%s %.3f ms",
                         run_id, dpid, mac, exec_ms)
        self._ensure_ho_csv_header()
        with open(self.HO_CSV, 'a') as f:
            f.write('%s,%.6f,%s,%s,%.3f\n' % (
                run_id, time.time(), dpid, mac, exec_ms))

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == DEAD_DISPATCHER and datapath is not None:
            self.mac_to_port.pop(datapath.id, None)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id
        in_port = msg.match['in_port']

        self.mac_to_port.setdefault(dpid, {})

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth is None:
            return

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst = eth.dst
        src = eth.src

        old_port = self.mac_to_port[dpid].get(src)

        # Detect mobility / handover by MAC moving to a new port
        handover_now = (old_port is not None and old_port != in_port)
        if handover_now:
            self.ho_start[(dpid, src)] = time.time()   # remember handover start
            self.logger.info(
                "Mobility detected: dpid=%s mac=%s old_port=%s new_port=%s",
                dpid, src, old_port, in_port
            )
            self.delete_flows_for_mac(datapath, src)

        # Re-learn current source location
        self.mac_to_port[dpid][src] = in_port

        # Decide forwarding port
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # Install flow only when destination is known
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(
                in_port=in_port,
                eth_src=src,
                eth_dst=dst
            )

            self.logger.info(
                "Installing flow: dpid=%s src=%s dst=%s in_port=%s out_port=%s",
                dpid, src, dst, in_port, out_port
            )

            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(
                    datapath=datapath,
                    priority=1,
                    match=match,
                    actions=actions,
                    buffer_id=msg.buffer_id,
                    idle_timeout=8,
                    hard_timeout=0
                )
                if (dpid, src) in self.ho_start:
                    self.ho_start.pop((dpid, src))
                    self._send_ho_barrier(datapath, parser, dpid, src)
                return
            else:
                self.add_flow(
                    datapath=datapath,
                    priority=1,
                    match=match,
                    actions=actions,
                    idle_timeout=8,
                    hard_timeout=0
                )
                if (dpid, src) in self.ho_start:
                    self.ho_start.pop((dpid, src))
                    self._send_ho_barrier(datapath, parser, dpid, src)

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data
        )
        datapath.send_msg(out)
