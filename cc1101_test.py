# cc1101_test.py — SPI connectivity check for CC1101 on Pico W
# Wiring (SPI0):
#   SCLK GP2 | MOSI(SI) GP3 | MISO(SO) GP4 | CSN GP5 | GDO0 GP6 | GDO2 GP7
#   VCC -> 3V3(OUT) pin36 | GND -> GND
# Expect: PARTNUM=0x00, VERSION=0x14 (some chips 0x04).

from machine import Pin, SPI
import time

SCK_PIN, MOSI_PIN, MISO_PIN, CS_PIN = 2, 3, 4, 5

READ_BURST = 0xC0
PARTNUM, VERSION = 0x30, 0x31

cs  = Pin(CS_PIN, Pin.OUT, value=1)
spi = SPI(0, baudrate=1_000_000, polarity=0, phase=0,
          sck=Pin(SCK_PIN), mosi=Pin(MOSI_PIN), miso=Pin(MISO_PIN))

def strobe(cmd):
    cs.value(0); spi.write(bytes([cmd])); cs.value(1)

def read_status(addr):
    cs.value(0)
    spi.write(bytes([addr | READ_BURST]))
    val = spi.read(1)[0]
    cs.value(1)
    return val

def reset():
    cs.value(1); time.sleep_us(5)
    cs.value(0); time.sleep_us(10)
    cs.value(1); time.sleep_us(45)
    strobe(0x30)  # SRES
    time.sleep_ms(10)

print("Resetting CC1101...")
reset()
part = read_status(PARTNUM)
ver  = read_status(VERSION)
print("PARTNUM = 0x%02X  (expect 0x00)" % part)
print("VERSION = 0x%02X  (expect 0x14, sometimes 0x04)" % ver)

if part == 0x00 and ver in (0x14, 0x04, 0x07):
    print(">>> OK: CC1101 detected, SPI working.")
elif ver in (0x00, 0xFF):
    print(">>> FAIL: no response. Check MISO/MOSI/SCK/CS, 3V3 power, GND.")
else:
    print(">>> Unexpected values - partial comms? recheck wiring / lower baudrate.")
