from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from os_ken.controller.handler import set_ev_cls
from os_ken.topology import event
from os_ken.topology.api import get_all_link, get_all_host, get_all_switch
from os_ken.topology.switches import Switch, Host, HostState, Port, PortState, PortData, PortDataState, Link, LinkState
from os_ken.topology.switches import Switches
from os_ken.ofproto import ofproto_v1_0, ether, inet
from os_ken.lib import addrconv
from os_ken.lib.packet import packet, ethernet, ether_types, arp
from os_ken.lib.packet import dhcp
from os_ken.lib.packet import ipv4
from os_ken.lib.packet import udp
from dhcp import DHCPServer
from collections import defaultdict
from collections import deque
from itertools import combinations
import time
from ofctl_utilis import OfCtl,OfCtl_v1_0,OfCtl_after_v1_2,VLANID_NONE
import logging
import copy
import heapq
from firewall import Firewall


class ControllerApp(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_0.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ControllerApp, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.ofctls = {}
        self.hosts = {}
        self.adjacency = defaultdict(dict)
        self.switch_host_ports = defaultdict(dict)
        self.firewall = Firewall(rule_file='firewall_rule.json')

    def _mac_to_bin(self, mac_addr):
        return addrconv.mac.text_to_bin(mac_addr)

    def _refresh_topology(self):
        previous_hosts = dict(self.hosts)
        current_dpids = set()
        for switch in get_all_switch(self):
            dpid = switch.dp.id
            current_dpids.add(dpid)
            self.datapaths[dpid] = switch.dp
            self.ofctls[dpid] = OfCtl.factory(switch.dp, self.logger)

        for dpid in list(self.datapaths):
            if dpid not in current_dpids:
                self.datapaths.pop(dpid, None)
                self.ofctls.pop(dpid, None)

        self.adjacency = defaultdict(dict)
        for link in get_all_link(self):
            self.adjacency[link.src.dpid][link.dst.dpid] = link.src.port_no

        self.switch_host_ports = defaultdict(dict)
        refreshed_hosts = {}
        for host in get_all_host(self):
            refreshed_hosts[host.mac] = {
                'mac': host.mac,
                'ip': host.ipv4[0] if host.ipv4 else None,
                'switch': host.port.dpid,
                'port': host.port.port_no,
            }
            self.switch_host_ports[host.port.dpid][host.mac] = host.port.port_no

        for mac, host_info in previous_hosts.items():
            if mac in refreshed_hosts and refreshed_hosts[mac]['ip'] is None:
                refreshed_hosts[mac]['ip'] = host_info.get('ip')
            elif mac not in refreshed_hosts and host_info.get('switch') in current_dpids:
                refreshed_hosts[mac] = dict(host_info)
                self.switch_host_ports[host_info['switch']][mac] = host_info['port']
        self.hosts = refreshed_hosts

    def _clear_switch_flows(self, datapath):
        ofp = datapath.ofproto
        parser = datapath.ofproto_parser
        match = parser.OFPMatch(ofp.OFPFW_ALL, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        flow_mod = parser.OFPFlowMod(
            datapath,
            match,
            0,
            ofp.OFPFC_DELETE,
            out_port=ofp.OFPP_NONE,
            priority=0,
            actions=[],
        )
        datapath.send_msg(flow_mod)

    def _install_table_miss(self, datapath):
        ofp = datapath.ofproto
        parser = datapath.ofproto_parser
        match = parser.OFPMatch(ofp.OFPFW_ALL, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, 65535)]
        datapath.send_msg(
            parser.OFPFlowMod(
                datapath,
                match,
                0,
                ofp.OFPFC_ADD,
                priority=0,
                actions=actions,
            )
        )

    def _install_host_flows(self):
        for dpid, datapath in self.datapaths.items():
            self._clear_switch_flows(datapath)
            self._install_table_miss(datapath)

        for mac, host_info in self.hosts.items():
            self._install_destination_host_flows(mac, host_info)

        if self.ofctls:
            self.firewall.installed.clear()
            self.firewall.install_rules(self.ofctls)

    def _install_destination_host_flows(self, host_mac, host_info):
        dst_switch = host_info['switch']
        for src_switch in sorted(self.datapaths):
            distances, parents = self._dijkstra(src_switch)
            path = self._reconstruct_path(parents, src_switch, dst_switch)
            if not path:
                continue
            for switch_index, dpid in enumerate(path):
                out_port = self._get_out_port_for_destination(path, switch_index, host_mac)
                if out_port is None:
                    continue
                self.ofctls[dpid].set_flow(
                    cookie=0x3050,
                    priority=1000,
                    dl_dst=self._mac_to_bin(host_mac),
                    actions=[self.datapaths[dpid].ofproto_parser.OFPActionOutput(out_port, 0)],
                )

    def _get_out_port_for_destination(self, path, switch_index, host_mac):
        dpid = path[switch_index]
        if switch_index == len(path) - 1:
            return self.switch_host_ports.get(dpid, {}).get(host_mac)
        next_dpid = path[switch_index + 1]
        return self.adjacency.get(dpid, {}).get(next_dpid)

    def _dijkstra(self, src):
        distances = {src: 0}
        parents = {src: None}
        heap = [(0, src)]
        while heap:
            distance, node = heapq.heappop(heap)
            if distance != distances.get(node):
                continue
            for neighbor in sorted(self.adjacency.get(node, {})):
                candidate = distance + 1
                if candidate < distances.get(neighbor, float('inf')):
                    distances[neighbor] = candidate
                    parents[neighbor] = node
                    heapq.heappush(heap, (candidate, neighbor))
        return distances, parents

    def _reconstruct_path(self, parents, src, dst):
        if dst not in parents:
            return []
        path = []
        current = dst
        while current is not None:
            path.append(current)
            current = parents.get(current)
        path.reverse()
        if path and path[0] == src:
            return path
        return []

    def _print_topology_and_paths(self):
        if not self.datapaths:
            self.logger.info('Current topology: no switches connected')
            return
        topology_edges = []
        seen = set()
        for src, neighbors in sorted(self.adjacency.items()):
            for dst in sorted(neighbors):
                edge = tuple(sorted((src, dst)))
                if edge in seen:
                    continue
                seen.add(edge)
                topology_edges.append('switch_%s <-> switch_%s' % edge)
        host_edges = [
            'host_%s <-> switch_%s' % (mac, info['switch'])
            for mac, info in sorted(self.hosts.items())
        ]
        self.logger.info('Current topology edges: %s', ', '.join(topology_edges + host_edges) or 'none')

        for left_switch, right_switch in combinations(sorted(self.datapaths), 2):
            distances, parents = self._dijkstra(left_switch)
            switch_path = self._reconstruct_path(parents, left_switch, right_switch)
            if not switch_path:
                self.logger.info('No path between switch_%s and switch_%s', left_switch, right_switch)
                continue
            distance = len(switch_path) - 1
            self.logger.info('The distance from switch_%s to switch_%s : %s', left_switch, right_switch, distance)
            self.logger.info('Path: %s', ' -> '.join('switch_%s' % dpid for dpid in switch_path))

        for left_mac, right_mac in combinations(sorted(self.hosts), 2):
            left = self.hosts[left_mac]
            right = self.hosts[right_mac]
            distances, parents = self._dijkstra(left['switch'])
            switch_path = self._reconstruct_path(parents, left['switch'], right['switch'])
            if not switch_path:
                self.logger.info('No path between host_%s and host_%s', left_mac, right_mac)
                continue
            full_path = ['host_%s' % left_mac] + ['switch_%s' % dpid for dpid in switch_path] + ['host_%s' % right_mac]
            distance = len(full_path) - 1
            self.logger.info('The distance from host_%s to host_%s : %s', left_mac, right_mac, distance)
            self.logger.info('Path: %s', ' -> '.join(full_path))
            reverse_path = ['host_%s' % right_mac] + ['switch_%s' % dpid for dpid in reversed(switch_path)] + ['host_%s' % left_mac]
            self.logger.info('The distance from host_%s to host_%s : %s', right_mac, left_mac, distance)
            self.logger.info('Path: %s', ' -> '.join(reverse_path))

    def _rebuild_network_state(self):
        self._refresh_topology()
        self._install_host_flows()
        self._print_topology_and_paths()

    def _update_host(self, mac_addr, ip_addr, dpid, port_no):
        existing = self.hosts.get(mac_addr, {})
        host_info = dict(existing)
        host_info.update({
            'mac': mac_addr,
            'ip': ip_addr or existing.get('ip'),
            'switch': dpid,
            'port': port_no,
        })
        changed = host_info != existing
        self.hosts[mac_addr] = host_info
        self.switch_host_ports[dpid][mac_addr] = port_no
        return changed

    def _handle_arp(self, datapath, in_port, pkt):
        pkt_eth = pkt.get_protocol(ethernet.ethernet)
        pkt_arp = pkt.get_protocol(arp.arp)
        if pkt_arp is None:
            return

        if pkt_arp.src_ip and pkt_arp.src_ip != '0.0.0.0':
            changed = self._update_host(pkt_arp.src_mac, pkt_arp.src_ip, datapath.id, in_port)
            if changed:
                self._install_host_flows()

        if pkt_arp.opcode != arp.ARP_REQUEST:
            return

        target_host = None
        for host in self.hosts.values():
            if host.get('ip') == pkt_arp.dst_ip:
                target_host = host
                break
        if target_host is None:
            return

        ofctl = self.ofctls.get(datapath.id)
        if ofctl is None:
            ofctl = OfCtl.factory(datapath, self.logger)
            self.ofctls[datapath.id] = ofctl

        ofctl.send_arp(
            arp_opcode=arp.ARP_REPLY,
            vlan_id=VLANID_NONE,
            dst_mac=pkt_arp.src_mac,
            sender_mac=target_host['mac'],
            sender_ip=target_host['ip'],
            target_ip=pkt_arp.src_ip,
            target_mac=pkt_arp.src_mac,
            src_port=datapath.ofproto.OFPP_CONTROLLER,
            output_port=in_port,
        )

    def _handle_ipv4(self, datapath, in_port, pkt):
        pkt_eth = pkt.get_protocol(ethernet.ethernet)
        pkt_ipv4 = pkt.get_protocol(ipv4.ipv4)
        if pkt_eth is None or pkt_ipv4 is None:
            return
        changed = self._update_host(pkt_eth.src, pkt_ipv4.src, datapath.id, in_port)
        if changed:
            self._install_host_flows()

    @set_ev_cls(event.EventSwitchEnter)
    def handle_switch_add(self, ev):
        """
        Event handler indicating a switch has come online.
        """
        self._rebuild_network_state()

    @set_ev_cls(event.EventSwitchLeave)
    def handle_switch_delete(self, ev):
        """
        Event handler indicating a switch has been removed
        """
        self._rebuild_network_state()


    @set_ev_cls(event.EventHostAdd)
    def handle_host_add(self, ev):
        """
        Event handler indiciating a host has joined the network
        This handler is automatically triggered when a host sends an ARP response.
        """ 
        host = ev.host
        self._update_host(host.mac, host.ipv4[0] if host.ipv4 else None, host.port.dpid, host.port.port_no)
        self._rebuild_network_state()

    @set_ev_cls(event.EventLinkAdd)
    def handle_link_add(self, ev):
        """
        Event handler indicating a link between two switches has been added
        """
        self._rebuild_network_state()

    @set_ev_cls(event.EventLinkDelete)
    def handle_link_delete(self, ev):
        """
        Event handler indicating when a link between two switches has been deleted
        """
        self._rebuild_network_state()
   
        

    @set_ev_cls(event.EventPortModify)
    def handle_port_modify(self, ev):
        """
        Event handler for when any switch port changes state.
        This includes links for hosts as well as links between switches.
        """
        self._rebuild_network_state()



    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        try:
            msg = ev.msg
            datapath = msg.datapath
            self.datapaths[datapath.id] = datapath
            self.ofctls.setdefault(datapath.id, OfCtl.factory(datapath, self.logger))
            pkt = packet.Packet(data=msg.data)
            pkt_dhcp = pkt.get_protocols(dhcp.dhcp)
            inPort = msg.in_port
            if not pkt_dhcp:
                pkt_arp = pkt.get_protocol(arp.arp)
                if pkt_arp is not None:
                    self._handle_arp(datapath, inPort, pkt)
                else:
                    self._handle_ipv4(datapath, inPort, pkt)
            else:
                DHCPServer.handle_dhcp(datapath, inPort, pkt)      
                pkt_eth = pkt.get_protocol(ethernet.ethernet)
                lease_ip = DHCPServer.ip_by_mac.get(pkt_eth.src)
                changed = self._update_host(pkt_eth.src, lease_ip, datapath.id, inPort)
                if changed:
                    self._install_host_flows()
            return 
        except Exception as e:
            self.logger.error(e)
    