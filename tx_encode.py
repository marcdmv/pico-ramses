#!/usr/bin/env python3
"""RAMSES-II transmit encoder (mirror of evofw3 TX) + round-trip self-test.
Build message -> Manchester-encode -> UART-frame -> on-air cell stream.
Then decode it back with our receive logic to prove the encoder is correct.
No radio involved — this validates the encode path safely."""

MAN_ENCODE=(0xAA,0xA9,0xA6,0xA5,0x9A,0x99,0x96,0x95,0x6A,0x69,0x66,0x65,0x5A,0x59,0x56,0x55)
MAN_DEC   =(0xF,0xF,0xF,0xF,0xF,0x3,0x2,0xF,0xF,0x1,0x0,0xF,0xF,0xF,0xF,0xF)
ADDR_PRESENT=[(1,1,1),(0,0,1),(1,0,1),(1,1,0)]
PREAMBLE=[0x55]*5; SYNC=[0xFF,0x00]; HEADER=[0x33,0x55,0x53]; TRAILER=[0x35,0x55]

# ---- build a RAMSES message (header byte + fields + checksum) ----
def make_addr(class_, id_):
    return [((class_<<2)&0xFC)|((id_>>16)&3), (id_>>8)&0xFF, id_&0xFF]
def build_message(verb, amode, addrs, opcode, payload, params=()):
    """verb 0..3, amode 0..3 (address_flags index), addrs = list of (class,id) for present slots."""
    h = ((verb&3)<<4) | ((amode&3)<<2) | (0x02 if len(params)>=1 else 0) | (0x01 if len(params)>=2 else 0)
    body=[h]
    for c,i in addrs: body += make_addr(c,i)
    for p in params: body.append(p)
    body += [(opcode>>8)&0xFF, opcode&0xFF, len(payload)]
    body += list(payload)
    body.append((-sum(body)) & 0xFF)           # checksum: whole message sums to 0
    return body

# ---- TX encode: bytes -> on-air cell stream ----
def manchester_bytes(msg):                      # each msg byte -> 2 man_encode bytes
    out=[]
    for b in msg: out += [MAN_ENCODE[b>>4], MAN_ENCODE[b&0xF]]
    return out
def uart_frame_bits(byte):                      # start(0) + 8 data LSB-first + stop(1)
    return [0] + [(byte>>k)&1 for k in range(8)] + [1]
def encode_onair(msg):
    onair_bytes = PREAMBLE + SYNC + HEADER + manchester_bytes(msg) + TRAILER
    bits=[]
    for b in onair_bytes: bits += uart_frame_bits(b)
    return bits

# ---- RX decode (same logic as the receiver) for round-trip check ----
def _frame(bits,p): return None if p+9>len(bits) else sum(bits[p+1+k]<<k for k in range(8))
def decode_onair(bits):
    for p in range(len(bits)-30):
        if _frame(bits,p)==0x33 and _frame(bits,p+10)==0x55 and _frame(bits,p+20)==0x53:
            msg=bytearray(); mb=0; c=0; q=p+30
            while q+10<=len(bits):
                fb=_frame(bits,q); q+=10
                if fb is None or fb==0x35: break
                lo=MAN_DEC[fb&0xF]; hi=MAN_DEC[(fb>>4)&0xF]
                if lo==0xF or hi==0xF: break
                mb=((mb<<4)|((hi<<2)|lo))&0xFF; c^=1
                if not c: msg.append(mb)
            return bytes(msg)
    return None

if __name__=="__main__":
    # Example: RQ 10E0 (device-info request) to your thermostat (set the ID below) — a SAFE probe.
    # "RQ <our_id> <thermostat> 10E0 00"  (amode 3 = addr0+addr1)
    msg = build_message(verb=0, amode=3,
                        addrs=[(18,730), (31,1)],   # 18:000730 (fake HGI) -> 31:000001 (← set to YOUR thermostat's ID, read it from the sniffer)
                        opcode=0x10E0, payload=[0x00])
    print("built message:", " ".join("%02X"%b for b in msg))
    print("  checksum byte:", "%02X"%msg[-1], " sum mod256 =", sum(msg)&0xFF, "(0 = valid)")
    bits = encode_onair(msg)
    print("  on-air: %d UART bits (%d bytes incl framing)"%(len(bits), len(bits)//10))
    back = decode_onair(bits)
    print("decoded back:", " ".join("%02X"%b for b in back) if back else "FAIL")
    print("ROUND-TRIP:", "✓ PASS  (encoder verified)" if back==bytes(msg) else "✗ MISMATCH")
