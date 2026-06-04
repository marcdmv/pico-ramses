# pico_stream.py — CONTINUOUS RAMSES burst streamer for the RAMSES web tool.
# Runs on the Pico W via: mpremote connect <port> run pico_stream.py
# Parks on 868.3 MHz, RSSI-gated, and prints one line per burst forever:
#   RUNS <start_level> <nruns> <hexpairs>   (run lengths in samples, 2 hex chars each)
# Wiring (SPI0): SCLK GP2 | MOSI GP3 | MISO GP4 | CSN GP5 | GDO0 GP6 | GDO2 GP7
FREQ_MHZ=868.3; OVERSAMPLE=16; SR_HZ=38400*OVERSAMPLE; RSSI_GATE=-90; NWORDS=460
from machine import Pin,SPI
import rp2,time,array,gc
XOSC=26_000_000; READ_BURST=0xC0
SRES,SRX,SIDLE,SFRX=0x30,0x34,0x36,0x3A; RSSI_REG,MARCSTATE=0x34,0x35
cs=Pin(5,Pin.OUT,value=1); gdo0=Pin(6,Pin.IN)
spi=SPI(0,baudrate=2_000_000,polarity=0,phase=0,sck=Pin(2),mosi=Pin(3),miso=Pin(4))
def wreg(a,v): cs.value(0);spi.write(bytes([a,v]));cs.value(1)
def rstatus(a):
    cs.value(0);spi.write(bytes([a|READ_BURST]));v=spi.read(1)[0];cs.value(1);return v
def strobe(c): cs.value(0);spi.write(bytes([c]));cs.value(1)
def reset():
    cs.value(1);time.sleep_us(5);cs.value(0);time.sleep_us(10)
    cs.value(1);time.sleep_us(45);strobe(SRES);time.sleep_ms(10)
# EXACT evofw3 register config (cc1101_param.c). Data on GDO0 (IOCFG0=0x0D) so we read GP6.
CONFIG={0x00:0x0D,0x02:0x0D,0x03:0x07,0x04:0xD3,0x05:0x91,0x06:0xFF,0x07:0x04,0x08:0x32,
 0x0B:0x0F,0x0C:0x00,0x0D:0x21,0x0E:0x65,0x0F:0x6A,0x10:0x6A,0x11:0x83,0x12:0x10,
 0x13:0x22,0x14:0xF8,0x15:0x50,0x16:0x07,0x17:0x30,0x18:0x18,0x19:0x16,0x1A:0x6C,
 0x1B:0x43,0x1C:0x40,0x1D:0x91,0x21:0x56,0x22:0x10,0x23:0xE9,0x24:0x21,0x25:0x00,
 0x26:0x1F,0x2C:0x81,0x2D:0x35,0x2E:0x09}
def set_freq(mhz):
    f=int(round(mhz*1_000_000*65536/XOSC));wreg(0x0D,(f>>16)&0xFF);wreg(0x0E,(f>>8)&0xFF);wreg(0x0F,f&0xFF)
def rssi():
    raw=rstatus(RSSI_REG);return (raw-256)/2-74 if raw>=128 else raw/2-74
@rp2.asm_pio(autopush=True,push_thresh=32,in_shiftdir=rp2.PIO.SHIFT_LEFT)
def sampler():
    label("loop"); in_(pins,1); jmp("loop")
buf=array.array("I",[0]*NWORDS); runs=array.array("H",[0]*3200); HEXD="0123456789ABCDEF"
def to_runs(words,n):
    start=(words[0]>>31)&1;cur=start;cnt=0;nr=0
    for wi in range(n):
        w=words[wi]
        for b in range(31,-1,-1):
            bit=(w>>b)&1
            if bit==cur: cnt+=1
            else:
                if nr<3200: runs[nr]=cnt if cnt<65535 else 65535;nr+=1
                cur=bit;cnt=1
    if nr<3200: runs[nr]=cnt if cnt<65535 else 65535;nr+=1
    return start,nr
def runs_hex(nr):
    o=[]
    for k in range(nr):
        v=runs[k]
        if v>255:v=255
        o.append(HEXD[v>>4]);o.append(HEXD[v&0xF])
    return "".join(o)
reset()
for a,v in CONFIG.items(): wreg(a,v)
set_freq(FREQ_MHZ)
strobe(SIDLE)
for _ in range(200):
    if (rstatus(MARCSTATE)&0x1F)==0x01: break
strobe(SRX)
sm=rp2.StateMachine(0,sampler,freq=SR_HZ,in_base=gdo0)
print("READY %.3f" % FREQ_MHZ)
while True:
    if rssi()>RSSI_GATE:
        sm.active(0);sm.restart()
        while sm.rx_fifo(): sm.get()
        sm.active(1)
        i=0
        while i<NWORDS:
            if sm.rx_fifo(): buf[i]=sm.get();i+=1
        sm.active(0)
        gc.collect()
        sl,nr=to_runs(buf,i)
        print("RUNS %d %d %s"%(sl,nr,runs_hex(nr)))
        strobe(SIDLE)
        for _ in range(200):
            if (rstatus(MARCSTATE)&0x1F)==0x01: break
        strobe(SFRX);strobe(SRX)
    else:
        time.sleep_ms(2)
