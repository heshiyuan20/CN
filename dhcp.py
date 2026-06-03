from os_ken.lib import addrconv
from os_ken.lib.packet import packet
from os_ken.lib.packet import ethernet
from os_ken.lib.packet import ipv4
from os_ken.lib.packet import udp
from os_ken.lib.packet import dhcp
import ipaddress
import struct


def _choose_server_ip(start_ip, end_ip, netmask):
    network = ipaddress.IPv4Network('%s/%s' % (start_ip, netmask), strict=False)
    pool_start = int(ipaddress.IPv4Address(start_ip))
    pool_end = int(ipaddress.IPv4Address(end_ip))

    for candidate in network.hosts():
        candidate_value = int(candidate)
        if not (pool_start <= candidate_value <= pool_end):
            return str(candidate)

    first_host = next(network.hosts(), ipaddress.IPv4Address(start_ip))
    return str(first_host)


class Config():
    controller_macAddr = '7e:49:b3:f0:f9:99' # don't modify, a dummy mac address for fill the mac enrty
    dns = '8.8.8.8' # don't modify, just for the dns entry
    start_ip = '192.168.1.2' # can be modified
    end_ip = '192.168.1.100' # can be modified
    netmask = '255.255.255.0' # can be modified

    # You may use above attributes to configure your DHCP server.
    # You can also add more attributes like "lease_time" to support bouns function.


class DHCPServer():
    hardware_addr = Config.controller_macAddr
    start_ip = Config.start_ip
    end_ip = Config.end_ip
    netmask = Config.netmask
    dns = Config.dns
    server_ip = _choose_server_ip(Config.start_ip, Config.end_ip, Config.netmask)
    lease_time = 3600
    ip_by_mac = {}
    mac_by_ip = {}

    @classmethod
    def assemble_ack(cls, pkt, datapath, port):
        pkt_eth = pkt.get_protocol(ethernet.ethernet)
        pkt_ipv4 = pkt.get_protocol(ipv4.ipv4)
        pkt_udp = pkt.get_protocol(udp.udp)
        pkt_dhcp = pkt.get_protocol(dhcp.dhcp)
        assigned_ip = cls.ip_by_mac.get(pkt_dhcp.chaddr)
        if assigned_ip is None:
            return None

        options = cls._build_reply_options(dhcp.DHCP_ACK, assigned_ip)
        ack_pkt = packet.Packet()
        ack_pkt.add_protocol(
            ethernet.ethernet(
                dst=pkt_eth.src,
                src=cls.hardware_addr,
                ethertype=pkt_eth.ethertype,
            )
        )
        ack_pkt.add_protocol(
            ipv4.ipv4(
                src=cls.server_ip,
                dst='255.255.255.255' if pkt_ipv4.src == '0.0.0.0' else pkt_ipv4.src,
                proto=17,
            )
        )
        ack_pkt.add_protocol(
            udp.udp(
                src_port=pkt_udp.dst_port,
                dst_port=pkt_udp.src_port,
            )
        )
        ack_pkt.add_protocol(
            dhcp.dhcp(
                op=dhcp.DHCP_BOOT_REPLY,
                chaddr=pkt_dhcp.chaddr,
                yiaddr=assigned_ip,
                siaddr=cls.server_ip,
                xid=pkt_dhcp.xid,
                flags=pkt_dhcp.flags,
                giaddr=pkt_dhcp.giaddr,
                options=options,
            )
        )
        return ack_pkt

    @classmethod
    def assemble_offer(cls, pkt, datapath):
        pkt_eth = pkt.get_protocol(ethernet.ethernet)
        pkt_ipv4 = pkt.get_protocol(ipv4.ipv4)
        pkt_udp = pkt.get_protocol(udp.udp)
        pkt_dhcp = pkt.get_protocol(dhcp.dhcp)
        offered_ip = cls._allocate_ip(pkt_dhcp.chaddr)
        if offered_ip is None:
            return None

        options = cls._build_reply_options(dhcp.DHCP_OFFER, offered_ip)
        offer_pkt = packet.Packet()
        offer_pkt.add_protocol(
            ethernet.ethernet(
                dst=pkt_eth.src,
                src=cls.hardware_addr,
                ethertype=pkt_eth.ethertype,
            )
        )
        offer_pkt.add_protocol(
            ipv4.ipv4(
                src=cls.server_ip,
                dst='255.255.255.255' if pkt_ipv4.src == '0.0.0.0' else pkt_ipv4.src,
                proto=17,
            )
        )
        offer_pkt.add_protocol(
            udp.udp(
                src_port=pkt_udp.dst_port,
                dst_port=pkt_udp.src_port,
            )
        )
        offer_pkt.add_protocol(
            dhcp.dhcp(
                op=dhcp.DHCP_BOOT_REPLY,
                chaddr=pkt_dhcp.chaddr,
                yiaddr=offered_ip,
                siaddr=cls.server_ip,
                xid=pkt_dhcp.xid,
                flags=pkt_dhcp.flags,
                giaddr=pkt_dhcp.giaddr,
                options=options,
            )
        )
        return offer_pkt

    @classmethod
    def handle_dhcp(cls, datapath, port, pkt):
        pkt_dhcp = pkt.get_protocol(dhcp.dhcp)
        if pkt_dhcp is None or pkt_dhcp.options is None:
            return

        message_type = cls._get_message_type(pkt_dhcp)
        if message_type == dhcp.DHCP_DISCOVER:
            reply_pkt = cls.assemble_offer(pkt, datapath)
        elif message_type == dhcp.DHCP_REQUEST:
            requested_ip = cls._get_requested_ip(pkt_dhcp)
            if requested_ip is None:
                requested_ip = cls.ip_by_mac.get(pkt_dhcp.chaddr)
            reserved_ip = cls._reserve_ip(pkt_dhcp.chaddr, requested_ip)
            if reserved_ip is None:
                reply_pkt = None
            else:
                reply_pkt = cls.assemble_ack(pkt, datapath, port)
        else:
            reply_pkt = None

        if reply_pkt is not None:
            cls._send_packet(datapath, port, reply_pkt)

    @classmethod
    def _build_reply_options(cls, message_type, assigned_ip):
        option_list = [
            dhcp.option(dhcp.DHCP_MESSAGE_TYPE_OPT, struct.pack('!B', message_type)),
            dhcp.option(dhcp.DHCP_SUBNET_MASK_OPT, addrconv.ipv4.text_to_bin(cls.netmask)),
            dhcp.option(dhcp.DHCP_SERVER_IDENTIFIER_OPT, addrconv.ipv4.text_to_bin(cls.server_ip)),
            dhcp.option(dhcp.DHCP_IP_ADDR_LEASE_TIME_OPT, struct.pack('!I', cls.lease_time)),
            dhcp.option(dhcp.DHCP_DNS_SERVER_ADDR_OPT, addrconv.ipv4.text_to_bin(cls.dns)),
        ]
        return dhcp.options(option_list=option_list)

    @classmethod
    def _get_message_type(cls, pkt_dhcp):
        for option in pkt_dhcp.options.option_list:
            if option.tag == dhcp.DHCP_MESSAGE_TYPE_OPT and option.value:
                return option.value[0]
        return None

    @classmethod
    def _get_requested_ip(cls, pkt_dhcp):
        for option in pkt_dhcp.options.option_list:
            if option.tag == dhcp.DHCP_REQUESTED_IP_ADDR_OPT and len(option.value) == 4:
                return addrconv.ipv4.bin_to_text(option.value)
        if pkt_dhcp.ciaddr != '0.0.0.0':
            return pkt_dhcp.ciaddr
        return cls.ip_by_mac.get(pkt_dhcp.chaddr)

    @classmethod
    def _reserve_ip(cls, mac, ip_addr):
        if ip_addr is None or not cls._ip_in_pool(ip_addr):
            return None
        current_owner = cls.mac_by_ip.get(ip_addr)
        if current_owner not in (None, mac):
            return None
        old_ip = cls.ip_by_mac.get(mac)
        if old_ip and old_ip != ip_addr:
            cls.mac_by_ip.pop(old_ip, None)
        cls.ip_by_mac[mac] = ip_addr
        cls.mac_by_ip[ip_addr] = mac
        return ip_addr

    @classmethod
    def _allocate_ip(cls, mac):
        if mac in cls.ip_by_mac:
            return cls.ip_by_mac[mac]
        start = int(ipaddress.IPv4Address(cls.start_ip))
        end = int(ipaddress.IPv4Address(cls.end_ip))
        for ip_value in range(start, end + 1):
            ip_addr = str(ipaddress.IPv4Address(ip_value))
            if ip_addr not in cls.mac_by_ip:
                cls.ip_by_mac[mac] = ip_addr
                cls.mac_by_ip[ip_addr] = mac
                return ip_addr
        return None

    @classmethod
    def _ip_in_pool(cls, ip_addr):
        ip_value = int(ipaddress.IPv4Address(ip_addr))
        return int(ipaddress.IPv4Address(cls.start_ip)) <= ip_value <= int(ipaddress.IPv4Address(cls.end_ip))

    @classmethod
    def _send_packet(cls, datapath, port, pkt):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        if isinstance(pkt, str):
            pkt = pkt.encode()
        pkt.serialize()
        data = pkt.data
        actions = [parser.OFPActionOutput(port=port)]
        out = parser.OFPPacketOut(datapath=datapath,
                                  buffer_id=ofproto.OFP_NO_BUFFER,
                                  in_port=ofproto.OFPP_CONTROLLER,
                                  actions=actions,
                                  data=data)
        datapath.send_msg(out)

