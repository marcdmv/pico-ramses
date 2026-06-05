# pico-ramses

**Listen to (and talk to) your Honeywell / evohome heating system over radio, using about €8 of hardware
you can buy anywhere.** No special equipment, no soldering, no prior electronics experience needed.

If that sentence had words you didn't recognise, you're exactly who this README is written for. Read on — it
assumes you know nothing about radios, microcontrollers, or heating protocols, and explains each step.

---

## What is this, in plain English?

Honeywell's wireless thermostats and boiler relays (the evohome / "T-series" family, sold under several
brands) talk to each other over **radio** at 868 MHz — a license-free band, a bit like a very simple walkie-talkie.
The messages they send ("turn the heat on", "the room is 19.5 °C") are unencrypted, so with a cheap radio
receiver you can **read them** — and, with care, **send your own** to control the boiler.

People normally do this by buying a "supported" USB radio stick. This project does the same job with a
**Raspberry Pi Pico** (a €4 hobbyist microcontroller board) plus a **CC1101** (a €1–4 radio chip on a little
board), wired together with jumper wires. That's it.

By the end you'll be able to:

- **watch** your heating system's radio messages scroll past in a web page, and/or
- plug it into **Home Assistant** (popular free home-automation software) as a heating gateway.

---

## Jargon, defined once

You'll meet these words below. Skim them now; refer back as needed.

| Word | What it means here |
|------|--------------------|
| **Raspberry Pi Pico** | A tiny, cheap programmable board (the "computer" you'll run code on). *Not* the bigger Raspberry Pi. A "Pico W" is the same thing with Wi-Fi — the Wi-Fi is unused here, either works. |
| **CC1101** | A small circuit board with a radio chip on it. It does the actual listening/transmitting at 868 MHz. |
| **MicroPython** | A version of the Python programming language that runs *on* the Pico. You install it once. |
| **Flashing** | Copying the MicroPython software onto the Pico (a one-time drag-and-drop). |
| **`mpremote`** | A small program you run on your normal computer to send code to the Pico and see its output. |
| **GPIO pin** | One of the little metal pins on the edge of the Pico that you connect wires to. |
| **SPI** | The "language" the Pico and CC1101 use to talk to each other over those wires. You don't need to understand it — just wire the right pins together. |
| **RAMSES-II** | The name of the radio message format Honeywell uses. |
| **`ramses_rf` / Home Assistant evohome** | Free software that understands RAMSES-II messages. This project can feed them. |

---

## What you need to buy

- **A Raspberry Pi Pico** (or Pico W) — about €4–7.
- **A CC1101 radio module — the 868 MHz version.**
  > ⚠️ **This is the one mistake to avoid:** CC1101 boards come in 433 MHz and 868 MHz versions that look
  > *identical*. Honeywell heating is **868 MHz**. Buy 868. Check the listing carefully.
- **7 jumper wires** ("female-to-female" if both your boards have pins sticking up; a cheap assorted pack is fine).
- *Optional:* a soldering iron, **only if** your boards came with loose pin headers that aren't attached yet. Many come pre-soldered.

Total: roughly **€8 / $9**, and the Pico is reusable for other projects afterwards.

---

## Wiring it up

You connect 7 wires between the CC1101 and the Pico. Each Pico pin has a number printed in the official
[Pico pinout diagram](https://datasheets.raspberrypi.com/pico/Pico-R3-A4-Pinout.pdf) — keep that open.
The "GP" numbers below are the labels in that diagram.

| CC1101 pin | connects to Pico pin | why |
|------------|----------------------|-----|
| **VCC** | **`3V3(OUT)`** — physical pin 36 | power. ⚠️ Use the *3.3 V output* pin, **not** the one labelled `3V3_EN`. The CC1101 is 3.3 V only — do not give it 5 V. |
| **GND** | any **GND** pin | the shared "0 V" reference |
| **SCLK** | **GP2** | SPI data wire (just match it up) |
| **MOSI** (sometimes labelled **SI**) | **GP3** | SPI data wire |
| **MISO** (sometimes labelled **SO**) | **GP4** | SPI data wire |
| **CSN** | **GP5** | SPI "chip select" wire |
| **GDO0** | **GP6** | the radio's decoded-data output |

Double-check VCC and GND before powering on — swapping them can damage the radio.

---

## Step by step, from a clean slate

### 1. Put MicroPython on the Pico (one time)

1. Download the MicroPython `.uf2` file for the Pico from the official site:
   <https://micropython.org/download/RPI_PICO/> (or `RPI_PICO_W` if you bought the Wi-Fi version).
2. **Unplug** the Pico. Hold down the small **BOOTSEL** button on the Pico while you plug its USB cable into
   your computer, then let go. The Pico appears as a **USB drive** called `RPI-RP2`.
3. **Drag the `.uf2` file onto that drive.** The drive disappears and the Pico reboots — MicroPython is now installed.

### 2. Install `mpremote` on your computer (one time)

`mpremote` is how your computer talks to the Pico. You need [Python 3](https://www.python.org/downloads/)
installed, then in a terminal:

```bash
pip install mpremote
```

### 3. Wire the CC1101 to the Pico

Follow the table above. Then plug the Pico into USB.

### 4. Check the radio is alive

In a terminal, in this project's folder:

```bash
mpremote run cc1101_test.py
```

You should see something like `PARTNUM 0x00` and `VERSION 0x14`. If you do, the Pico and radio are talking —
the hard part is over. If not, re-check your wiring (it's almost always a wrong or loose wire).

### 5. Now pick what you want to do

#### A) Just watch the heating messages (live web page)

```bash
./start.sh
```

Then open **<http://localhost:8765>** in your browser. You'll see a live feed of radio messages — who sent
them, to whom, and what they say (temperature, heat demand, etc.). Press `Ctrl-C` in the terminal to stop.

*(This needs only Python 3 — no extra installs beyond `mpremote`.)*

#### B) Use it with Home Assistant (as a heating gateway)

`evofw3_pico.py` makes the Pico pretend to be a commercial "evofw3" radio stick, so Home Assistant's
**evohome (RAMSES RF)** integration — and the [`ramses_rf`](https://github.com/zxdavb/ramses_rf) library —
treat it as a supported device.

Install it onto the Pico so it starts automatically whenever the Pico has power:

```bash
mpremote cp evofw3_pico.py :main.py
```

Now whenever the Pico is plugged in, it streams decoded messages over USB. Point Home Assistant /
`ramses_rf` at the Pico's serial port (it looks like `/dev/ttyACM0` on Linux, `/dev/cu.usbmodem...` on macOS,
or a `COM` port on Windows).

To just try it without installing, run it directly:

```bash
mpremote run evofw3_pico.py
```

Each received message prints as one line, e.g.:

```
028 RQ --- 31:193108 08:171840 --:------ 3EF1 012 002A0095CAD0D4ECC72E4C55
```

Reading left to right: signal strength · message type · (sequence) · sender · receiver · (unused) · message
code · length · data.

To **send** a message (advanced — this can command your boiler, so be careful), type a line in the same shape
without the signal-strength number and press Enter; the gateway handles all the encoding for you.

---

## What each file is

| file | what it does |
|------|--------------|
| `cc1101_test.py` | quick "is the radio wired up correctly?" check |
| `start.sh` + `server.py` + `pico_stream.py` + `index.html` | the live web sniffer (option A) |
| `evofw3_pico.py` | **the Home Assistant gateway** (option B) — does everything on the Pico itself |
| `test_evofw3_pico.py` | automated tests for the gateway's logic; run with `python3 test_evofw3_pico.py` (no Pico needed) |
| `uart_decode.py` | the decoder written out plainly in one file, heavily commented — read this to understand how it works |
| `tx_encode.py` | builds an outgoing message and proves the encoder is correct, with no radio involved |
| `cc1101_tx_test.py` | ⚠️ experimental: actually transmits. Set your own device ID first; transmitting at heating gear is at your own risk |

---

## How it actually works (optional reading)

You don't need this to use the project, but if you're curious:

1. The CC1101 listens on 868 MHz and spits out the raw decoded radio "bits" on one wire (GDO0).
2. The Pico's special hardware (called **PIO**) samples that wire fast enough to catch every bit — the main
   processor is too slow to do it reliably.
3. Here's **the trap that costs everyone a week:** those bits aren't the message directly. Honeywell sends each
   byte wrapped as a **UART character** — a "start" marker, 8 data bits sent *least-significant-bit first*, and a
   "stop" marker. If you slice the bits into bytes *without* peeling off that wrapper, every byte comes out
   shifted by 2 bits and the whole message looks like meaningless **encrypted noise**. It isn't encrypted at
   all. Peel off the UART wrapper and it's clean, readable text. See [`uart_decode.py`](uart_decode.py).
4. After un-wrapping, each pair of bytes is **Manchester-decoded** (a simple 4-bits-to-8-bits scheme) into one
   real message byte, giving you: `sender · receiver · message-code · length · data · checksum`.

---

## Status

- ✅ **Receiving** — works and is verified on real hardware (Honeywell `10E0` "device info" messages decode
  cleanly, checksum and all, even spelling out the device's model name in plain text).
- ✅ **Home Assistant gateway** (`evofw3_pico.py`) — verified live producing correct evofw3 lines; drop-in for
  `ramses_rf` / Home Assistant. Logic covered by automated tests.
- 🟡 **Transmitting** — the encoder is proven correct and the radio keys up, but I haven't yet *confirmed* the
  signal goes out over the air (the boiler relay only listens, it never replies, so it can't confirm receipt).
  The clean way to verify is a second cheap CC1101 acting as a witness — that's the next step.

---

## Credits

This project stands entirely on the protocol groundwork of [`evofw3`](https://github.com/ghoti57/evofw3) and
[`ramses_rf`](https://github.com/zxdavb/ramses_rf). Its only new contribution is doing it on a Raspberry Pi
Pico. Full write-up: **[Reverse-engineering a Honeywell T4R with a €4 Pico + CC1101](https://noemarc.com/?p=190)**.

MIT licensed.
