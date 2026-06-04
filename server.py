#!/usr/bin/env python3
"""RAMSES web tool — host server.
Spawns `mpremote run pico_stream.py`, reads the live burst stream, decodes each
burst, and serves a browser UI + JSON feed. Stdlib only.  Run: python3 server.py
Then open http://localhost:8765  (Chrome).  Ctrl-C to stop."""
import http.server, socketserver, json, subprocess, threading, glob, os, re, time, sys

PORT = 8765
HERE = os.path.dirname(os.path.abspath(__file__))
PICO = os.path.join(HERE, "pico_stream.py")

# ---------- decode (evofw3 way: UART framing + Manchester) ----------
MAN_DEC = (0xF,0xF,0xF,0xF,0xF,0x3,0x2,0xF,0xF,0x1,0x0,0xF,0xF,0xF,0xF,0xF)
OPC={0x10E0:'device_info',0x1FC9:'rf_bind',0x0008:'relay_demand',0x30C9:'temperature',
 0x3150:'heat_demand',0x2309:'setpoint',0x3B00:'actuator_sync',0x3EF0:'actuator_state',
 0x1060:'battery',0x313F:'datetime',0x000A:'zone_config',0x1100:'tpi_params',0x2E04:'system_mode',
 0x0004:'zone_name',0x0100:'language',0x10A0:'dhw_params',0x1F09:'sync_cycle',0x12B0:'window',
 0x3120:'state',0x22C9:'ufh_setpoint',0x0001:'rf_check',0x0009:'relay_failsafe',0x1030:'mixvalve',
 0x000C:'zone_devices',0x0404:'schedule',0x0418:'fault_log',0x1290:'out_temp',0x1F41:'dhw_mode'}
DEVT={1:'CTL',2:'UFC',3:'sensor',4:'TRV',7:'DHW',10:'OTB',12:'THM',13:'BDR',17:'OUT',18:'HGI',
 22:'THm',30:'RFG',31:'JspStat',34:'RND',37:'FAN',63:'NUL'}

def measure_bp(runs):
    s = [r for r in runs[:80] if 3 < r < 14]
    return sum(s)/len(s) if s else 8.0
def _levels(sl, runs):                 # expand run-lengths to per-sample level bytes
    L = bytearray(); lvl = sl & 1
    for r in runs:
        L += bytes([lvl]) * r; lvl ^= 1
    return L
def _uart_byte(L, t0, bp):              # sample 8 data bits LSB-first at bit centres from start-bit @t0
    b = 0
    for k in range(8):
        c = int(t0 + (k + 1.5) * bp + 0.5)
        if c >= len(L): return None
        b |= L[c] << k
    return b
def _next_start(L, exp, bp):            # find the next start bit (falling edge) near `exp` — resync
    lo = max(0, int(exp - 0.6 * bp)); hi = min(len(L) - 1, int(exp + 1.7 * bp))
    for i in range(lo, hi):
        if L[i] and not L[i + 1]: return i + 1
    return None
def decode_burst(sl, runs):
    bp = measure_bp(runs)
    for inv in (0, 1):
        L = _levels(sl ^ inv, runs); N = len(L)
        i = 0
        while i < N - 1:
            if L[i] and not L[i + 1]:               # candidate start bit
                s0 = i + 1
                if _uart_byte(L, s0, bp) == 0x33:    # header byte 1
                    s1 = _next_start(L, s0 + 10 * bp, bp)
                    s2 = _next_start(L, s1 + 10 * bp, bp) if s1 else None
                    if s1 and s2 and _uart_byte(L, s1, bp) == 0x55 and _uart_byte(L, s2, bp) == 0x53:
                        msg = bytearray(); mb = 0; c = 0
                        t = _next_start(L, s2 + 10 * bp, bp)
                        while t is not None:
                            fb = _uart_byte(L, t, bp)
                            if fb is None or fb == 0x35: break          # trailer
                            lo = MAN_DEC[fb & 0xF]; hi = MAN_DEC[(fb >> 4) & 0xF]
                            if lo == 0xF or hi == 0xF: break             # bad Manchester
                            mb = ((mb << 4) | ((hi << 2) | lo)) & 0xFF; c ^= 1
                            if not c: msg.append(mb)
                            t = _next_start(L, t + 10 * bp, bp)
                        if len(msg) >= 5: return bytes(msg), round(bp, 2)
            i += 1
    return None

def _did(b): return "%s:%06d"%(DEVT.get(b[0]>>2,"%02d"%(b[0]>>2)),((b[0]&3)<<16)|(b[1]<<8)|b[2])
# evofw3 message.c: header byte -> verb(5:4), addr-config(3:2), param0(b1), param1(b0)
ADDR_PRESENT=[(1,1,1),(0,0,1),(1,0,1),(1,1,0)]
def parse_ramses(msg):
    if len(msg)<6: return None
    h=msg[0]; verb=("RQ","I","W","RP")[(h>>4)&3]
    present=ADDR_PRESENT[(h>>2)&3]; p0=h&0x02; p1=h&0x01
    p=1; addrs=[]
    for k in range(3):
        if present[k] and p+3<=len(msg): addrs.append(_did(msg[p:p+3])); p+=3
        elif not present[k]: addrs.append("--:------")
    params=[]
    if p0 and p<len(msg): params.append(msg[p]); p+=1
    if p1 and p<len(msg): params.append(msg[p]); p+=1
    if p+3>len(msg): return {"verb":verb,"addrs":addrs,"op":None,"opname":"?","payload":""}
    op=(msg[p]<<8)|msg[p+1]; p+=2; ln=msg[p]; p+=1
    pstr=("("+",".join("%d"%x for x in params)+") " if params else "")
    return {"verb":verb,"addrs":addrs,"op":op,"opname":OPC.get(op,"?"),
            "len":ln,"payload":pstr+" ".join("%02X"%x for x in msg[p:p+ln])}

# ---------- shared state ----------
LOCK = threading.Lock()
BURSTS = []          # list of dicts
RAW = []             # parallel list: {"id","sl","nr","raw"} for offline re-decode
STATUS = {"connected": False, "port": None, "msg": "starting"}
CUR_TAG = {"tag": ""}

def add_burst(sl, nr, hexstr):
    runs = [int(hexstr[i:i+2],16) for i in range(0, nr*2, 2)]
    res = decode_burst(sl, runs)
    with LOCK:
        bid = len(BURSTS)
        RAW.append({"id": bid, "sl": sl, "nr": nr, "raw": hexstr, "tag": CUR_TAG["tag"]})
        if res:
            by, bp = res
            chk = (sum(by) & 0xFF) == 0
            pr = parse_ramses(by) or {}
            BURSTS.append({
                "id": bid, "t": time.strftime("%H:%M:%S"), "ok": True,
                "verb": pr.get("verb","?"),
                "addrs": " ".join(pr.get("addrs",[])),
                "op": "%04X"%pr["op"] if pr.get("op") is not None else "----",
                "opname": pr.get("opname","?"),
                "payload": pr.get("payload",""),
                "chk": chk,
                "nbytes": len(by),
                "hex": " ".join("%02X"%x for x in by),
                "tag": CUR_TAG["tag"],
            })
        else:
            BURSTS.append({"id": bid, "t": time.strftime("%H:%M:%S"), "ok": False,
                           "verb":"--","addrs":"(no header lock)","op":"----","opname":"",
                           "payload":"","chk":False,"nbytes":0,"hex":"","tag":CUR_TAG["tag"]})

def reader_thread():
    while True:
        ports = sorted(glob.glob("/dev/cu.usbmodem*"))
        if not ports:
            STATUS.update(connected=False, msg="no Pico found (/dev/cu.usbmodem*)")
            time.sleep(2); continue
        port = ports[0]
        STATUS.update(port=port, msg="connecting…")
        try:
            proc = subprocess.Popen(["mpremote","connect",port,"run",PICO],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    bufsize=1, universal_newlines=True)
        except Exception as e:
            STATUS.update(connected=False, msg="mpremote error: %s"%e); time.sleep(2); continue
        for line in proc.stdout:
            line=line.strip()
            if line.startswith("READY"):
                STATUS.update(connected=True, msg="streaming on "+line.split()[1]+" MHz")
            elif line.startswith("RUNS"):
                parts=line.split(maxsplit=3)
                if len(parts)==4:
                    try: add_burst(int(parts[1]), int(parts[2]), parts[3])
                    except Exception: pass
        STATUS.update(connected=False, msg="stream ended, reconnecting…")
        time.sleep(2)

# ---------- HTTP ----------
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body,str) else body
        self.send_response(code); self.send_header("Content-Type",ctype)
        self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._send(200, open(os.path.join(HERE,"index.html"),"rb").read(), "text/html")
        elif self.path.startswith("/api/raw"):
            m = re.search(r"since=(\d+)", self.path); since=int(m.group(1)) if m else 0
            with LOCK: payload = {"raw": RAW[since:], "total": len(RAW)}
            self._send(200, json.dumps(payload))
        elif self.path.startswith("/api/bursts"):
            m = re.search(r"since=(\d+)", self.path); since=int(m.group(1)) if m else 0
            with LOCK:
                new = BURSTS[since:]
                payload = {"status": STATUS, "bursts": new, "total": len(BURSTS), "tag": CUR_TAG["tag"]}
            self._send(200, json.dumps(payload))
        else:
            self._send(404, "{}")
    def do_POST(self):
        ln=int(self.headers.get("Content-Length",0)); data=self.rfile.read(ln).decode() if ln else ""
        if self.path=="/api/tag":
            try: CUR_TAG["tag"]=json.loads(data).get("tag","")
            except Exception: pass
            self._send(200, json.dumps({"tag":CUR_TAG["tag"]}))
        elif self.path=="/api/clear":
            with LOCK: BURSTS.clear()
            self._send(200, "{}")
        else: self._send(404,"{}")

class ThreadingHTTP(socketserver.ThreadingMixIn, http.server.HTTPServer): daemon_threads=True

if __name__=="__main__":
    threading.Thread(target=reader_thread, daemon=True).start()
    print("RAMSES tool serving at  http://localhost:%d   (Ctrl-C to stop)" % PORT)
    try: ThreadingHTTP(("127.0.0.1",PORT), H).serve_forever()
    except KeyboardInterrupt: print("\nbye")
