#!/usr/bin/env python3
"""Offline tests for evofw3_pico.py pure logic (no Pico needed).
Stubs `machine` and `rp2` so the gateway module imports on desktop CPython, then exercises:
  - build_message  -> checksummed RAMSES bytes (sum mod 256 == 0)
  - onair encode   -> RX decode round-trip (encoder == receiver)
  - line_from_msg  -> exact evofw3 RX line format
  - command line   -> build_message -> line_from_msg round-trip (drop-in shape)
Run: python3 test_evofw3_pico.py"""
import sys, types

# ---- stub the MicroPython-only modules so the gateway imports on desktop ----
machine = types.ModuleType("machine")
class _Pin:
    OUT = IN = 0
    def __init__(self, *a, **k): pass
    def value(self, *a): return 0
class _SPI:
    def __init__(self, *a, **k): pass
    def write(self, *a): pass
    def read(self, n): return b"\x00" * n
machine.Pin = _Pin; machine.SPI = _SPI
rp2 = types.ModuleType("rp2")
class _PIO: SHIFT_LEFT = 0
rp2.PIO = _PIO
def _asm_pio(*a, **k):
    def deco(f): return f
    return deco
rp2.asm_pio = _asm_pio
# asm_pio bodies reference label()/in_()/jmp() at *call* time only, never on import — safe.
sys.modules["machine"] = machine
sys.modules["rp2"] = rp2

import importlib.util, os
spec = importlib.util.spec_from_file_location(
    "evofw3_pico", os.path.join(os.path.dirname(__file__), "evofw3_pico.py"))
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)   # __name__ != "__main__" -> main() not invoked

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    print(("  ok  " if cond else " FAIL ") + name)
    if cond: PASS += 1
    else: FAIL += 1

# ---- bit-level RX decoder mirroring the receiver (for round-trip; no sample expansion) ----
def decode_onair(msg_bytes):
    ob = G.onair_bytes(msg_bytes)
    bits = []
    for b in ob: bits += [0] + [(b >> k) & 1 for k in range(8)] + [1]
    def frame(p): return None if p + 9 > len(bits) else sum(bits[p+1+k] << k for k in range(8))
    for p in range(len(bits) - 30):
        if frame(p) == 0x33 and frame(p+10) == 0x55 and frame(p+20) == 0x53:
            out = bytearray(); mb = 0; c = 0; q = p + 30
            while q + 10 <= len(bits):
                fb = frame(q); q += 10
                if fb is None or fb == 0x35: break
                lo = G.MAN_DEC[fb & 0xF]; hi = G.MAN_DEC[(fb >> 4) & 0xF]
                if lo == 0xF or hi == 0xF: break
                mb = ((mb << 4) | ((hi << 2) | lo)) & 0xFF; c ^= 1
                if not c: out.append(mb)
            return bytes(out)
    return None

print("== build_message: checksum + structure ==")
# RQ 10E0, amode 3 (addr0+addr1): 18:000730 -> 31:000001
m = G.build_message("RQ --- 18:000730 31:000001 --:------ 10E0 001 00")
check("build_message returns bytes", m is not None)
check("checksum sums to 0 mod 256", (sum(m) & 0xFF) == 0)
check("header verb=RQ amode=3 (h=0x0C)", m[0] == 0x0C)
check("opcode 10E0 + len 01 + payload 00 present", m[7:11] == [0x10, 0xE0, 0x01, 0x00])

print("== onair encode -> RX decode round-trip ==")
back = decode_onair(bytes(m))
check("round-trip decodes identical bytes", back == bytes(m))

print("== line_from_msg: exact evofw3 RX line ==")
line = G.line_from_msg(bytes(m), 52)
check("formats with rssi 052", line is not None and line.startswith("052 RQ --- "))
check("addr fields formatted cc:iiiiii",
      "18:000730 31:000001 --:------ 10E0 001 00" in line)
print("    -> " + repr(line))

print("== I-broadcast (amode 0, all three addrs) ==")
m2 = G.build_message(" I --- 01:123456 63:262143 01:123456 1F09 003 FF073F")
check("amode 0 header (h=0x10)", m2 is not None and m2[0] == 0x10)
check("checksum ok", (sum(m2) & 0xFF) == 0)
l2 = G.line_from_msg(bytes(m2), 40)
check("line round-trips addrs+opcode+payload",
      l2 is not None and "01:123456 63:262143 01:123456 1F09 003 FF073F" in l2)
check("onair round-trip (amode 0)", decode_onair(bytes(m2)) == bytes(m2))
print("    -> " + repr(l2))

print("== seq/param0 path (numeric seq) ==")
m3 = G.build_message(" W 018 18:000730 13:171840 --:------ 0008 002 00C8")
check("param0 sets header bit 0x02", m3 is not None and (m3[0] & 0x02) == 0x02)
check("checksum ok (param path)", (sum(m3) & 0xFF) == 0)
l3 = G.line_from_msg(bytes(m3), 50)
check("seq printed as 018", l3 is not None and l3.split()[2] == "018")
check("onair round-trip (param path)", decode_onair(bytes(m3)) == bytes(m3))
print("    -> " + repr(l3))

print("== rejects malformed input ==")
check("too few tokens -> None", G.build_message("I --- 01:123456") is None)
check("bad verb -> None", G.build_message("XX --- 01:123456 --:------ --:------ 1F09 001 00") is None)
check("len/payload mismatch -> None",
      G.build_message("I --- 01:123456 --:------ --:------ 1F09 003 00") is None)

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
