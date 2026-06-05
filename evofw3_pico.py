# evofw3_pico.py — single-file evofw3-compatible RAMSES-II gateway for Pico W + CC1101.
#
# Turns a Raspberry Pi Pico W + CC1101 (868.3 MHz) into a USB-serial gateway that speaks
# the *exact* evofw3 line protocol, so it is a drop-in for ramses_rf / Home Assistant evohome.
#
#   RX  (radio -> host) : one line per decoded frame, evofw3 format:
#          "<rssi> <verb> <seq> <addr0> <addr1> <addr2> <opcode> <len> <payload>\r\n"
#   TX  (host -> radio) : same line shape minus the rssi field, e.g.
#          " I --- 18:000730 --:------ 18:000730 1FC9 018 ..."
#   Boot banner mimics evofw3's version string so ramses_rf recognises the gateway.
#
# Run as the main program:   mpremote connect /dev/cu.usbmodemXXXX run evofw3_pico.py
# Or copy to the Pico as main.py to auto-start on power-up:
#   mpremote connect /dev/cu.usbmodemXXXX cp evofw3_pico.py :main.py
#
# Wiring (hardware SPI0):
#   VCC 3V3(OUT pin36) | GND | MOSI/SI GP3 | SCLK GP2 | MISO/SO GP4 | GDO2 GP7 | GDO0 GP6 | CSN GP5
#
# Lineage: RX config+PIO sampler from pico_stream.py, decode from server.py (decode_burst /
# parse_ramses), TX encode+radio from tx_encode.py / cc1101_tx_test.py — all previously verified.

import sys, select, time, array, gc
from machine import Pin, SPI
import rp2

VERSION = "evofw3_pico 0.1.0"           # printed at boot (ramses_rf reads the version banner)

# ----------------------------------------------------------------------------- CC1101 SPI
XOSC = 26_000_000
WB, RB = 0x40, 0xC0                      # write-burst / read-burst address bits
SRES, STX, SRX, SIDLE, SFTX, SFRX = 0x30, 0x35, 0x34, 0x36, 0x3B, 0x3A
MARCSTATE, RSSI_REG, TXFIFO = 0x35, 0x34, 0x3F

cs   = Pin(5, Pin.OUT, value=1)
gdo0 = Pin(6, Pin.IN)
spi  = SPI(0, baudrate=2_000_000, polarity=0, phase=0,
           sck=Pin(2), mosi=Pin(3), miso=Pin(4))

def wreg(a, v):   cs.value(0); spi.write(bytes([a, v])); cs.value(1)
def strobe(c):    cs.value(0); spi.write(bytes([c]));    cs.value(1)
def wburst(a, d): cs.value(0); spi.write(bytes([a | WB])); spi.write(bytes(d)); cs.value(1)
def rstat(a):
    cs.value(0); spi.write(bytes([a | RB])); v = spi.read(1)[0]; cs.value(1); return v
def reset():
    cs.value(1); time.sleep_us(5); cs.value(0); time.sleep_us(10)
    cs.value(1); time.sleep_us(45); strobe(SRES); time.sleep_ms(10)

# EXACT evofw3 register config (cc1101_param.c). GFSK, 868.3 MHz, ~38.4 kbaud, async-serial RX.
CONFIG = {0x00:0x0D,0x02:0x0D,0x03:0x07,0x04:0xD3,0x05:0x91,0x06:0xFF,0x07:0x04,0x08:0x32,
 0x0B:0x0F,0x0C:0x00,0x0D:0x21,0x0E:0x65,0x0F:0x6A,0x10:0x6A,0x11:0x83,0x12:0x10,
 0x13:0x22,0x14:0xF8,0x15:0x50,0x16:0x07,0x17:0x30,0x18:0x18,0x19:0x16,0x1A:0x6C,
 0x1B:0x43,0x1C:0x40,0x1D:0x91,0x21:0x56,0x22:0x10,0x23:0xE9,0x24:0x21,0x25:0x00,
 0x26:0x1F,0x2C:0x81,0x2D:0x35,0x2E:0x09}

def set_freq(mhz=868.3):
    f = int(round(mhz * 1_000_000 * 65536 / XOSC))
    wreg(0x0D, (f >> 16) & 0xFF); wreg(0x0E, (f >> 8) & 0xFF); wreg(0x0F, f & 0xFF)

def rssi_dbm():
    raw = rstat(RSSI_REG)
    return (raw - 256) / 2 - 74 if raw >= 128 else raw / 2 - 74

def rssi_evofw3():
    # evofw3 prints -dBm as %03u (cc1101.c: rssi = reg/2-74; returns (uint8_t)(-rssi), 10..138)
    v = int(round(-rssi_dbm()))
    return 0 if v < 0 else (255 if v > 255 else v)

def enter_rx():
    strobe(SIDLE)
    for _ in range(200):
        if (rstat(MARCSTATE) & 0x1F) == 0x01: break
    strobe(SFRX); strobe(SRX)

# ----------------------------------------------------------------------------- PIO sampler
OVERSAMPLE = 16; SR_HZ = 38400 * OVERSAMPLE; RSSI_GATE = -90; NWORDS = 460
# 2-instruction loop -> effective sample rate = SR_HZ/2 = 307200 (~8 samples per UART bit).

@rp2.asm_pio(autopush=True, push_thresh=32, in_shiftdir=rp2.PIO.SHIFT_LEFT)
def sampler():
    label("loop"); in_(pins, 1); jmp("loop")

buf  = array.array("I", [0] * NWORDS)
runs = array.array("H", [0] * 3200)

def to_runs(words, n):
    start = (words[0] >> 31) & 1; cur = start; cnt = 0; nr = 0
    for wi in range(n):
        w = words[wi]
        for b in range(31, -1, -1):
            bit = (w >> b) & 1
            if bit == cur:
                cnt += 1
            else:
                if nr < 3200: runs[nr] = cnt if cnt < 65535 else 65535; nr += 1
                cur = bit; cnt = 1
    if nr < 3200: runs[nr] = cnt if cnt < 65535 else 65535; nr += 1
    return start, nr

# ----------------------------------------------------------------------------- decode (RX)
MAN_DEC    = (0xF,0xF,0xF,0xF,0xF,0x3,0x2,0xF,0xF,0x1,0x0,0xF,0xF,0xF,0xF,0xF)
MAN_ENCODE = (0xAA,0xA9,0xA6,0xA5,0x9A,0x99,0x96,0x95,0x6A,0x69,0x66,0x65,0x5A,0x59,0x56,0x55)
ADDR_PRESENT = ((1,1,1),(0,0,1),(1,0,1),(1,1,0))   # evofw3 address_flags index -> slots present
MSGTYPE = ("RQ", " I", " W", "RP")                 # verb by header bits 5:4 (evofw3 MsgType)

def measure_bp(nr):
    s = [runs[i] for i in range(min(nr, 80)) if 3 < runs[i] < 14]
    return sum(s) / len(s) if s else 8.0

def _levels(sl, nr):                       # expand run-lengths -> per-sample level bytes
    L = bytearray(); lvl = sl & 1
    for i in range(nr):
        L += bytes([lvl]) * runs[i]; lvl ^= 1
    return L

def _uart_byte(L, t0, bp):                 # 8 data bits LSB-first sampled at bit centres
    b = 0
    for k in range(8):
        c = int(t0 + (k + 1.5) * bp + 0.5)
        if c >= len(L): return None
        b |= L[c] << k
    return b

def _next_start(L, exp, bp):               # find next start bit (falling edge) near exp -> resync
    lo = max(0, int(exp - 0.6 * bp)); hi = min(len(L) - 1, int(exp + 1.7 * bp))
    for i in range(lo, hi):
        if L[i] and not L[i + 1]: return i + 1
    return None

def decode_burst(sl, nr):
    bp = measure_bp(nr)
    for inv in (0, 1):
        L = _levels(sl ^ inv, nr); N = len(L); i = 0
        while i < N - 1:
            if L[i] and not L[i + 1]:                       # candidate start bit
                s0 = i + 1
                if _uart_byte(L, s0, bp) == 0x33:           # header byte 1
                    s1 = _next_start(L, s0 + 10 * bp, bp)
                    s2 = _next_start(L, s1 + 10 * bp, bp) if s1 else None
                    if s1 and s2 and _uart_byte(L, s1, bp) == 0x55 and _uart_byte(L, s2, bp) == 0x53:
                        msg = bytearray(); mb = 0; c = 0
                        t = _next_start(L, s2 + 10 * bp, bp)
                        while t is not None:
                            fb = _uart_byte(L, t, bp)
                            if fb is None or fb == 0x35: break          # trailer
                            lo = MAN_DEC[fb & 0xF]; hi = MAN_DEC[(fb >> 4) & 0xF]
                            if lo == 0xF or hi == 0xF: break            # bad Manchester
                            mb = ((mb << 4) | ((hi << 2) | lo)) & 0xFF; c ^= 1
                            if not c: msg.append(mb)
                            t = _next_start(L, t + 10 * bp, bp)
                        if len(msg) >= 5: return bytes(msg)
            i += 1
    return None

# --------------------------------------------------------------- RAMSES message <-> evofw3 line
def _fmt_addr(b):
    return "%02d:%06d" % (b[0] >> 2, ((b[0] & 3) << 16) | (b[1] << 8) | b[2])

def line_from_msg(msg, rssi):
    """Format a decoded RAMSES message as an evofw3 RX line (or None if it can't be parsed)."""
    if len(msg) < 6: return None
    h = msg[0]
    verb = MSGTYPE[(h >> 4) & 3]
    present = ADDR_PRESENT[(h >> 2) & 3]
    p0 = h & 0x02; p1 = h & 0x01
    p = 1; addrs = []
    for k in range(3):
        if present[k]:
            if p + 3 > len(msg): return None
            addrs.append(_fmt_addr(msg[p:p+3])); p += 3
        else:
            addrs.append("--:------")
    seq = "---"
    if p0:
        if p >= len(msg): return None
        seq = "%03d" % msg[p]; p += 1
    if p1:
        if p >= len(msg): return None
        p += 1                                   # param1 not surfaced as a field by evofw3
    if p + 3 > len(msg): return None
    op = (msg[p] << 8) | msg[p+1]; p += 2
    ln = msg[p]; p += 1
    payload = "".join("%02X" % x for x in msg[p:p+ln])
    return "%03d %2s %s %s %s %s %04X %03d %s" % (
        rssi, verb, seq, addrs[0], addrs[1], addrs[2], op, ln, payload)

# --------------------------------------------------------------- TX: evofw3 command line -> radio
PREAMBLE = [0x55]*5; SYNC = [0xFF,0x00]; HDR = [0x33,0x55,0x53]; TRAILER = [0x35,0x55]
# present-slots tuple -> address_flags index (amode). Inverse of ADDR_PRESENT.
AMODE = {(1,1,1):0, (0,0,1):1, (1,0,1):2, (1,1,0):3}
VERB_IDX = {"RQ":0, "I":1, "W":2, "RP":3}

def _parse_addr(tok):
    # "cc:iiiiii" -> [b0,b1,b2]; "--:------" -> None
    if tok[0] == "-": return None
    c, _, i = tok.partition(":")
    c = int(c); i = int(i)
    return [((c << 2) & 0xFC) | ((i >> 16) & 3), (i >> 8) & 0xFF, i & 0xFF]

def build_message(line):
    """Parse an evofw3 command line (no rssi) into a checksummed RAMSES message, or None."""
    t = line.split()
    if len(t) < 7: return None
    verb = t[0].upper()
    if verb not in VERB_IDX: return None
    seq = t[1]
    a = [_parse_addr(t[2]), _parse_addr(t[3]), _parse_addr(t[4])]
    present = tuple(1 if x is not None else 0 for x in a)
    if present not in AMODE: return None
    amode = AMODE[present]
    try:
        op = int(t[5], 16); ln = int(t[6])
        pl_hex = "".join(t[7:])                   # payload is contiguous hex (evofw3 line format)
        if len(pl_hex) != 2 * ln: return None
        payload = [int(pl_hex[i:i+2], 16) for i in range(0, 2 * ln, 2)]
    except (ValueError, IndexError):
        return None
    params = [] if seq == "---" else [int(seq)]
    h = ((VERB_IDX[verb] & 3) << 4) | ((amode & 3) << 2) \
        | (0x02 if len(params) >= 1 else 0) | (0x01 if len(params) >= 2 else 0)
    body = [h]
    for x in a:
        if x is not None: body += x
    body += params
    body += [(op >> 8) & 0xFF, op & 0xFF, ln]
    body += payload
    body.append((-sum(body)) & 0xFF)             # checksum: whole message sums to 0 mod 256
    return body

def onair_bytes(msg):
    ob = list(PREAMBLE) + SYNC + HDR
    for x in msg: ob += [MAN_ENCODE[x >> 4], MAN_ENCODE[x & 0xF]]
    ob += TRAILER
    return ob

def pack_msb(msg, inv=0):
    bits = []
    for b in onair_bytes(msg):
        bits += [0] + [(b >> k) & 1 for k in range(8)] + [1]   # start + 8 LSB-first + stop
    while len(bits) % 8: bits.append(1)                        # pad idle-high
    out = bytearray()
    for i in range(0, len(bits), 8):
        v = 0
        for k in range(8): v = (v << 1) | ((bits[i+k] ^ inv) & 1)
        out.append(v)
    return out

def tx_message(msg, inv=0):
    pkt = pack_msb(msg, inv)
    strobe(SIDLE)
    for _ in range(200):
        if (rstat(MARCSTATE) & 0x1F) == 0x01: break
    strobe(SFTX)
    wburst(0x3E, [0xC3,0,0,0,0,0,0,0])           # PATABLE max power
    wreg(0x08, 0x00)                              # PKTCTRL0 = fixed length, FIFO mode
    wreg(0x06, len(pkt) & 0xFF)                   # PKTLEN
    wreg(0x00, 0x06)                              # IOCFG2 sync/pkt
    wburst(TXFIFO, pkt)
    strobe(STX)
    t = time.ticks_ms()
    while (rstat(MARCSTATE) & 0x1F) != 0x01 and time.ticks_diff(time.ticks_ms(), t) < 200:
        pass
    strobe(SIDLE)
    radio_rx_config()                             # restore async-serial RX after FIFO TX
    enter_rx()

def radio_rx_config():
    reset()
    for a, v in CONFIG.items(): wreg(a, v)
    set_freq()

# ----------------------------------------------------------------------------- main loop
def main():
    radio_rx_config()
    sm = rp2.StateMachine(0, sampler, freq=SR_HZ, in_base=gdo0)
    enter_rx()
    poll = select.poll(); poll.register(sys.stdin, select.POLLIN)
    cmd = ""
    print(VERSION)                                # boot banner
    while True:
        # --- host -> radio: drain any pending command bytes (non-blocking) ---
        while poll.poll(0):
            ch = sys.stdin.read(1)
            if ch in ("\n", "\r"):
                if cmd.strip():
                    msg = build_message(cmd.strip())
                    if msg is None:
                        print("# bad command: %s" % cmd.strip())
                    else:
                        tx_message(msg, inv=0)
                        # echo what we sent, evofw3-style (no rssi on TX echo)
                        ln = line_from_msg(bytes(msg), 0)
                        if ln: print(ln[4:])      # strip the dummy "000 " rssi field
                cmd = ""
            elif ch:
                cmd += ch
                if len(cmd) > 256: cmd = ""        # runaway guard

        # --- radio -> host: RSSI-gated burst capture + decode ---
        if rssi_dbm() > RSSI_GATE:
            r = rssi_evofw3()
            sm.active(0); sm.restart()
            while sm.rx_fifo(): sm.get()
            sm.active(1); i = 0
            while i < NWORDS:
                if sm.rx_fifo(): buf[i] = sm.get(); i += 1
            sm.active(0)
            gc.collect()
            sl, nr = to_runs(buf, i)
            msg = decode_burst(sl, nr)
            if msg:
                line = line_from_msg(msg, r)
                if line: print(line)
            enter_rx()
        else:
            time.sleep_ms(2)

if __name__ == "__main__":
    main()
