#!/usr/bin/env python3
# lv_relay.py —— 相机端预览中继（proven 流控 + 双鱼眼组帧 + TCP 视频/控制服务）
# 流控沿用 lv_pipe6（手机握手订阅 + 52ms 周期请求 + ACKN=4 大端计数 ACK）
# 组帧沿用 pc_recv7（k=d[17]>>7 左右鱼眼，i=d[18] 帧内包序，缺包整帧丢弃）
# 参数集 SPS0(2048x1024)+CABAC PPS 68ee06f2c0（与手机预览逐字节一致）
# 运行：python3 lv_relay.py /dev/shm/mimo.pcap [ACKN]
import struct, sys, time, socket, select, threading, queue, collections, os

pcap = sys.argv[1] if len(sys.argv) > 1 else "/dev/shm/mimo.pcap"
ACKN = int(sys.argv[2]) if len(sys.argv) > 2 else 4
VIDEO_PORT, CTRL_PORT = 5010, 5011
SP = bytes.fromhex("0000000167640028acb40100040d3501010106d0a135")
PP = bytes.fromhex("0000000168ee06f2c0")
PARAM = SP + PP

data = open(pcap, "rb").read(); link = struct.unpack("<I", data[20:24])[0]
off = 24; up = []; tf = None
while off + 16 <= len(data):
    ts, tu, incl, orig = struct.unpack("<IIII", data[off:off + 16]); off += 16
    p = data[off:off + incl]; off += incl
    ip = p[16:] if link == 113 else p[14:]
    if len(ip) < 20 or ip[0] >> 4 != 4 or ip[9] != 17: continue
    udp = ip[(ip[0] & 0x0F) * 4:]
    if len(udp) < 8: continue
    sp, dp = struct.unpack(">HH", udp[:4])
    sip = ".".join(str(x) for x in ip[12:16])
    tsx = ts + tu / 1e6
    if tf is None: tf = tsx
    if dp == 9004 and not sip.startswith("192.168.2.1"):
        up.append((tsx - tf, sp, udp[8:]))
up.sort(); cport = up[0][1]
pro = [(t, sp, p) for t, sp, p in up if t <= 2.7]
req = next(p for t, sp, p in up if len(p) == 37 and b"\x55\x11\x04\x92" in p)
ack0 = next(p for t, sp, p in up if len(p) == 34 and p[6] == 0x04)

U = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
U.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try: U.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 * 1024 * 1024)
except OSError: pass
U.bind(("0.0.0.0", cport)); U.setblocking(False)

clients = []; cl_lock = threading.Lock()
st = {"big": 0, "ack": 0, "req": 0, "ok": 0, "drop": 0, "gap": 0}
seqv = [0]; lk = threading.Lock()
cq = queue.Queue()
state = {"ackn": ACKN, "reqms": 0.052, "kf": False, "rehand": True, "idr": 0}


def put(p):
    try: U.sendto(p, ("127.0.0.1", 9004))
    except OSError: pass


def sendp(pkt):
    q = bytearray(pkt)
    if len(q) >= 18:
        with lk:
            seqv[0] = (seqv[0] + 1) & 0xFFFF
            q[16:18] = struct.pack(">H", seqv[0])
    put(bytes(q))


def broadcast(msg):
    with cl_lock:
        dead = []
        for c in clients:
            try: c.sendall(msg)
            except OSError: dead.append(c)
        for c in dead: clients.remove(c)


def video_server():
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", VIDEO_PORT)); s.listen(4)
    while True:
        c, _ = s.accept()
        try: c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError: pass
        with cl_lock: clients.append(c)


def ctrl_server():
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", CTRL_PORT)); s.listen(2)
    while True:
        c, _ = s.accept()
        threading.Thread(target=ctrl_handle, args=(c,), daemon=True).start()


def ctrl_handle(c):
    buf = b""
    while True:
        try: d = c.recv(1024)
        except OSError: return
        if not d: return
        buf += d
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            handle_cmd(line.decode("utf-8", "ignore").strip(), c)


def handle_cmd(cmd, c):
    parts = cmd.split()
    if not parts: return
    op = parts[0].lower()
    if op == "help":
        reply = ("命令: stat 状态 | kick 重启camera3逼IDR | rehand 重发握手 | "
                 "ackn <n> ACK批量 | reqms <s> 请求间隔 | kf 请求关键帧")
    elif op == "stat":
        reply = "big=%d ok=%d drop=%d gap=%d idr=%d ack=%d req=%d clients=%d ackn=%d" % (
            st["big"], st["ok"], st["drop"], st["gap"], state["idr"], st["ack"], st["req"], len(clients), state["ackn"])
    elif op == "kick":
        threading.Thread(target=lambda: (os.system("systemctl restart dji_camera3"),
                                         time.sleep(3), state.__setitem__("rehand", True)), daemon=True).start()
        reply = "正在重启camera3并重握手（逼IDR）"
    elif op == "rehand":
        state["rehand"] = True; reply = "已触发重发握手"
    elif op == "ackn" and len(parts) > 1:
        state["ackn"] = max(1, int(parts[1])); reply = "ackn=%d" % state["ackn"]
    elif op == "reqms" and len(parts) > 1:
        state["reqms"] = float(parts[1]); reply = "reqms=%.4f" % state["reqms"]
    elif op == "kf":
        try:
            from gen38 import build38
            put(build38(seqv[0] & 0xFF)); reply = "已发关键帧请求"
        except Exception as e:
            reply = "kf失败:%s" % e
    else:
        reply = "未知命令，输入 help"
    try: c.sendall((reply + "\n").encode())
    except OSError: pass


def sender():
    # 周期性完整重放握手（仿 lv_fix）：持续重订阅以捕获编码器重建时的 IDR
    while True:
        t0 = time.time(); i = 0; last = 0
        while True:
            if state["rehand"]:
                i = 0; t0 = time.time(); last = 0; state["rehand"] = False
            el = time.time() - t0
            while i < len(pro) and pro[i][0] <= el:
                put(pro[i][2]); i += 1
            if el > 2.7 and el - last > state["reqms"]:
                sendp(req); st["req"] += 1; last = el
            if el > 8.0:
                break
            time.sleep(0.001)


def acker():
    buf = []
    while True:
        n = state["ackn"]
        try:
            buf.append(cq.get_nowait())
            while len(buf) < n:
                buf.append(cq.get_nowait())
        except queue.Empty:
            pass
        if len(buf) >= n:
            q = bytearray(ack0)
            q[8:10] = struct.pack(">H", buf[0])
            q[10:12] = struct.pack(">H", buf[n - 1])
            put(bytes(q)); st["ack"] += 1
            buf = []
        else:
            time.sleep(0.0003)


ch = {0: {"cur": b"", "have": False, "bad": False, "prev": None},
      1: {"cur": b"", "have": False, "bad": False, "prev": None}}


def flush(k):
    c = ch[k]
    if not c["have"]: return
    if c["bad"]:
        st["drop"] += 1
    else:
        body = c["cur"]
        if b"\x00\x00\x00\x01\x65" in body[:64] or b"\x00\x00\x00\x01\x67" in body[:64]:
            state["idr"] += 1
        nal = PARAM + body
        broadcast(b"\x01" + bytes([k]) + struct.pack("<I", len(nal)) + nal)
        st["ok"] += 1
    c["cur"] = b""; c["have"] = False; c["bad"] = False; c["prev"] = None


def receiver():
    while True:
        r, _, _ = select.select([U], [], [], 0.001)
        if not r: continue
        while True:
            try: d = U.recv(4096)
            except (BlockingIOError, OSError): break
            if len(d) < 24: continue
            if len(d) >= 1000: st["big"] += 1
            try: cq.put_nowait(struct.unpack(">H", d[4:6])[0])
            except queue.Full: pass
            if len(d) < 1000: continue
            k = d[17] >> 7; i = d[18]
            c = ch[k]
            if i == 0:
                flush(k)
                c["cur"] = d[19:]; c["have"] = True; c["prev"] = 0
            else:
                if not c["have"]: continue
                if c["prev"] is not None and i != (c["prev"] + 1) & 0xFF:
                    st["gap"] += 1; c["bad"] = True
                c["cur"] += d[19:]; c["prev"] = i


for fn in (video_server, ctrl_server, sender, acker, receiver):
    threading.Thread(target=fn, daemon=True).start()
print("中继就绪 视频%d 控制%d 握手%d条 cport=%d ACKN=%d" % (
    VIDEO_PORT, CTRL_PORT, len(pro), cport, ACKN), flush=True)
mark = 0; t_start = time.time()
while True:
    time.sleep(1); el = time.time() - t_start
    if el - mark >= 5:
        print("%5.0fs big=%d ok=%d drop=%d gap=%d clients=%d" % (
            el, st["big"], st["ok"], st["drop"], st["gap"], len(clients)), flush=True)
        mark = el
