import random

import os_ident
import pcapy
from impacket import ImpactPacket
from impacket import ImpactDecoder
from impacket.ImpactPacket import TCPOption

Fingerprint = 'Adtran NetVanta 3200 router'
# Fingerprint = 'ADIC Scalar 1000 tape library remote management unit'
# Fingerprint = 'Sun Solaris 9 (SPARC)'
# Fingerprint = 'Sun Solaris 9 (x86)'

Fingerprint = '3Com OfficeConnect 3CRWER100-75 wireless broadband router'  # TI=Z
Fingerprint = 'WatchGuard Firebox X5w firewall/WAP' # TI=RD
# no TI=Hex
Fingerprint = 'FreeBSD 6.0-STABLE - 6.2-RELEASE' # TI=RI
# Fingerprint = 'Microsoft Windows 98 SE' # TI=BI ----> BROKEN! nmap shows no SEQ() output
# Fingerprint = 'Microsoft Windows NT 4.0 SP5 - SP6' # TI=BI
# Fingerprint = 'Microsoft Windows Vista Business' # TI=I
Fingerprint = 'FreeBSD 6.1-RELEASE' # no TI (TI=O)

MAC = "01:02:03:04:05:06"
IP  = "192.168.67.254"
IFACE = "eth0"
TCP_OPEN_PORT = 80
TCP_CLOSED_PORT = 22

O_ETH = 0
O_IP  = 1
O_ARP = 1
O_UDP = 2
O_TCP = 2
O_ICMP = 2

def string2tuple(string):
    if string.find(':') >= 0:
       return [int(x) for x in string.split(':')]
    else:
       return [int(x) for x in string.split('.')]

class Responder:
   templateClass = None
   signatureName      = None

   def __init__(self, machine, port):
       self.machine = machine
       self.port = port
       print "Initializing %s" % self.__class__.__name__
       self.initTemplate()
       self.initFingerprint()

   def initTemplate(self):
       if not self.templateClass:
          self.template_onion = None
       else:
          probe = self.templateClass(0, ['0.0.0.0',self.getIP()],[0, 0])
          self.template_onion = [probe.get_packet()]
          try:
             while 1: self.template_onion.append(self.template_onion[-1].child())
          except: pass
       
          # print "Template: %s" % self.template_onion[O_ETH]
          # print "Options: %r" % self.template_onion[O_TCP].get_padded_options()
          # print "Flags: 0x%04x" % self.template_onion[O_TCP].get_th_flags()

   def initFingerprint(self):
       if not self.signatureName:
          self.fingerprint = None
       else:
          self.fingerprint = self.machine.fingerprint.get_tests()[self.signatureName]
          # print "Fingerprint: %r" % self.fingerprint

   def isMine(self, in_onion):
       return False

   def sendAnswer(self, in_onion):
       pass

   def process(self, in_onion):
       if not self.isMine(in_onion): return False
       print "Got packet for %s" % self.__class__.__name__

       self.sendAnswer(in_onion)
       return True

   def getIP(self):
       return self.machine.ipAddress

class ARPResponder(Responder):
   def isMine(self, in_onion):
       if len(in_onion) < 2: return False

       if in_onion[O_ARP].ethertype != ImpactPacket.ARP.ethertype:
          return False

       return (
          in_onion[O_ARP].get_ar_op() == 1 and # ARP REQUEST
          in_onion[O_ARP].get_ar_tpa() == string2tuple(self.machine.ipAddress))

   def sendAnswer(self, in_onion):
       eth = ImpactPacket.Ethernet()
       arp = ImpactPacket.ARP()
       eth.contains(arp)

       arp.set_ar_hrd(1)	# Hardward type Ethernet
       arp.set_ar_pro(0x800)	# IP
       arp.set_ar_op(2)	# REPLY
       arp.set_ar_hln(6)
       arp.set_ar_pln(4)
       arp.set_ar_sha(string2tuple(self.machine.macAddress))
       arp.set_ar_spa(string2tuple(self.machine.ipAddress))
       arp.set_ar_tha(in_onion[O_ARP].get_ar_sha())
       arp.set_ar_tpa(in_onion[O_ARP].get_ar_spa())

       eth.set_ether_shost(arp.get_ar_sha())
       eth.set_ether_dhost(arp.get_ar_tha())

       self.machine.sendPacket([eth])

class IPResponder(Responder):
   def initAnswer(self, in_onion):
       eth = ImpactPacket.Ethernet()
       ip = ImpactPacket.IP()

       eth.contains(ip)

       eth.set_ether_shost(in_onion[O_ETH].get_ether_dhost())
       eth.set_ether_dhost(in_onion[O_ETH].get_ether_shost())

       ip.set_ip_src(in_onion[O_IP].get_ip_dst())
       ip.set_ip_dst(in_onion[O_IP].get_ip_src())
       ip.set_ip_id(self.machine.getIPID())

       return [eth, ip]

   def sameIPFlags(self, in_onion):
       if not self.template_onion: return True
       return (self.template_onion[O_IP].get_ip_off() & 0xe000) == (in_onion[O_IP].get_ip_off() & 0xe000)

   def isMine(self, in_onion):
       if len(in_onion) < 2: return False

       return (
           (in_onion[O_IP].ethertype == ImpactPacket.IP.ethertype) and
           (in_onion[O_IP].get_ip_dst() == self.machine.ipAddress) and
           self.sameIPFlags(in_onion)
       )

class TCPResponder(IPResponder):
   def initAnswer(self, in_onion):
       out_onion = IPResponder.initAnswer(self, in_onion)
       tcp = ImpactPacket.TCP()

       out_onion[O_IP].contains(tcp)
       out_onion.append(tcp)

       tcp.set_th_dport(in_onion[O_TCP].get_th_sport())
       tcp.set_th_sport(in_onion[O_TCP].get_th_dport())

       return out_onion

   def sameTCPFlags(self, in_onion):
       if not self.template_onion: return True
       in_flags = in_onion[O_TCP].get_th_flags() & 0xfff
       t_flags  = self.template_onion[O_TCP].get_th_flags() & 0xfff

       return in_flags == t_flags

   def sameTCPOptions(self, in_onion):
       if not self.template_onion: return True
       in_options = in_onion[O_TCP].get_padded_options()
       t_options  = self.template_onion[O_TCP].get_padded_options()

       return in_options == t_options

   def isMine(self, in_onion):
       if not IPResponder.isMine(self, in_onion): return False
       if len(in_onion) < 3: return False

       #if in_onion[O_TCP].protocol == ImpactPacket.TCP.protocol:
          # print "Options: %r" % in_onion[O_TCP].get_padded_options()
          # print "Flags: 0x%04x" % in_onion[O_TCP].get_th_flags()

       return (
           in_onion[O_TCP].protocol == ImpactPacket.TCP.protocol and
           in_onion[O_TCP].get_th_dport() == self.port and
           self.sameTCPFlags(in_onion) and
           self.sameTCPOptions(in_onion)
       )

class TCPClosedPort(TCPResponder):
   def __init__(self, *args):
       TCPResponder.__init__(self, *args)

   def isMine(self, in_onion):
       if not TCPResponder.isMine(self, in_onion): return False

       return (
          (in_onion[O_TCP].get_th_dport() == self.port) and
          in_onion[O_TCP].get_SYN())

   def sendAnswer(self, in_onion):
       out_onion = self.initAnswer(in_onion)

       out_onion[O_TCP].set_RST()
       out_onion[O_TCP].set_th_ack(in_onion[O_TCP].get_th_seq()+1)

       self.machine.sendPacket(out_onion)

class TCPOpenPort(TCPResponder):
   def __init__(self, *args):
       TCPResponder.__init__(self, *args)

   def isMine(self, in_onion):
       if not TCPResponder.isMine(self, in_onion): return False

       return (
          (in_onion[O_TCP].get_th_dport() == self.port) and
          in_onion[O_TCP].get_SYN())

   def initAnswer(self, in_onion):
       out_onion = TCPResponder.initAnswer(self, in_onion)

       out_onion[O_TCP].set_SYN()
       out_onion[O_TCP].set_ACK()
       out_onion[O_TCP].set_th_ack(in_onion[O_TCP].get_th_seq()+1)
       out_onion[O_TCP].set_th_seq(random.randint(0,2**32))

       return out_onion

   def sendAnswer(self, in_onion):
       out_onion = self.initAnswer(in_onion)
       self.machine.sendPacket(out_onion)

class NMAP2TCPResponder(TCPResponder):
   def initAnswer(self, in_onion):
       out_onion = TCPResponder.initAnswer(self, in_onion)

       f = self.fingerprint

       # Test R: There is a response = [YN]
       if (f['R'] == 'N'): return None

       # Test DF: Don't fragment IP bit set = [YN]
       if (f['DF'] == 'Y'): out_onion[O_IP].set_ip_df(True)

       # Test W: Initial TCP windows size
       try: win = int(ingerp['W'])
       except: win = 0
       out_onion[O_TCP].set_th_win(0)

       # Test T: Initial TTL = range_low-range_hi, base 16
       # Assumption: we are using the minimum in the TTL range
       try:
          ttl = f['T'].split('-')
          ttl = int(ttl[0], 16)
       except:
          ttl = 0x7f

       # Test TG: Initial TTL Guess. It's just a number, we prefer this
       try: ttl = int(f['TG'], 16)
       except: pass

       # Test CC: Explicit congestion notification
       # Two TCP flags are used in this test: ECE and CWR
       try:
           cc = f['CC']
           if cc == 'N': ece,cwr = 0,0
           if cc == 'Y': ece,cwr = 1,0
           if cc == 'S': ece,cwr = 1,1
           if cc == 'O': ece,cwr = 0,1
       except:
           ece,cwr = 0,0

       if ece: out_onion[O_TCP].set_ECE()
       else:   out_onion[O_TCP].reset_ECE()
       if cwr: out_onion[O_TCP].set_CWR()
       else:   out_onion[O_TCP].reset_CWR()

       out_onion[O_IP].set_ip_ttl(ttl)

       # Test O: TCP Options
       try: options = f['O']
       except: options = ''
       self.setTCPOptions(out_onion, options)
       
       # Test S: TCP Sequence number
       # Z: Sequence number is zero
       # A: Sequence number is the same as the ACK in the probe
       # A+: Sequence number is the same as the ACK in the probe + 1
       # O: Other value
       try: s = f['S']
       except: s = 'O'
       if s == 'Z': out_onion[O_TCP].set_th_seq(0)
       if s == 'A': out_onion[O_TCP].set_th_seq(in_onion[O_TCP].get_th_ack())
       if s == 'A+': out_onion[O_TCP].set_th_seq(in_onion[O_TCP].get_th_ack()+1)
       if s == 'O': out_onion[O_TCP].set_th_seq(self.machine.getTCPSequence())

       # Test A: TCP ACK number
       # Z: Ack is zero
       # S: Ack is the same as the Squence number in the probe
       # S+: Ack is the same as the Squence number in the probe + 1
       # O: Other value
       try: a = f['A']
       except: a = 'O'
       if a == 'Z': out_onion[O_TCP].set_th_ack(0)
       if a == 'S': out_onion[O_TCP].set_th_ack(in_onion[O_TCP].get_th_seq())
       if a == 'S+': out_onion[O_TCP].set_th_ack(in_onion[O_TCP].get_th_seq()+1)

       # Test Q: Quirks
       # R: Reserved bit set (right after the header length)
       # U: Urgent pointer non-zero and URG flag clear
       try: 
          if 'R' in f['Q']: out_onion[O_TCP].set_flags(0x800)
       except: pass
       try: 
          if 'U' in f['Q']: out_onion[O_TCP].set_th_urp(0xffff)
       except: pass

       # Test F: TCP Flags
       try: flags = f['F']
       except: flags = ''
       if 'E' in flags: out_onion[O_TCP].set_ECE()
       if 'U' in flags: out_onion[O_TCP].set_URG()
       if 'A' in flags: out_onion[O_TCP].set_ACK()
       if 'P' in flags: out_onion[O_TCP].set_PSH()
       if 'R' in flags: out_onion[O_TCP].set_RST()
       if 'S' in flags: out_onion[O_TCP].set_SYN()
       if 'F' in flags: out_onion[O_TCP].set_FIN()

       return out_onion

   def setTCPOptions(self, onion, options):
       def getValue(string, i):
           value = 0
           
           idx = i
           for c in options[i:]:
               try:
                   value = value * 0x10 + int(c,16)
               except:
                   break
               idx += 1

           return value, idx

       # Test O,O1=O6: TCP Options
       # L: End of Options
       # N: NOP
       # S: Selective ACK
       # Mx: MSS (x is a hex number)
       # Wx: Windows Scale (x is a hex number)
       # Tve: Timestamp (v and e are two binary digits, v for TSval and e for TSecr

       i = 0
       tcp = onion[O_TCP]
       while i < len(options):
          opt = options[i]
          i += 1
          if opt == 'L': tcp.add_option(TCPOption(TCPOption.TCPOPT_EOL))
          if opt == 'N': tcp.add_option(TCPOption(TCPOption.TCPOPT_NOP))
          if opt == 'S': tcp.add_option(TCPOption(TCPOption.TCPOPT_SACK_PERMITTED))
          if opt == 'T':
             opt = TCPOption(TCPOption.TCPOPT_TIMESTAMP)  # default ts = 0, ts_echo = 0
             if options[i] == '1':  opt.set_ts(self.machine.getTCPTimeStamp())
             if options[i+1] == '1': opt.set_ts_echo(0xffffffffL)
             tcp.add_option(opt)
             i += 2
          if opt == 'M':
             maxseg, i = getValue(options, i)
             tcp.add_option(TCPOption(TCPOption.TCPOPT_MAXSEG, maxseg))
          if opt == 'W':
             window, i = getValue(options, i)
             tcp.add_option(TCPOption(TCPOption.TCPOPT_WINDOW, window))

   def sendAnswer(self, in_onion):
       out_onion = self.initAnswer(in_onion)
       self.machine.sendPacket(out_onion)

class nmap2_ECN(NMAP2TCPResponder):
   templateClass = os_ident.nmap2_ecn_probe
   signatureName      = 'ECN'

class nmap2_SEQ(NMAP2TCPResponder):
   templateClass = None
   signatureName = None
   seqNumber     = None

   def initFingerprint(self):
       NMAP2TCPResponder.initFingerprint(self)
       if not self.seqNumber: return
       else:
          OPS = self.machine.fingerprint.get_tests()['OPS']
          WIN = self.machine.fingerprint.get_tests()['WIN']
          self.fingerprint['O'] = OPS['O%d' % self.seqNumber]
          self.fingerprint['W'] = WIN['W%d' % self.seqNumber]
          # print "Fingerprint: %r" % self.fingerprint

class nmap2_SEQ1(nmap2_SEQ):
   templateClass = os_ident.nmap2_seq_1
   signatureName = 'T1'
   seqNumber     = 1

class nmap2_SEQ2(nmap2_SEQ):
   templateClass = os_ident.nmap2_seq_2
   signatureName = 'T1'
   seqNumber     = 2

class nmap2_SEQ3(nmap2_SEQ):
   templateClass = os_ident.nmap2_seq_3
   signatureName = 'T1'
   seqNumber     = 3

class nmap2_SEQ4(nmap2_SEQ):
   templateClass = os_ident.nmap2_seq_4
   signatureName = 'T1'
   seqNumber     = 4

class nmap2_SEQ5(nmap2_SEQ):
   templateClass = os_ident.nmap2_seq_5
   signatureName = 'T1'
   seqNumber     = 5

class nmap2_SEQ6(nmap2_SEQ):
   templateClass = os_ident.nmap2_seq_6
   signatureName = 'T1'
   seqNumber     = 6

class nmap2_T2(NMAP2TCPResponder):
   templateClass = os_ident.nmap2_tcp_open_2
   signatureName = 'T2'

class nmap2_T3(NMAP2TCPResponder):
   templateClass = os_ident.nmap2_tcp_open_3
   signatureName = 'T3'

class nmap2_T4(NMAP2TCPResponder):
   templateClass = os_ident.nmap2_tcp_open_4
   signatureName = 'T4'

class nmap2_T5(NMAP2TCPResponder):
   templateClass = os_ident.nmap2_tcp_closed_1
   signatureName = 'T5'

class nmap2_T6(NMAP2TCPResponder):
   templateClass = os_ident.nmap2_tcp_closed_2
   signatureName = 'T6'

class nmap2_T7(NMAP2TCPResponder):
   templateClass = os_ident.nmap2_tcp_closed_3
   signatureName = 'T7'

class Machine:
   AssumedTimeIntervalPerPacket = 0.11 # seconds
   def __init__(self, emmulating, ipAddress, macAddress):
       self.ipAddress = ipAddress
       self.macAddress = macAddress
       self.responders = []
       self.decoder = ImpactDecoder.EthDecoder()

       self.initPcap()
       self.initFingerprint(emmulating)
       self.initResponders()

       self.initSequenceGenerators()

   def initPcap(self):
       self.pcap = pcapy.open_live(IFACE, 65535, 1, 1)
       self.pcap.setfilter("host %s or ether host %s" % (self.ipAddress, self.macAddress))

   def initResponders(self):
       self.addResponder(ARPResponder(self, 0))
       self.addResponder(nmap2_ECN(self, TCP_OPEN_PORT))
       self.addResponder(nmap2_SEQ1(self, TCP_OPEN_PORT))
       self.addResponder(nmap2_SEQ2(self, TCP_OPEN_PORT))
       self.addResponder(nmap2_SEQ3(self, TCP_OPEN_PORT))
       self.addResponder(nmap2_SEQ4(self, TCP_OPEN_PORT))
       self.addResponder(nmap2_SEQ5(self, TCP_OPEN_PORT))
       self.addResponder(nmap2_SEQ6(self, TCP_OPEN_PORT))
       self.addResponder(nmap2_T2(self, TCP_OPEN_PORT))
       self.addResponder(nmap2_T3(self, TCP_OPEN_PORT))
       self.addResponder(nmap2_T4(self, TCP_OPEN_PORT))
       self.addResponder(nmap2_T5(self, TCP_CLOSED_PORT))
       self.addResponder(nmap2_T6(self, TCP_CLOSED_PORT))
       self.addResponder(nmap2_T7(self, TCP_CLOSED_PORT))
       self.addResponder(TCPOpenPort(self, TCP_OPEN_PORT))
       self.addResponder(TCPClosedPort(self, TCP_CLOSED_PORT))

   def initFingerprint(self, emmulating):
       fpm = os_ident.NMAP2_Fingerprint_Matcher('')
       f = file('nmap-os-db','r')
       for text in fpm.fingerprints(f):
           fingerprint = fpm.parse_fp(text)
           if fingerprint.get_id() == emmulating:
              self.fingerprint = fingerprint
              self.simplifyFingerprint()
              print "Emmulating: %s" % fingerprint.get_id()
              print fingerprint
              return

       raise Exception, "Couldn't find fingerprint data for %r" % emmulating

   def simplifyFingerprint(self):
       tests = self.fingerprint.get_tests()
       for probeName in tests:
           probe = tests[probeName]
           for test in probe:
               probe[test] = probe[test].split('|')[0]
               
   def initSequenceGenerators(self):
       self.initIPIDGenerator()
       self.initTCPISNGenerator()
       self.initTCPTSGenerator()

   def initIPIDGenerator(self):
       self.ip_ID = 0

       try:
          TI = self.fingerprint.get_tests()['SEQ']['TI']
       except:
          TI = 'O'

       if   TI == 'Z': self.ip_ID_delta = 0
       elif TI == 'RD': self.ip_ID_delta = 30000
       elif TI == 'RI': self.ip_ID_delta = 1234
       elif TI == 'BI': self.ip_ID_delta = 1024+256
       elif TI == 'I': self.ip_ID_delta = 1
       elif TI == 'O': self.ip_ID_delta = 123
       else: self.ip_ID_delta = int(TI, 16)

       print "IP ID Delta: %d" % self.ip_ID_delta

   def initTCPISNGenerator(self):
       # tcp_ISN and tcp_ISN_delta for TCP Initial sequence numbers
       self.tcp_ISN = 0
       try:
          self.tcp_ISN_GCD = int(self.fingerprint.get_tests()['SEQ']['GCD'].split('-')[0], 16)
       except:
          self.tcp_ISN_GCD = 1

       try:
          isr = self.fingerprint.get_tests()['SEQ']['ISR'].split('-')
          if len(isr) == 1:
             isr = int(isr[0], 16)
          else:
             isr = (int(isr[0], 16) + int(isr[1], 16)) / 2
       except:
          isr = 0

       try:
          sp = self.fingerprint.get_tests()['SEQ']['SP'].split('-')
          sp = int(sp[0], 16)
       except:
          sp = 0

       self.tcp_ISN_stdDev = (2**(sp/8.0)) * 5 / 4  # n-1 on small populations... erm...

       if self.tcp_ISN_GCD > 9:
          self.tcp_ISN_stdDev *= self.tcp_ISN_GCD

       self.tcp_ISN_stdDev *= self.AssumedTimeIntervalPerPacket

       self.tcp_ISN_delta  = 2**(isr/8.0) * self.AssumedTimeIntervalPerPacket

       # generate a few, so we don't start with 0 when we don't have to
       for i in range(10): self.getTCPSequence()

       print "TCP ISN Delta: %f" % self.tcp_ISN_delta
       print "TCP ISN Standard Deviation: %f" % self.tcp_ISN_stdDev

   def initTCPTSGenerator(self):
       # tcp_TS and tcp_TS_delta for TCP Time stamp generation
       self.tcp_TS = 0

       try: ts = self.fingerprint.get_tests()['SEQ']['TS']
       except: ts = 'U'

       if ts == 'U' or ts == 'Z': self.tcp_TS_delta = 0
       else:
           self.tcp_TS_delta = (2**int(ts, 16)) * self.AssumedTimeIntervalPerPacket

       # generate a few, so we don't start with 0 when we don't have to
       for i in range(10): self.getTCPTimeStamp()

       print "TCP TS Delta: %f" % self.tcp_TS_delta

   def getIPID(self):
       answer = self.ip_ID
       self.ip_ID += self.ip_ID_delta
       self.ip_ID %= 0x10000L
       # print "IP ID: %x" % answer
       return answer

   def getTCPSequence(self):
       answer = self.tcp_ISN + self.tcp_ISN_stdDev
       self.tcp_ISN_stdDev *= -1
       answer = int(round(answer/self.tcp_ISN_GCD) * self.tcp_ISN_GCD)
       self.tcp_ISN += self.tcp_ISN_delta
       self.tcp_ISN %= 0x100000000L
       # print "TCP ISN: %x" % answer
       return answer

   def getTCPTimeStamp(self):
       answer = int(round(self.tcp_TS))
       self.tcp_TS += self.tcp_TS_delta
       self.tcp_TS %= 0x100000000L
       # print "TCP Time Stamp: %x" % answer
       return answer

   def sendPacket(self, onion):
       if not onion: return
       print "--> Packet sent"
       #print onion[0]
       #print
       self.pcap.sendpacket(onion[O_ETH].get_packet())

   def addResponder(self, aResponder):
       self.responders.append(aResponder)

   def run(self):
       while 1:
          p = self.pcap.next()
          in_onion = [self.decoder.decode(p[1])]
          try:
             while 1: in_onion.append(in_onion[-1].child())
          except:
             pass

          #print "-------------- Received: ", in_onion[0]
          for r in self.responders:
              if r.process(in_onion): break


def main():
   Machine(Fingerprint, IP, MAC).run()

if __name__ == '__main__':
   main()

# All Probes
# [|] SEQ
# [x] OPS
# [x] WIN
# [x] T1
# [x] T2
# [x] T3
# [x] T4
# [x] T5
# [x] T6
# [x] T7
# [ ] IE
# [x] ECN
# [ ] U1

# All Tests

# SEQ()
# [x] TCP ISN sequence predictability index (SP)
# [x] TCP ISN greatest common divisor (GCD)
# [x] TCP ISN counter rate (ISR)
# [ ] IP ID sequence generation algorithm (TI)
#   [+] Z  - All zeros
#   [+] RD - Random: It increments at least once by at least 20000.
#   [-] Hex Value - fixed IP ID
#   [+] RI - Random positive increments. Any (delta_i > 1000, and delta_i % 256 != 0) or (delta_i > 256000 and delta_i % 256 == 0)
#   [x] BI - Broken increment. All delta_i % 256 = 0 and all delta_i <= 5120.
#   [x] I - Incremental. All delta_i < 10
#   [x] O - (Ommited, the test does not show in the fingerprint). None of the other
# [ ] IP ID sequence generation algorithm (CI)
# [-] IP ID sequence generation algorithm (II)
# [ ] Shared IP ID sequence Boolean (SS)
# [x] TCP timestamp option algorithm (TS)
#   [x] U - unsupported (don't send TS)
#   [x] 0 - Zero
#   [x] 1 - 0-5.66 (2 Hz)
#   [x] 7 - 70-150 (100 Hz)
#   [x] 8 - 150-350 (200 Hz)
#   [x]   - avg_freq = sum(TS_diff/time_diff) . round(.5 + math.log(avg_freq)/math.log(2)))
#           time_diff = 0.11 segs
# OPS()
# [x] TCP options (O, O1-O6)
# WIN()
# [x] TCP initial window size (W, W1-W6)
# ECN, T1-T7
# [x] TCP options (O, O1-O6)
# [x] TCP initial window size (W, W1-W6)
# [x] Responsiveness (R)
# [x] IP don't fragment bit (DF)
# [x] IP initial time-to-live (T)
# [x] IP initial time-to-live guess (TG)
# [x] Explicit congestion notification (CC)
# [x] TCP miscellaneous quirks (Q)
# [x] TCP sequence number (S)
# [x] TCP acknowledgment number (A)
# [x] TCP flags (F)
# [ ] TCP RST data checksum (RD)
# IE()
# [ ] Responsiveness (R)
# [ ] Don't fragment (ICMP) (DFI)
# [ ] IP initial time-to-live (T)
# [ ] IP initial time-to-live guess (TG)
# [ ] ICMP response code (CD)
# [ ] IP Type of Service (TOSI)
# [ ] ICMP Sequence number (SI)
# [ ] IP Data Length (DLI)
# U1()
# [ ] Responsiveness (R)
# [ ] IP don't fragment bit (DF)
# [ ] IP initial time-to-live (T)
# [ ] IP initial time-to-live guess (TG)
# [ ] IP total length (IPL)
# [ ] Unused port unreachable field nonzero (UN)
# [ ] Returned probe IP total length value (RIPL)
# [ ] Returned probe IP ID value (RID)
# [ ] Integrity of returned probe IP checksum value (RIPCK)
# [ ] Integrity of returned probe UDP checksum (RUCK)
# [ ] Integrity of returned UDP data (RUD)
# [ ] ??? (TOS) Type of Service
# [ ] ??? (RUL) Length of return UDP packet is correct
