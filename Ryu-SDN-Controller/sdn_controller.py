from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types


class MobilityAwareFlowSwitch13(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(MobilityAwareFlowSwitch13, self).__init__(*args, **kwargs)
        self.mac_to_port = {}

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        self.mac_to_port.setdefault(datapath.id, {})

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
        if old_port is not None and old_port != in_port:
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
