# pico-ramses

**Decode Honeywell / evohome RAMSES-II 868 MHz traffic with a Raspberry Pi Pico + a ~€1 CC1101 — no nanoCUL, no ESP32.**

A from-scratch RAMSES-II receiver (and work-in-progress transmitter) for the cheapest hardware that works.
If you have a Pico and a CC1101 in a drawer, you can sniff your Honeywell T-series / evohome heating system
without buying a "supported" radio stick.

> **The one gotcha that wastes everyone's week:** RAMSES puts its bytes on the air as **UART characters**
> — start bit + 8 data bits **LSB-first** + stop bit. Slice the raw demodulated bits into bytes without
> honouring that framing and every byte drifts 2 bits and the message looks like *encrypted noise*. It isn't.
> Honour the framing and it's clean plain-text. See [`uart_decode.py`](uart_decode.py).

Full write-up: **[Reverse-engineering a Honeywell T4R with a €4 Pico + CC1101](https://noemarc.com/?p=190)**.

## Hardware

- Raspberry Pi Pico (or Pico W)
- CC1101 **868 MHz** module (⚠ not 433 — they look identical; RAMSES is 868)
- 7 jumper wires

### Wiring (SPI0)

| CC1101 | Pico | role |
|--------|------|------|
| VCC | `3V3(OUT)` pin 36 | power — **3.3 V only**, *not* `3V3_EN` |
| GND | GND | ground |
| SCLK | GP2 | SPI clock |
| MOSI (SI) | GP3 | SPI out |
| MISO (SO) | GP4 | SPI in |
| CSN | GP5 | chip select |
| GDO0 | GP6 | demodulated serial data out |

## Quick start

1. Flash **MicroPython** onto the Pico.
2. Sanity check the radio: `mpremote run cc1101_test.py` → should print `PARTNUM 0x00`, `VERSION 0x14`.
3. Start the live sniffer (host needs only Python 3 stdlib + [`mpremote`](https://pypi.org/project/mpremote/)):
   ```bash
   ./start.sh           # then open http://localhost:8765 in a browser
   ```
   `pico_stream.py` runs on the Pico and streams every 868.3 MHz burst over USB; `server.py` decodes them
   and shows a live feed of verb / source→dest devices / opcode / payload / checksum.

### Use it as an evofw3 gateway (Home Assistant / ramses_rf)

`evofw3_pico.py` turns the Pico into a USB-serial gateway that speaks the **exact evofw3 line protocol**, so
it's a drop-in for [`ramses_rf`](https://github.com/zxdavb/ramses_rf) and the Home Assistant *evohome (RAMSES
RF)* integration — no host-side decoder needed, all decode/encode runs on the Pico.

```bash
mpremote run evofw3_pico.py                 # try it
mpremote cp evofw3_pico.py :main.py         # or install it to auto-start on power-up
```

Each received frame is printed as one evofw3 line — `<rssi> <verb> <seq> <addr0> <addr1> <addr2> <opcode> <len> <payload>`:

```
028 RQ --- 31:193108 08:171840 --:------ 3EF1 012 002A0095CAD0D4ECC72E4C55
```

To transmit, send a line in the same shape (minus the rssi field) over the serial port; the gateway builds the
RAMSES checksum, Manchester-encodes, UART-frames and keys the radio:

```
 I --- 18:000730 --:------ 18:000730 1FC9 018 0010E07FFFFF...
```

## What's here

| file | what it does |
|------|--------------|
| `evofw3_pico.py` | **Pico firmware — evofw3-compatible USB-serial gateway** (RX decode + TX encode on-Pico, drop-in for ramses_rf / Home Assistant) |
| `test_evofw3_pico.py` | offline tests for the gateway's pure logic (build/encode/decode/format round-trips) — `python3 test_evofw3_pico.py` |
| `pico_stream.py` | **Pico firmware** — async-mode + PIO continuous burst capture, streams run-lengths over USB |
| `server.py` | **Host** — decodes the UART-framed Manchester stream into RAMSES-II messages, serves the web UI + JSON |
| `index.html` | live browser feed |
| `start.sh` | launch the server |
| `uart_decode.py` | **standalone reference decoder** — the whole receive path in plain Python, well commented |
| `cc1101_test.py` | SPI connectivity check |
| `tx_encode.py` | **transmit encoder** — build a RAMSES message → Manchester → UART-frame, with a round-trip self-test |
| `cc1101_tx_test.py` | ⚠ **experimental** on-air transmit (set your device ID first; transmitting at heating gear is on you) |

## Status

- ✅ **Receive** — works, fully verified (e.g. `10E0` device-info decodes with valid checksum + the model name in ASCII).
- ✅ **evofw3 gateway** (`evofw3_pico.py`) — live RX verified emitting correct evofw3 lines; drop-in for ramses_rf / Home Assistant. Offline logic tests pass (`test_evofw3_pico.py`).
- 🟡 **Transmit** — the encoder round-trips and the radio keys; on-air confirmation is pending (a receive-only relay can't ack, so a 2nd CC1101 as witness is the clean way to verify).

## How it works (the short version)

1. CC1101 in **async serial mode** dumps the demodulated bitstream on GDO0.
2. The RP2040 **PIO** samples it at line rate (the CPU/MicroPython is far too slow to bit-bang 26 µs bits).
3. The stream is **UART-framed** (start/stop, LSB-first) — decode the frames to get the `man_encode` bytes.
4. **Manchester-decode** pairs of bytes → RAMSES-II message: `header · addresses · opcode · length · payload · checksum`.

## Credits

Stands entirely on the protocol groundwork of [`evofw3`](https://github.com/ghoti57/evofw3) and
[`ramses_rf`](https://github.com/zxdavb/ramses_rf). This project's contribution is doing it on an RP2040/Pico.

MIT licensed.
