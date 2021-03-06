# Copyright (C) 2011 Nippon Telegraph and Telephone Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.


# Extension to SimpleSwitch13 prepared for Load Balancer
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet, arp
from ryu.lib.packet import ether_types
import requests
from random import randint

class SimpleSwitch13(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(SimpleSwitch13, self).__init__(*args, **kwargs)
        self.mac_to_port = {}

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # install table-miss flow entry
        #
        # We specify NO BUFFER to max_len of the output action due to
        # OVS bug. At this moment, if we specify a lesser number, e.g.,
        # 128, OVS will send Packet-In with invalid buffer_id and
        # truncated packet data. In that case, we cannot output packets
        # correctly.  The bug has been fixed in OVS v2.1.0.
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match, instructions=inst)
        datapath.send_msg(mod)

    def get_srv_data(self):
	try:
            response = requests.get('http://192.168.152.179:8080/stats/servers/')
	    return response.json()
	except ValueError:
	    raise 'Cannot retrieve JSON data, too many requests'

    def get_port_data(self,dpid):
	data = list()
	for i in range(3):
	   response = requests.get('http://localhost:8080/stats/port/{0}/{1}'.format(dpid, i+2))
	   data.append(response.json())
	return data

    def balance_traffic(self,dpid):
	srv_data = self.get_srv_data()
#	port_data = self.get_port_data(dpid)
	tmp_mem = 100.0
	tmp_cpu = 100.0
	tmp_mac = 'ff:ff:ff:ff:ff:ff'
	tmp_ip = '0.0.0.0'
	i = 1
	macs = list()
	ips = list()
	for d in srv_data:
	   cpu = float(srv_data[d]['cpu'])
	   mem = float(srv_data[d]['mem'])
	   mac = srv_data[d]['mac']
	   ip = srv_data[d]['ip']
	   if mem < tmp_mem:
		tmp_mem = mem
		tmp_cpu = cpu
		tmp_mac = mac
		macs.append(mac)
		tmp_ip = ip
		ips.append(ip)
	   elif mem == tmp_mem:
		if cpu < tmp_cpu:
		   tmp_mem = mem
		   tmp_cpu = cpu
		   tmp_mac = mac
		   tmp_ip = ip
		elif cpu == tmp_cpu:
		   i += 1
		   macs.append(mac)
		   ips.append(ip)
	if i==2:
	    v = randint(1,2)
	    tmp_mac = macs[v-1]
	    tmp_ip = ips[v-1]
	elif i==3:
	    v = randint(1,3)
	    tmp_mac = macs[v-1]
	    tmp_ip = ips[v-1]

	return tmp_mac, tmp_ip


    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        # If you hit this you might want to increase
        # the "miss_send_length" of your switch
        if ev.msg.msg_len < ev.msg.total_len:
            self.logger.debug("packet truncated: only %s of %s bytes",
                              ev.msg.msg_len, ev.msg.total_len)
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
	pkt_arp = pkt.get_protocol(arp.arp)

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            # ignore lldp packet
            return

        dpid = datapath.id

	for p in pkt:
	    if p.protocol_name == 'arp' and p.dst_ip == '10.0.0.100':
		print 'RECEIVED REQUEST TO LOAD BALANCER - Processing...'	
		dst, p.dst_ip = self.balance_traffic(dpid)
		pkt_resp_arp = packet.Packet()
		pkt_resp_arp.add_protocol(ethernet.ethernet(ethertype=eth.ethertype, dst=eth.src,src=dst))
	#	pkt.add_protocol(arp.arp(opcode=arp.ARP_REPLY, src_mac=dst, src_ip=p.dst_ip, dst_mac=pkt_arp.src_mac, dst_ip=pkt_arp.src_ip))
		pkt.add_protocol(arp.arp(opcode=arp.ARP_REPLY, src_mac=dst, src_ip='10.0.0.100', dst_mac=pkt_arp.src_mac, dst_ip=pkt_arp.src_ip))
	        parser = datapath.ofproto_parser
	        pkt_resp_arp.serialize()
	        self.logger.info("ARP packet-out %s" % (pkt_resp_arp,))
	        data = pkt_resp_arp.data
	        actions = [parser.OFPActionOutput(port=in_port)]
	        out = parser.OFPPacketOut(datapath=datapath,
                                  buffer_id=ofproto.OFP_NO_BUFFER,
                                  in_port=ofproto.OFPP_CONTROLLER,
                                  actions=actions,
                                  data=data)
	        datapath.send_msg(out)		
	    else:
	        dst = eth.dst
	        src = eth.src

	        self.mac_to_port.setdefault(dpid, {})

	        self.logger.info("packet in %s %s %s %s", dpid, src, dst, in_port)

	        # learn a mac address to avoid FLOOD next time.
	        self.mac_to_port[dpid][src] = in_port

	        if dst in self.mac_to_port[dpid]:
	            out_port = self.mac_to_port[dpid][dst]
	        else:
	            out_port = ofproto.OFPP_FLOOD

	       	actions = [parser.OFPActionOutput(out_port)]

        	# install a flow to avoid packet_in next time
        	if out_port != ofproto.OFPP_FLOOD:
	        	match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            	# verify if we have a valid buffer_id, if yes avoid to send both
            	# flow_mod & packet_out
            		if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                		self.add_flow(datapath, 1, match, actions, msg.buffer_id)
                		return
            		else:
                		self.add_flow(datapath, 1, match, actions)
        	data = None
        	if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            		data = msg.data

        	out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        	datapath.send_msg(out)
