import time

from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.topo import Topo


def disable_ipv6(node):
    node.cmd("sysctl -w net.ipv6.conf.all.disable_ipv6=1")
    node.cmd("sysctl -w net.ipv6.conf.default.disable_ipv6=1")
    node.cmd("sysctl -w net.ipv6.conf.lo.disable_ipv6=1")


def send_arp(node, count=1):
    node.cmd('arping -c %s -A -I %s-eth0 %s' % (count, node.name, node.IP()))


def do_arp_all(net):
    for host in net.hosts:
        send_arp(host)


def print_demo_commands():
    print('\n===== Suggested Demo Commands =====')
    print('pingall')
    print('link s2 s5 down')
    print('link s2 s5 up')
    print('link s4 s7 down')
    print('link s4 s7 up')
    print('switch s7 stop')
    print('switch s7 start')
    print('sh ovs-ofctl mod-port s4 5 down')
    print('sh ovs-ofctl mod-port s4 5 up')
    print('links')
    print('net')
    print('===================================\n')


class ComplexTopo(Topo):
    def __init__(self, **opts):
        Topo.__init__(self, **opts)

        hosts = {}
        switches = {}
        host_ips = {
            'h1': '192.168.117.2/24',
            'h2': '192.168.117.3/24',
            'h3': '192.168.117.4/24',
            'h4': '192.168.117.5/24',
            'h5': '192.168.117.6/24',
            'h6': '192.168.117.7/24',
            'h7': '192.168.117.8/24',
        }

        for index in range(1, 8):
            host_name = 'h%s' % index
            switch_name = 's%s' % index
            hosts[host_name] = self.addHost(host_name, ip=host_ips[host_name])
            switches[switch_name] = self.addSwitch(switch_name)
            self.addLink(hosts[host_name], switches[switch_name])

        switch_links = [
            ('s1', 's2'),
            ('s2', 's3'),
            ('s3', 's4'),
            ('s4', 's5'),
            ('s5', 's6'),
            ('s6', 's7'),
            ('s1', 's4'),
            ('s2', 's5'),
            ('s3', 's6'),
            ('s4', 's7'),
            ('s2', 's7'),
        ]

        for left, right in switch_links:
            self.addLink(switches[left], switches[right])


def run_mininet():
    topo = ComplexTopo()
    net = Mininet(topo=topo, autoSetMacs=True, controller=RemoteController)

    for host in net.hosts:
        disable_ipv6(host)

    for switch in net.switches:
        disable_ipv6(switch)

    net.start()
    time.sleep(2)

    for _ in range(2):
        do_arp_all(net)
        time.sleep(1)

    print_demo_commands()
    CLI(net)
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    run_mininet()