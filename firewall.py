# firewall.py

import json
import os
from dataclasses import dataclass

from os_ken.ofproto import ether, inet


@dataclass(frozen=True)
class FirewallRule:
    src_ip: str = None
    dst_ip: str = None
    proto: str = None
    src_port: object = None
    dst_port: object = None
    action: str = "deny"


class Firewall:
    COOKIE = 0x305F
    PRIORITY = 60000

    PROTO_MAP = {
        None: 0,
        "": 0,
        "*": 0,
        "any": 0,
        "icmp": inet.IPPROTO_ICMP,
        "tcp": inet.IPPROTO_TCP,
        "udp": inet.IPPROTO_UDP,
    }

    def __init__(self, rule_file="firewall_rule.json"):
        self.rule_file = rule_file
        self.rules = self._load_rules(rule_file)
        self.installed = set()

    # Some helper functions that may be useful
    def _normalize_any(self, value):
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lower() in ["", "*", "any"]:
            return None
        return value

    def _normalize_proto(self, proto):
        proto = self._normalize_any(proto)
        if proto is None:
            return None
        return str(proto).lower()

    def _proto_to_number(self, proto):
        proto = self._normalize_proto(proto)
        return self.PROTO_MAP.get(proto, 0)

    def _normalize_port(self, value):
        value = self._normalize_any(value)
        if value is None:
            return 0
        return int(value)

    def _load_rules(self, rule_file):
        """
        Load firewall rules from firewall_rules.json and return a list of FirewallRule.
        """
        rules = []

        rule_path = rule_file
        if not os.path.isabs(rule_path):
            rule_path = os.path.join(os.path.dirname(__file__), rule_file)

        if not os.path.exists(rule_path):
            return rules

        with open(rule_path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)

        for raw_rule in data.get('rules', []):
            rules.append(
                FirewallRule(
                    src_ip=self._normalize_any(raw_rule.get('src_ip')),
                    dst_ip=self._normalize_any(raw_rule.get('dst_ip')),
                    proto=self._normalize_proto(raw_rule.get('proto')),
                    src_port=self._normalize_any(raw_rule.get('src_port')),
                    dst_port=self._normalize_any(raw_rule.get('dst_port')),
                    action=str(raw_rule.get('action', 'deny')).lower(),
                )
            )

        return rules

    def install_rules(self, ofctls):
        """
        Install firewall rules to all switches.
        """
        for dpid, ofctl in ofctls.items():
            for rule in self.rules:
                if rule.action != 'deny':
                    continue

                nw_proto = self._proto_to_number(rule.proto)
                src_port = self._normalize_port(rule.src_port)
                dst_port = self._normalize_port(rule.dst_port)

                if nw_proto == inet.IPPROTO_ICMP and (src_port or dst_port):
                    continue
                if nw_proto not in (0, inet.IPPROTO_TCP, inet.IPPROTO_UDP) and (src_port or dst_port):
                    continue

                install_key = (dpid, rule)
                if install_key in self.installed:
                    continue

                ofctl.set_flow(
                    cookie=self.COOKIE,
                    priority=self.PRIORITY,
                    dl_type=ether.ETH_TYPE_IP,
                    nw_src=rule.src_ip or 0,
                    nw_dst=rule.dst_ip or 0,
                    nw_proto=nw_proto,
                    tp_src=src_port,
                    tp_dst=dst_port,
                    actions=[],
                )
                self.installed.add(install_key)