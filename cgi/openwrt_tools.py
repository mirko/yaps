#!/usr/bin/python3

import subprocess
from re import search as re_compile
from sys import stderr

class DHCP():
    def __init__(self):
        self.leases = []

    def fetch(self):
        #TODO: This relies on dnsmasq, while there are multiple DHCP server implementation within OpenWrt (mainly odhcpd)
        self.leases = []
        lines = open("/tmp/dhcp.leases", "r").readlines()
        for line in lines:
            if not line.strip():
                continue
            split = line.split(' ')
            self.leases.append({'mac': split[1], 'ip': split[2], 'name': split[3]})

class FStools():
    def __init__(self):
        self.result = ''

    def fetch(self):
        proc = subprocess.Popen("/sbin/block info", stdout=subprocess.PIPE, shell=True)
        self.result = proc.communicate()[0].decode("utf-8")

    def mounted(self, fslabel=None, uuid=None, device=None):
        if not (fslabel or uuid or device):
            raise Exception("must provide either fslabel or uuid or device")

        if fslabel:
            #return True if re_compile("LABEL=\"(%s)\"" % fslabel, self.result) else False
            return True if re_compile('LABEL="(%s) *.?".*MOUNT="(.*)"' % fslabel, self.result) else False

        if uuid:
            return True if re_compile("UUID=\"(%s)\".*MOUNT=\"(.*)\"" % uuid, self.result) else False

        if device:
            return True if re_compile("^(%s): .*MOUNT=\"(.*)\"" % uuid, self.result) else False
