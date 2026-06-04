# cc1101_tx_test.py — SAFE transmit test: send RQ 10E0 (device-info request) to the
# thermostat and listen for its RP reply. No boiler/relay command involved.
# Tries both FSK polarities (our RX needed inversion, so TX may too).
# Wiring (SPI0): SCLK GP2 MOSI GP3 MISO GP4 CSN GP5 GDO0 GP6 GDO2 GP7
from machine import Pin, SPI
import rp2, time, array, gc

XOSC=26_000_000; WB,RB=0x40,0xC0
SRES,STX,SRX,SIDLE,SFTX,SFRX=0x30,0x35,0x34,0x36,0x3B,0x3A
MARCSTATE=0x35; RSSI_REG=0x34; TXFIFO=0x3F
MAN_ENCODE=(0xAA,0xA9,0xA6,0xA5,0x9A,0x99,0x96,0x95,0x6A,0x69,0x66,0x65,0x5A,0x59,0x56,0x55)
MAN_DEC=(0xF,0xF,0xF,0xF,0xF,0x3,0x2,0xF,0xF,0x1,0x0,0xF,0xF,0xF,0xF,0xF)

cs=Pin(5,Pin.OUT,value=1); gdo0=Pin(6,Pin.IN)
spi=SPI(0,baudrate=2_000_000,polarity=0,phase=0,sck=Pin(2),mosi=Pin(3),miso=Pin(4))
def wreg(a,v): cs.value(0);spi.write(bytes([a,v]));cs.value(1)
def rstat(a): cs.value(0);spi.write(bytes([a|RB]));v=spi.read(1)[0];cs.value(1);return v
def strobe(c): cs.value(0);spi.write(bytes([c]));cs.value(1)
def wburst(a,data): cs.value(0);spi.write(bytes([a|WB]));spi.write(bytes(data));cs.value(1)
def reset():
    cs.value(1);time.sleep_us(5);cs.value(0);time.sleep_us(10)
    cs.value(1);time.sleep_us(45);strobe(SRES);time.sleep_ms(10)

# base config (evofw3 values); per-mode PKTCTRL0/IOCFG set below
BASE={0x0B:0x0F,0x0C:0x00,0x0D:0x21,0x0E:0x65,0x0F:0x6A,0x10:0x6A,0x11:0x83,0x12:0x10,
 0x13:0x22,0x14:0xF8,0x15:0x50,0x16:0x07,0x17:0x30,0x18:0x18,0x19:0x16,0x1A:0x6C,
 0x1B:0x43,0x1C:0x40,0x1D:0x91,0x21:0x56,0x22:0x10,0x23:0xE9,0x24:0x21,0x25:0x00,
 0x26:0x1F,0x2C:0x81,0x2D:0x35,0x2E:0x09,0x03:0x07,0x04:0xD3,0x05:0x91,0x07:0x04}
def set_freq():
    f=int(round(868.3*1_000_000*65536/XOSC));wreg(0x0D,(f>>16)&0xFF);wreg(0x0E,(f>>8)&0xFF);wreg(0x0F,f&0xFF)
def init():
    reset()
    for a,v in BASE.items(): wreg(a,v)
    set_freq()
    wburst(0x3E,[0xC3,0,0,0,0,0,0,0])   # PATABLE: 0xC3 max

# ---- build + encode RQ 10E0 -> your thermostat (set the ID below) ----
def make_addr(c,i): return [((c<<2)&0xFC)|((i>>16)&3),(i>>8)&0xFF,i&0xFF]
def build(verb,amode,addrs,op,pl,params=()):
    h=((verb&3)<<4)|((amode&3)<<2)|(0x02 if len(params)>=1 else 0)|(0x01 if len(params)>=2 else 0)
    b=[h]
    for c,i in addrs: b+=make_addr(c,i)
    b+=list(params); b+=[(op>>8)&0xFF,op&0xFF,len(pl)]; b+=list(pl)
    b.append((-sum(b))&0xFF); return b
def onair_bytes(msg):
    ob=[0x55]*5+[0xFF,0x00]+[0x33,0x55,0x53]
    for x in msg: ob+=[MAN_ENCODE[x>>4],MAN_ENCODE[x&0xF]]
    ob+=[0x35,0x55]; return ob
def frame_bits(ob):
    bits=[]
    for b in ob: bits+=[0]+[(b>>k)&1 for k in range(8)]+[1]
    return bits
def pack_msb(bits,inv):
    while len(bits)%8: bits=bits+[1]   # pad with idle high
    out=bytearray()
    for i in range(0,len(bits),8):
        v=0
        for k in range(8): v=(v<<1)|((bits[i+k]^inv)&1)
        out.append(v)
    return out

MSG=build(0,3,[(18,730),(31,1)],0x10E0,[0x00])
BITS=frame_bits(onair_bytes(MSG))
print("TX msg:"," ".join("%02X"%x for x in MSG))

def tx_packet(payload):
    strobe(SIDLE)
    while (rstat(MARCSTATE)&0x1F)!=0x01: pass
    strobe(SFTX)
    wburst(0x3E,[0xC3,0,0,0,0,0,0,0])  # PATABLE (must re-arm after any reset)
    wreg(0x08,0x00)                 # PKTCTRL0 fixed length, FIFO
    wreg(0x06,len(payload)&0xFF)    # PKTLEN
    wreg(0x00,0x06)                 # IOCFG2 sync/pkt
    wburst(TXFIFO,payload)
    strobe(STX)
    t=time.ticks_ms()
    while (rstat(MARCSTATE)&0x1F)!=0x01 and time.ticks_diff(time.ticks_ms(),t)<200:
        pass
    strobe(SIDLE)

# ---- RX capture (async+PIO) to listen for a reply ----
@rp2.asm_pio(autopush=True,push_thresh=32,in_shiftdir=rp2.PIO.SHIFT_LEFT)
def samp():
    label("l"); in_(pins,1); jmp("l")
def rx_setup():
    reset()                                  # full clean reset after TX
    for a,v in BASE.items(): wreg(a,v)
    set_freq()
    wreg(0x08,0x32); wreg(0x02,0x0D); wreg(0x00,0x0D)   # async, GDO0=data
    strobe(SIDLE)
    for _ in range(200):
        if (rstat(MARCSTATE)&0x1F)==0x01: break
    strobe(SRX); time.sleep_ms(5)
    print("  rx state marc=0x%02X rssi=%d"%(rstat(MARCSTATE)&0x1F,int(rssi())))
def rssi():
    raw=rstat(RSSI_REG); return (raw-256)/2-74 if raw>=128 else raw/2-74

print("Init CC1101...")
init()
LEN=len(pack_msb(BITS[:],0))
print("packet bytes:",LEN)
sm=rp2.StateMachine(0,samp,freq=614400,in_base=gdo0)
buf=array.array("I",[0]*250); HEXD="0123456789ABCDEF"
def to_runs(words,n):
    sl=(words[0]>>31)&1; cur=sl; cnt=0; out=[]
    for wi in range(n):
        w=words[wi]
        for b in range(31,-1,-1):
            bit=(w>>b)&1
            if bit==cur: cnt+=1
            else: out.append(cnt if cnt<255 else 255); cur=bit; cnt=1
    out.append(cnt if cnt<255 else 255)
    return sl,out
pk=pack_msb(BITS[:],0)   # inv=0 (matches our RX polarity)
print("INTERLEAVED TX+RX ~55s — reboot the device now")
tend=time.ticks_add(time.ticks_ms(),55000)
while time.ticks_diff(tend,time.ticks_ms())>0:
    tx_packet(pk); time.sleep_ms(25); tx_packet(pk)   # send our RQ twice
    rx_setup()                                        # back to RX
    t=time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(),t)<4000:    # listen 4s for a reply / traffic
        if rssi()>-92:
            sm.active(0);sm.restart()
            while sm.rx_fifo(): sm.get()
            sm.active(1); i=0
            while i<250:
                if sm.rx_fifo(): buf[i]=sm.get();i+=1
            sm.active(0)
            sl,runs=to_runs(buf,i)
            print("RUNS %d %d %s"%(sl,len(runs),"".join(HEXD[r>>4]+HEXD[r&0xF] for r in runs)))
            strobe(SIDLE)
            for _ in range(100):
                if (rstat(MARCSTATE)&0x1F)==0x01: break
            strobe(SFRX);strobe(SRX)
print("DONE")
