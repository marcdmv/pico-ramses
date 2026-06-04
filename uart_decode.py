#!/usr/bin/env python3
"""Decode bursts the evofw3 way: the on-air stream is UART-framed
(start=0, 8 data LSB-first, stop=1). Find the 33 55 53 header as framed bytes,
then read 10-bit frames and Manchester-decode the message."""
import json
MAN=(0xF,0xF,0xF,0xF,0xF,0x3,0x2,0xF,0xF,0x1,0x0,0xF,0xF,0xF,0xF,0xF)
OPC={0x10E0:'device_info',0x1FC9:'rf_bind',0x0008:'relay_demand',0x30C9:'temperature',
 0x3150:'heat_demand',0x2309:'setpoint',0x3B00:'actuator_sync',0x3EF0:'actuator_state',
 0x1060:'battery',0x313F:'datetime',0x000A:'zone_config',0x1100:'tpi_params',0x2E04:'system_mode',
 0x0004:'zone_name',0x0100:'language',0x10A0:'dhw_params',0x1F09:'sync_cycle',0x12B0:'window',
 0x3120:'state',0x22C9:'ufh_setpoint',0x0001:'rf_unknown',0x0009:'relay_failsafe'}
DEVT={1:'CTL',2:'UFC',3:'sensor',4:'TRV',7:'DHW',10:'OTB',12:'THM',13:'BDR',17:'OUT',18:'HGI',
 22:'THm',30:'RFG',34:'RND',37:'FAN',63:'NUL'}

def measure_bp(runs):
    s=[r for r in runs[:80] if 3<r<14]; return sum(s)/len(s) if s else 8.0
def runs_to_bits(sl,runs,bp):
    b=[];lvl=sl
    for r in runs:
        n=max(1,int(round(r/bp)))
        b+=[lvl]*n; lvl^=1
    return b
def frame_byte(bits,p):    # read 10-bit UART frame at p: start,8 data LSB-first,stop
    if p+10>len(bits): return None
    if bits[p]!=0 or bits[p+9]!=1: return None   # need start=0, stop=1
    return sum(bits[p+1+k]<<k for k in range(8))
def frame_byte_loose(bits,p):
    if p+9>len(bits): return None
    return sum(bits[p+1+k]<<k for k in range(8))
def did(b):
    return "%s:%06d"%(DEVT.get(b[0]>>2,"%02d"%(b[0]>>2)),((b[0]&3)<<16)|(b[1]<<8)|b[2])
def asc(b): return ''.join(chr(x) if 32<=x<127 else '.' for x in b)

def try_decode(bits):
    # scan for header 0x33,0x55,0x53 as 3 consecutive UART frames (10 bits each)
    for p in range(len(bits)-30):
        b0=frame_byte_loose(bits,p); b1=frame_byte_loose(bits,p+10); b2=frame_byte_loose(bits,p+20)
        if b0==0x33 and b1==0x55 and b2==0x53:
            # message starts at p+30; read frames, manchester-decode
            msg=bytearray(); mb=0; c=0; q=p+30
            while q+10<=len(bits):
                fb=frame_byte_loose(bits,q); q+=10
                if fb is None: break
                if fb==0x35: break          # trailer
                lo=MAN[fb&0xF]; hi=MAN[(fb>>4)&0xF]
                if lo==0xF or hi==0xF: break
                mb=((mb<<4)|((hi<<2)|lo))&0xFF; c^=1
                if c==0: msg.append(mb)
            if len(msg)>=5: return p,bytes(msg)
    return None

# address_flags[] from evofw3 message.c: which of addr0,addr1,addr2 are present
ADDR_PRESENT=[(1,1,1),(0,0,1),(1,0,1),(1,1,0)]
def parse(msg):
    if len(msg)<6: return ''
    h=msg[0]; verb=("RQ","I","W","RP")[(h>>4)&3]
    amode=(h>>2)&3; present=ADDR_PRESENT[amode]
    p0=h&0x02; p1=h&0x01
    p=1; addrs=[]; addrbytes=[]
    for k in range(3):
        if present[k] and p+3<=len(msg):
            addrs.append(did(msg[p:p+3])); addrbytes+=list(msg[p:p+3]); p+=3
        elif not present[k]:
            addrs.append('--:------')
    params=[]
    if p0 and p<len(msg): params.append(msg[p]); p+=1
    if p1 and p<len(msg): params.append(msg[p]); p+=1
    if p+3>len(msg): return verb+' '+' '.join(addrs)+' (short)'
    op=(msg[p]<<8)|msg[p+1]; p+=2; ln=msg[p]; p+=1
    payload=msg[p:p+ln]
    # canonical-header checksum (evofw3 msg_checksum): csum over canonical hdr + fields == 0
    canon=((h>>4)&3)<<4 | amode<<2 | (0x02 if p0 else 0) | (0x01 if p1 else 0)
    csum=(canon+sum(addrbytes)+sum(params)+(op>>8)+(op&0xFF)+ln+sum(payload)
          +(msg[p+ln] if p+ln<len(msg) else 0)) & 0xFF
    pstr=(' p='+','.join('%d'%x for x in params)) if params else ''
    return "%-2s %s%s %04X(%s) len=%d %s [csum %s]"%(verb,' '.join(addrs),pstr,op,OPC.get(op,'?'),
        ln,' '.join('%02X'%x for x in payload),'OK' if csum==0 else 'x%02X'%csum)

d=json.load(open('/Users/marcdemas/ramses_tool/raw.json'))
print("decoding %d bursts the UART way..."%len(d['raw']))
for rb in d['raw']:
    runs=[int(rb['raw'][i:i+2],16) for i in range(0,rb['nr']*2,2)]
    bp=measure_bp(runs)
    hit=None
    for inv in (0,1):
        bits=runs_to_bits(rb['sl']^inv,runs,bp)
        r=try_decode(bits)
        if r: hit=(inv,)+r; break
    print("--- burst #%d (bp=%.2f) ---"%(rb['id'],bp))
    if hit:
        inv,pos,msg=hit
        chk=(sum(msg)&0xFF)==0
        print("  HEADER FOUND inv=%d @bit%d  msg=%dB checksum=%s"%(inv,pos,len(msg),'OK' if chk else 'x%02X'%(sum(msg)&0xFF)))
        print("  hex:",' '.join('%02X'%x for x in msg))
        print("  asc:",asc(msg))
        print("  ==> ",parse(msg))
    else:
        print("  no UART-framed header found")
