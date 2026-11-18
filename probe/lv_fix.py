#!/usr/bin/env python3
# Minimal liveview client: replays the app's opening sequence and keeps the
# session alive with ACKs + periodic requests.
#   ACK   [8:10]=[10:12]=last large packet [4:6]
#         [16:18]=[18:20]=camera status packet [16:18]
#         [24:26]=status packet [24:26], [26:28]=0
#   REQ   [4:6]=our counter (LE, +8), [8:10]=last large packet counter,
#         [10:12]=our counter
# usage: lv_fix.py <pcap> <secs> [ack_ms]
import struct, sys, time, socket, select, threading

sys.setswitchinterval(0.0005)
pcap, secs = sys.argv[1], float(sys.argv[2])
ACKMS = (float(sys.argv[3]) / 1000.0) if len(sys.argv) > 3 else 0.020

data = open(pcap, "rb").read()
link = struct.unpack("<I", data[20:24])[0]
off = 24
up = []
tf = None
while off + 16 <= len(data):
    ts, tu, incl, orig = struct.unpack("<IIII", data[off:off + 16])
    off += 16
    pp = data[off:off + incl]
    off += incl
    ip = pp[16:] if link == 113 else pp[14:]
    if len(ip) < 20 or ip[0] >> 4 != 4 or ip[9] != 17:
        continue
    udp = ip[(ip[0] & 0x0F) * 4:]
    if len(udp) < 8:
        continue
    sp, dp = struct.unpack(">HH", udp[:4])
    sip = ".".join(str(x) for x in ip[12:16])
    tsx = ts + tu / 1e6
    if tf is None:
        tf = tsx
    if dp == 9004 and sip != "192.168.2.1":
        up.append((tsx - tf, sp, udp[8:]))
up.sort()
cport = up[0][1]
pro = [q for t, sp, q in up if t <= 1.4]
req0 = bytearray(next(q for t, sp, q in up if len(q) == 37 and b"\x55\x11\x04\x92" in q))
ack0 = bytearray(next(q for t, sp, q in up if len(q) == 34 and q[6] == 0x04))

U = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
U.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    U.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
except OSError:
    pass
U.bind(("0.0.0.0", cport))
U.setblocking(False)

st = {"big": 0, "ack": 0, "req": 0, "status": 0, "first": None, "last": 0, "dup": 0}
sh = {"big4": 0, "s16": 0, "s24": 0}
ctl = {"cur": 0, "seq": 0}
L = threading.Lock()


def xor8(b, n=7):
    v = 0
    for i in range(n):
        v ^= b[i]
    return v & 0xFF


def sender():
    while True:
        for q in pro:
            try:
                U.sendto(q, ("127.0.0.1", 9004))
            except OSError:
                pass
        t0 = time.time()
        last = 0
        while time.time() - t0 < secs:
            if time.time() - last > 0.083:
                with L:
                    q = bytearray(req0)
                    cur = ctl["cur"]
                    b4 = sh["big4"]
                    q[4:6] = struct.pack("<H", cur)
                    q[6] = 0x05
                    q[7] = xor8(q)
                    q[8:10] = struct.pack("<H", b4)
                    q[10:12] = struct.pack("<H", cur)
                    ctl["seq"] = (ctl["seq"] + 1) & 0xFFFF
                    q[16:18] = struct.pack(">H", ctl["seq"])
                    ctl["cur"] = (cur + 8) & 0xFFFF
                try:
                    U.sendto(bytes(q), ("127.0.0.1", 9004))
                    st["req"] += 1
                except OSError:
                    pass
                last = time.time()
            time.sleep(0.001)


def acker():
    pkt = bytearray(ack0)
    while True:
        with L:
            b4 = sh["big4"]
            s16 = sh["s16"]
            s24 = sh["s24"]
        pkt[4:6] = b"\x00\x00"
        pkt[6] = 0x04
        pkt[7] = 0
        pkt[8:10] = struct.pack("<H", b4)
        pkt[10:12] = struct.pack("<H", b4)
        pkt[16:18] = struct.pack("<H", s16)
        pkt[18:20] = struct.pack("<H", s16)
        pkt[24:26] = struct.pack("<H", s24)
        pkt[26:28] = b"\x00\x00"
        pkt[7] = xor8(pkt)
        try:
            U.sendto(bytes(pkt), ("127.0.0.1", 9004))
            st["ack"] += 1
        except OSError:
            pass
        time.sleep(ACKMS)


threading.Thread(target=sender, daemon=True).start()
threading.Thread(target=acker, daemon=True).start()
t0 = time.time()
mark = 0
while time.time() - t0 < secs:
    r, _, _ = select.select([U], [], [], 0.05)
    if r:
        while True:
            try:
                d = U.recv(2048)
            except (BlockingIOError, OSError):
                break
            if len(d) < 24:
                continue
            if len(d) >= 1000:
                st["big"] += 1
                a = struct.unpack("<H", d[4:6])[0]
                with L:
                    sh["big4"] = a
                    if st["first"] is None:
                        st["first"] = a
                    if a == st["last"]:
                        st["dup"] += 1
                    st["last"] = a
            elif len(d) == 34:
                st["status"] += 1
                if d[6] == 0x01:
                    with L:
                        sh["s16"] = struct.unpack("<H", d[16:18])[0]
                        sh["s24"] = struct.unpack("<H", d[24:26])[0]
    el = time.time() - t0
    if el - mark >= 4:
        print("%4.0fs big=%d (%.0f/s) frames~%.0f ack=%d status=%d big4=%d s16=%d s24=%d dup=%d"
              % (el, st["big"], st["big"] / el, st["big"] / 28.0, st["ack"], st["status"],
                 sh["big4"], sh["s16"], sh["s24"], st["dup"]), flush=True)
        mark = el
print("done big=%d frames~%.1f ack=%d dup=%d" % (st["big"], st["big"] / 28.0, st["ack"], st["dup"]), flush=True)
