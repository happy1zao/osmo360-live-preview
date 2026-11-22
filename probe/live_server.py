#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""live_server.py —— 相机端低延迟实时视频/音频服务（脱手机，常驻）

架构（全部在相机本机，PC 只需 adb forward 5010/5011/5012）：
  1) 会话维持：按原时序完整重放 mimo_cold_handshake.pcap 上行包
     （bootstrap 前 2s = APP 注册/话题订阅；之后 steady = ACK/请求/保活，循环）
  2) 起服务 9 秒后 restart dji_camera3（连接中重启，逼出 IDR），之后周期 force_I
  3) UDP 收相机 9004 下行，按 **pair 拼法**实时组帧
     (base=ch&0x7f, fid, (frag&0x1f, rank=ch>>7)；frag0 剥 17B，其余剥 1B)
     每个 (base,fid) 给 WINDOW 秒收集窗口，片齐才输出（缺片丢弃）
  4) TCP 5010：\x01\x00 + <I 长度 + H264 帧（兼容 preview.py）
     TCP 5011：控制（stat / kick / force / quit）
     TCP 5012：4ch/48k/s24-32le 实时 PCM（20ms 块，只录不播）

★所有计时必须用 time.monotonic()：相机会被 kick/dji_cst 回拨系统时钟十余分钟，
  用 time.time() 会让帧窗口永远不超时（实测踩过）。
用法: setsid python3 -u /data/local/tmp/live_server.py /dev/shm/ae2d.pcap

第十轮（2026-11-22）新增：LIVE_RESTAMP / LIVE_CYCLE_FROM=full / ACK_MODE=clk。
"""
import os, sys, time, socket, struct, threading, subprocess, collections

PCAP = sys.argv[1] if len(sys.argv) > 1 else "/dev/shm/ae2d.pcap"
BOOT_SEC = 2.0
# ★实测：kick camera3 会让相机把本机会话踢掉（big 停止增长）。
# 完整注册本身就会让相机新建编码器实例并吐 IDR（expG），无需 kick。
# ★复刻 expI/expJ 的成功时序：完整单次重放 + 在位期间 kick。
# 曾试过「bootstrap + steady 循环」，kick 后相机会停流；原生 63.5s 时序才稳。
KICK_DELAY = float(os.environ.get("LIVE_KICK", "0"))      # 0 = 禁用（有 IDR 种子，kick 反而踢掉会话）
# ★IDR 策略（2026-11-22 最终定案：默认**不 force**）
#   逐秒采样证明：force_I（dji_cst + LD_PRELOAD）一调用，开流约 7 秒后视频就彻底停推
#   （big 卡死不再增长，同时冒出一个 63 片/91KB 的"另一路 IDR"），之后再 force 也救不回来。
#   关掉 force 后同一流程能连续稳跑 30 秒以上、~18-20fps。所以默认彻底不碰它。
FORCE_PERIOD = float(os.environ.get("LIVE_FORCE", "0"))     # 0 = 禁用（实测 force 会杀流）
FORCE_MIN_GAP = float(os.environ.get("LIVE_FORCE_GAP", "3.0"))
REPAIR = os.environ.get("LIVE_REPAIR", "0") == "1"          # 默认关：丢帧自愈也不再 force
WINDOW = float(os.environ.get("LIVE_WINDOW", "0.30"))   # 收口宽限（片齐的帧不等它，见 try_emit）
# ★拼法（2026-11-22 第七轮定案 = split）
ASM = os.environ.get("LIVE_ASM", "pair")   # pair=唯一验证过的交错拼法；split/union/rank0=降级对照
# ★是否在"下一帧第一个包到达"时就 flush 上一帧。默认关：
#   相机的片是流水线发的，上一帧的尾部片会晚于下一帧首片到达，
#   一有下一帧就 flush 会把每帧切成半截（=竖直糊条）。
FLUSH_FID = os.environ.get("LIVE_FLUSH_FID", "0") == "1"
# ★是否只认一个 base（客户端传 0）。
LOCK_BASE = os.environ.get("LIVE_LOCK_BASE", "1") == "1"
# 合并收割：多路并存时允许"从带 IDR 的帧接管另一路"（安全分时复用，不破坏参考链）
MERGE = os.environ.get("LIVE_MERGE", "1") == "1"
# ★★第八轮：ACCEPT_ALL=1（默认）—— 不再按 base 过滤 P 帧。
ACCEPT_ALL = os.environ.get("LIVE_ACCEPT_ALL", "1") == "1"
# ★现场取证：LIVE_DUMP_SEC=2 时，服务起来 5 秒后把 2 秒内的所有 9004 下行包
#   原样落盘到 /dev/shm/rt.bin（记录 = u32 长度 + 原始 payload）。
DUMP_SEC = float(os.environ.get("LIVE_DUMP_SEC", "0"))
DUMP_PATH = "/dev/shm/rt.bin"
DUMP = {"left": 0, "f": None}


def dump_worker():
    """LIVE_DUMP_SEC>0 时：服务起来 5 秒后，连续 DUMP_SEC 秒把每个 9004 下行包原样落盘。
    旧版只读了 env 却从没触发（rt.bin 一直不存在），这里补成真正的一段定时窗口。"""
    if DUMP_SEC <= 0:
        return
    time.sleep(5)
    DUMP["left"] = 1 << 30          # 先置大数，receiver 见到就开写
    time.sleep(DUMP_SEC)
    DUMP["left"] = 0
    try:
        if DUMP["f"] is not None:
            DUMP["f"].close()
            DUMP["f"] = None
        log("取证落盘完成 %s" % DUMP_PATH)
    except OSError:
        pass


CUR_FID = {}       # base -> 当前正在收集的 fid（fid 变化即 flush 上一帧）
EXPECTED = {}      # base -> 该流一帧的典型片数（自适应学习）
EXPECTED_R = {}    # base -> {rank: 该 rank 的典型片数}（第八轮：分 rank 学习，齐了就发）
BASE_OK = {}       # base -> 已成功推出的帧数（诊断：看清到底哪个 base 在供帧）
BASE_DROP = {}     # base -> 被丢的帧数
TRUNC_FAIL = {}    # base -> 连续"帧尾截断"计数（超阈值就重新学习片数）
AUDIO_DEV = "regular0.audio-processing"
VIDEO_PORT, CTRL_PORT, AUDIO_PORT = 5010, 5011, 5012
DJI_HEAD = b"\x00\x00\x00\x01\xff"
NAL_START = b"\x00\x00\x00\x01"
NAL_TYPES = (1, 5, 6, 7, 8)


def log(msg):
    print("[live] %s %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def load_up(path):
    """解析上行重放包。支持两种格式：
      1) 原始 pcap（58MB，历史用法）
      2) 本工具压出的紧凑格式 UP9K：`b"UP9K" + u32 n + n×[f64 t, u16 sport, u16 len, payload]`
         只有 258KB（5146 个 9004 上行包），推一次不到 1 秒，也不再吃 tmpfs 的 58MB 内存。
    """
    d = open(path, "rb").read()
    if d[:4] == b"UP9K":
        n = struct.unpack("<I", d[4:8])[0]
        off, up = 8, []
        for _ in range(n):
            t, sp, ln = struct.unpack("<dHH", d[off:off + 12]); off += 12
            up.append((t, sp, d[off:off + ln])); off += ln
        up.sort()
        t0 = up[0][0]
        return [(t - t0, q) for t, sp, q in up], up[0][1]
    link = struct.unpack("<I", d[20:24])[0]
    off, up = 24, []
    while off + 16 <= len(d):
        ts, tu, il, og = struct.unpack("<IIII", d[off:off + 16]); off += 16
        p = d[off:off + il]; off += il
        ip = p[16:] if link == 113 else (p[14:] if link == 1 else None)
        if not ip or len(ip) < 20 or ip[0] >> 4 != 4 or ip[9] != 17:
            continue
        u = ip[(ip[0] & 0xF) * 4:]
        if len(u) < 8:
            continue
        sp, dp = struct.unpack(">HH", u[:4])
        if dp == 9004:
            up.append((ts + tu / 1e6, sp, u[8:]))
    up.sort()
    t0 = up[0][0]
    return [(t - t0, q) for t, sp, q in up], up[0][1]


ALL_UP_RAW, CPORT = load_up(PCAP)
# ★2026-11-22 第六轮假设：相机很可能按 (源IP,源端口) 认定"客户端会话"。
CPORT = int(os.environ.get("LIVE_CPORT", str(CPORT)))
# ★实测坑：完整 63.5s 重放里 3s 之后有 314 条 DUML 用户操作长命令
#   （6.16s 一批模式配置），会把相机切进「app 正在回放」态，预览编码器
#   随即停流 → PC 窗口只出 2 帧后卡死。
CUT_SEC = float(os.environ.get("LIVE_CUT", "3.0"))
# ★★关键：CUT=3.0 会把"每秒重发一次的话题订阅包"一起砍掉，
#   而相机是靠这些订阅判断 app 还在不在的。砍掉后实测 60~62 秒流就停。
#   所以：长度 >50 但属于"周期订阅/状态推送"的（65/67/79/97B）必须保留。
#   ★第十轮结论：正确的做法是 CUT=0（完全不裁剪），裁剪反而破坏了冷启动顺序。
KEEP_LONG = {65, 67, 79, 97}
LOOP_TO_SEC = float(os.environ.get("LIVE_LOOP_TO", "55.0"))
ALL_UP = [(t, q) for t, q in ALL_UP_RAW
          if t <= CUT_SEC or len(q) in KEEP_LONG or (len(q) <= 50 and t <= LOOP_TO_SEC)]

# ★★2026-11-22 第四次归因：冷启动后"一点视频都没有"，是因为相机不在 **app 预览模式**。
APPMODE = os.environ.get("LIVE_APPMODE", "1") == "1"
MODE_PKT = None
BURST = []
for t, q in ALL_UP_RAW:
    i = q.find(b"\x55")
    if MODE_PKT is None and i >= 0 and i + 3 < len(q) and q[i + 3] == 0x1D and len(q) >= 200:
        MODE_PKT = q
for t, q in ALL_UP_RAW:
    if 6.10 <= t <= 6.25 and len(q) >= 59:
        BURST.append(q)
if APPMODE and MODE_PKT:
    log("app 模式包就绪 %dB，订阅批 %d 条" % (len(MODE_PKT), len(BURST)))
elif APPMODE:
    log("⚠ 抓包里没找到 cmdset=0x1d 模式包，app 模式补发关闭")
    APPMODE = False
log("上行过滤: %d -> %d (剔除 %d 条 %0.1fs 后的长命令, CUT=%.1fs)" %
    (len(ALL_UP_RAW), len(ALL_UP), len(ALL_UP_RAW) - len(ALL_UP), CUT_SEC, CUT_SEC))
BOOT = [(t, q) for t, q in ALL_UP if t <= BOOT_SEC]
STEADY = [(t - BOOT_SEC, q) for t, q in ALL_UP if t > BOOT_SEC]
STEADY_SPAN = STEADY[-1][0] if STEADY else 1.0
log("上行包 %d (bootstrap %d / steady %d, cport=%d, span=%.1fs)" %
    (len(ALL_UP), len(BOOT), len(STEADY), CPORT, STEADY_SPAN))

U = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
U.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    U.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    # ★必须同时把内核 net.core.rmem_max 抬上去（默认只有 256KB，会被静默截断）：
    #   sysctl -w net.core.rmem_max=8388608  否则 25fps/48Mbps 的突发直接丢包
except OSError:
    pass
U.bind(("0.0.0.0", CPORT))
U.setblocking(False)

clients = {"v": [], "a": []}
lock = threading.Lock()
T_START = time.monotonic()
st = {"pkts": 0, "big": 0, "frames": 0, "idr": 0, "dropped": 0, "incomplete": 0,
      "audio_bytes": 0, "key_base": None, "main_base": None,
      "r_no0": 0, "r_gap": 0, "r_pair": 0, "r_nomain": 0, "r_nal": 0, "r_trunc": 0}
IDR_CACHE = {"buf": b""}
IDR_LIVE = {"ok": False}      # True = 缓存里的 IDR 来自本次会话（参数与当前编码器一致）
NEED_REPAIR = {"v": False}    # 刚丢过帧 → 需要一次 fresh IDR 让解码器重同步
LAST_REPAIR = {"t": 0.0}
LAST_FORCE = {"t": 0.0}
SEED = "/dev/shm/idr_seed.h264"
if os.environ.get("LIVE_SEED", "0") == "1" and os.path.exists(SEED):
    IDR_CACHE["buf"] = open(SEED, "rb").read()
    st["key_base"] = 0x25
    st["idr"] = 1
    log("载入 IDR 种子 %dB（无需 kick）" % len(IDR_CACHE["buf"]))
FRAMES = {}                       # (base, fid) -> {"t0": monotonic, "parts": {(frag,rank): body}}
VOTE = collections.Counter()      # 近期 P 帧投票（用于选主流）
VOTE_RECENT = {}                  # base -> deque[(monotonic, 1)]  1 秒滑动窗口
LEAD = {}                         # base -> 连续领先次数
HYS = {"cand": None, "since": 0.0, "last": 0.0}
IDR_LOCK = {"base": None, "t": 0.0}
IDR_PIN_SEC = float(os.environ.get("LIVE_IDR_PIN", "25"))
WARNT = {"t": 0.0}
ACT = {"n": 0}
try:
    import collections as _c
    VOTE_RECENT = collections.defaultdict(_c.deque)
except Exception:
    pass

# ★★相机的"直播传输"是**有状态会话**：相机每 ~0.13s 发一个 35B stream-check，
#   客户端必须回 34B ack（同样三段 16 位时钟，是客户端的时钟）。回放死的 ack
#   = 告诉相机"客户端失联" → 最多吐十几帧就停推。→ 改成反应式活客户端。
ACK_ON = os.environ.get("LIVE_ACK", "1") == "1"
TREQ_ON = os.environ.get("LIVE_TREQ", "1") == "1"
ACK_OFF = [int(x) for x in os.environ.get("LIVE_ACK_OFF", "19880,30245,31507").split(",")]
ACK_MAX = float(os.environ.get("LIVE_ACK_MAX", "0"))     # 每秒最多回几次（0=不限）
ACK = {"tpl": None, "sent": 0, "dxy": 0, "last": 0.0, "skip": 0,
       "clk0": None, "clk": 0}       # ★LIVE_ACK_MODE=clk 用的虚拟客户端时钟（见 reply_ack）


def _find_ack_template():
    for t, q in ALL_UP_RAW:
        if len(q) == 34 and q[2:4] == b"\xae\x2d" and q[4:8] == b"\x00\x00\x04\x25":
            return q
    for t, q in ALL_UP_RAW:
        if len(q) == 34 and q[2:4] == b"\xae\x2d":
            return q
    return None


ACK["tpl"] = _find_ack_template()
if ACK["tpl"] is None:
    ACK_ON = False
else:
    log("活客户端 ack 模板 %dB 就绪（偏移 %s）" % (len(ACK["tpl"]), ACK_OFF))


def reply_ack(check):
    """收到相机 stream-check，用它的片上时钟现算一个 ack 回去（保持时钟同步=客户端还活着）。"""
    tpl = ACK["tpl"]
    if not tpl:
        return
    ACK["last_chk"] = bytes(check)      # 取证用：最近一次 check 原文（ctrl chk 取回）
    if ACK_MAX > 0:
        now = time.monotonic()
        if now - ACK["last"] < 1.0 / ACK_MAX:
            ACK["skip"] += 1
            return
        ACK["last"] = now
    a = bytearray(tpl)
    # ★★第八轮离线取证结论（up9004.bin 里 3140 条真机 ack 的硬证据）：
    #   1) 真机 ack 的 (8,10)、(16,18) 两对 **永远是同一个值**（77% 完全相同），
    #      而相机 35B check 里这两对是**不相等**的。照抄 check 会破坏"成对相等"不变量。
    #   2) 三个字段都是 16 位回绕的活计数，必须跟着时钟走。
    #   ★★★第十轮定案：真正有效的是 **clk 档**（虚拟客户端时钟），其余只作对照。
    mode = os.environ.get("LIVE_ACK_MODE", "clk")
    if mode == "clk":
        # ★★r85 的关键发现（kick_sniff.pcap，双向，2614 条真机 ack）：
        #   真机 ack 的 f8/f16/f24 是**同一个单调时钟的三次采样**——
        #   Δ(字段)/Δ(时间) ≈ 2.34e6 单位/秒（约 427ns 一个单位），16 位回绕 ≈28ms。
        #   它跟相机 check 里那个"7 单位/ms"的计数器**不是同一个钟**，所以任何"抄 check"
        #   的写法都等于告诉相机"我的客户端时钟停着"→ 发完首个 GOP 就停推（固定 16 帧）。
        #   本档自己维护虚拟客户端时钟：起点对齐首个 check 的 f8，之后按 RATE 推进。
        #   ★实测：只有这一档能跨过磁带结尾继续收流（ACK=0 时必在 45~55s 处掉流）。
        rate = float(os.environ.get("LIVE_ACK_RATE", "2338000"))
        if ACK["clk0"] is None:
            ACK["clk0"] = (struct.unpack_from("<H", check, 8)[0], time.monotonic())
            ACK["clk"] = ACK["clk0"][0]
        el = time.monotonic() - ACK["clk0"][1]
        v = int(ACK["clk0"][0] + rate * el) & 0xFFFF
        for o in (8, 10, 16, 18, 24, 26):
            struct.pack_into("<H", a, o, v)
    elif mode == "table":                       # 老行为：check + ACK_OFF（对照用）
        for i, o in enumerate((8, 10, 16, 18, 24, 26)):
            v = struct.unpack_from("<H", check, o)[0]
            v = (v + ACK_OFF[min(i // 2, 2)]) & 0xFFFF
            struct.pack_into("<H", a, o, v)
    elif mode in ("dup", "dup_lo", "dup_hi"):  # 成对收成同一值（对照用）
        for lo, hi in ((8, 10), (16, 18)):
            v = struct.unpack_from("<H", check, hi if mode == "dup_hi" else lo)[0]
            struct.pack_into("<H", a, lo, v)
            struct.pack_into("<H", a, hi, v)
        struct.pack_into("<H", a, 24, struct.unpack_from("<H", check, 24)[0])
        struct.pack_into("<H", a, 26, struct.unpack_from("<H", check, 26)[0])
    else:                                     # echo / plus：原样回显（可加固定微调）
        bias = int(os.environ.get("LIVE_ACK_BIAS", "0")) if mode == "plus" else 0
        for o in (8, 10, 16, 18, 24, 26):
            v = (struct.unpack_from("<H", check, o)[0] + bias) & 0xFFFF
            struct.pack_into("<H", a, o, v)
    try:
        U.sendto(bytes(a), ("127.0.0.1", 9004))
        ACK["sent"] += 1
        ACK["last_ack"] = bytes(a)
    except OSError:
        pass


def is_stream_check(d):
    return len(d) == 35 and d[2:4] == b"\xae\x2d" and d[4:8] == b"\x00\x00\x01\x21"


# 75B 的"直播传输请求"包（`... 4a 00 2a 10 0X 00 ...`）。
TREQ = {"tpl": None, "n": 0, "idx": 0}
TREQ_CTL = {"hz": 0.0}          # 运行时取帧请求频率（ctrl "treq N" 可改，0=停发）
for t, q in ALL_UP_RAW:
    if len(q) == 75 and b"\x4a\x00\x2a\x10" in q:
        TREQ["tpl"] = q
        TREQ["idx"] = q.find(b"\x4a\x00\x2a\x10") + 4
        break
if TREQ["tpl"] is None:
    TREQ_ON = False
else:
    log("传输请求模板 %dB 就绪（计数位 %d）" % (len(TREQ["tpl"]), TREQ["idx"]))


def send_treq():
    q = bytearray(TREQ["tpl"])
    TREQ["n"] = (TREQ["n"] + 1) & 0xFF
    q[TREQ["idx"]] = TREQ["n"] if TREQ["n"] else 1
    try:
        U.sendto(bytes(q), ("127.0.0.1", 9004))
    except OSError:
        pass


def broadcast(kind, data):
    with lock:
        dead = []
        for c in clients[kind]:
            try:
                c.sendall(data)
            except OSError:
                dead.append(c)
        for c in dead:
            clients[kind].remove(c)


def strip_frag0(body):
    if body[:5] == DJI_HEAD:
        return body[17:]
    for m in range(0, min(48, len(body) - 4)):
        if body[m:m + 4] == NAL_START and (body[m + 4] & 0x1F) in NAL_TYPES:
            return body[m:]
    return body


def emit(key, f):
    """片齐 → 拼帧 → 判定 IDR/主流 P 帧 → 推给 5010 客户端。"""
    base, fid = key
    parts = f["parts"]
    BASE_DROP[base] = BASE_DROP.get(base, 0) + 1     # 先记"到过这里"的帧数，成功时再抵掉
    if base == st["main_base"] and st["frames"] > 20:
        NEED_REPAIR["v"] = True
    if (0, 0) not in parts:
        st["incomplete"] += 1
        st["r_no0"] += 1
        return
    frags = {x for x, _ in parts}
    # ★★拼法权威口径 = 与离线 osmo360/video.py::_assemble 完全一致：
    #   frag0(rank0) 剥 17B 包头，然后按 (frag, rank) 排序续接、每片剥 1B 分片头：
    #   (0,1),(1,0),(1,1),(2,0),(2,1)… 也就是 rank0/rank1 在**同一片号上交替**。
    #   离线这条口径解出过 127 帧干净双鱼眼 / live20.h264（264 帧、conceal 仅 20 次）。
    byrank = {0: {}, 1: {}}
    for (fr, rk), body in parts.items():
        byrank.setdefault(rk, {})[fr] = body
    if ASM == "split":
        # "前后段"拼法（对照）：rank0 全部片 → rank1 全部片。实测 conceal 7891。
        ok = all(d and len(d) == max(d) + 1 for d in (byrank[0], byrank[1]))
        if not ok:
            st["incomplete"] += 1
            st["r_pair"] += 1
            return
        b = bytearray(strip_frag0(byrank[0][0]))
        for i in range(1, max(byrank[0]) + 1):
            b += byrank[0][i][1:]
        b += strip_frag0(byrank[1][0])
        for i in range(1, max(byrank[1]) + 1):
            b += byrank[1][i][1:]
        need = max(max(byrank[0]), max(byrank[1])) + 1
    else:
        if (0, 0) not in parts:
            st["incomplete"] += 1
            st["r_no0"] += 1
            return
        need = max(frags) + 1
        if len(frags) != need:
            st["incomplete"] += 1
            st["r_gap"] += 1
            return
        b = bytearray(strip_frag0(parts[(0, 0)]))
        for key in sorted(parts):
            if key == (0, 0):
                continue
            b += parts[key][1:]
    # ★帧尾截断必须拦掉：末片丢了以后 max 自动变小，缺尾巴的帧照样被放行 → 花屏。
    if need < EXPECTED.get(base, 0):
        st["incomplete"] += 1
        st["r_trunc"] += 1
        TRUNC_FAIL[base] = TRUNC_FAIL.get(base, 0) + 1
        if TRUNC_FAIL[base] > 200:
            EXPECTED[base] = need
            TRUNC_FAIL[base] = 0
            log("片数变化 base=0x%02x 重新学习为 %d" % (base, need))
        return
    seg = bytes(b)
    if len(seg) <= 64 or (seg[4] & 0x1F) not in NAL_TYPES:
        st["r_nal"] += 1
        return
    nt = seg[4] & 0x1F
    head = seg[:1024]
    sps = (NAL_START + b"\x67") in head or (NAL_START + b"\x27") in head
    is_idr = sps or nt == 5
    st["cframes"] = st.get("cframes", 0) + 1
    EXPECTED[base] = max(EXPECTED.get(base, 0), max(frags) + 1)
    _er = EXPECTED_R.setdefault(base, {})
    for rk in (0, 1):
        _n = [fr + 1 for (fr, r) in parts if r == rk]
        if _n:
            _er[rk] = max(_er.get(rk, 0), max(_n))
    if is_idr:
        IDR_CACHE["buf"] = seg
        IDR_LIVE["ok"] = True
        NEED_REPAIR["v"] = False
        st["idr"] += 1
        st["key_base"] = base
        log("★ IDR base=0x%02x %dB 片=%d" % (base, len(seg), len(parts)))
        IDR_LOCK["base"] = base
        IDR_LOCK["t"] = time.monotonic()
        if st["main_base"] != base:
            log("钉住主路 0x%02x -> 0x%02x (有 IDR 的实例优先)" % (
                st["main_base"] or 0, base))
            st["main_base"] = base
            st["idr_lock"] = st.get("idr_lock", 0) + 1
    elif nt == 1:
        if not IDR_CACHE["buf"]:
            if os.environ.get("LIVE_NOIDR", "0") != "1":
                st["r_nomain"] += 1
                return
        if LOCK_BASE and ACT["n"] > 2 and base != st["main_base"] and not ACCEPT_ALL:
            if MERGE and is_idr:
                log("合并接管 0x%02x (IDR %dB)" % (base, len(seg)))
                st["main_base"] = base
                st["idr_switch"] = st.get("idr_switch", 0) + 1
            else:
                st["r_nomain"] += 1
                return
    else:
        st["r_nal"] += 1
        return
    st["frames"] += 1
    BIT = 0
    BASE_OK[base] = BASE_OK.get(base, 0) + 1
    BASE_DROP[base] -= 1          # 成功就不算丢
    broadcast("v", b"\x01\x00" + struct.pack("<I", len(seg)) + seg)


def receiver():
    while True:
        try:
            d = U.recv(65535)
        except (BlockingIOError, OSError):
            time.sleep(0.001)
            continue
        st["pkts"] += 1
        if ACK_ON and is_stream_check(d):
            reply_ack(d)
        if DUMP["left"] > 0:
            DUMP["left"] -= 1
            try:
                if DUMP["f"] is None:
                    DUMP["f"] = open(DUMP_PATH, "wb")
                DUMP["f"].write(struct.pack("<I", len(d)) + d)
                if DUMP["left"] == 0:
                    DUMP["f"].close()
                    DUMP["f"] = None
                    log("取证落盘完成 %s" % DUMP_PATH)
            except OSError:
                pass
        if len(d) > 1000:
            st["big"] += 1
        if len(d) < 20:
            continue
        ch = d[17]
        base, fid = ch & 0x7F, d[16]
        rank = ch >> 7
        if ASM == "rank0" and rank != 0:
            continue
        if len(d) > 1000:
            VOTE[base] += 1
            VOTE_RECENT[base].append((time.monotonic(), 1))
        old = CUR_FID.get(base)
        if old is not None and old != fid and FLUSH_FID:
            k = (base, old)
            if k in FRAMES:
                emit(k, FRAMES.pop(k))
        CUR_FID[base] = fid
        key = (base, fid)
        f = FRAMES.get(key)
        if f is None:
            f = FRAMES[key] = {"t0": time.monotonic(), "parts": {}}
        f["parts"][(d[18] & 0x1F, rank)] = d[19:]
        if len(FRAMES) > 256:          # 防内存膨胀
            oldest = min(FRAMES, key=lambda k: FRAMES[k]["t0"])
            emit(oldest, FRAMES.pop(oldest))
        # ★★第八轮：片齐立刻 emit（低延迟的关键）。
        _er = EXPECTED_R.get(base) or {}
        _p = f["parts"]
        if _er.get(0) and _er.get(1):
            if all((i, 0) in _p for i in range(_er[0])) and \
               all((i, 1) in _p for i in range(_er[1])):
                emit(key, FRAMES.pop(key))


def watchdog():
    last_vote = time.monotonic()
    while True:
        time.sleep(0.01)
        now = time.monotonic()
        for key in list(FRAMES):
            f = FRAMES.get(key)
            if f and now - f["t0"] >= WINDOW:
                emit(key, FRAMES.pop(key))
        # ★主流选举（强迟滞 + 冷却）：新候选必须比当前高出 2 倍票数、领先持续 0.6 秒。
        if now - T_START >= 2.5:
            for b in list(VOTE_RECENT):
                while VOTE_RECENT[b] and now - VOTE_RECENT[b][0][0] > 1.0:
                    VOTE_RECENT[b].popleft()
            cand = [(len(v), b) for b, v in VOTE_RECENT.items() if v]
            if cand:
                cand.sort(reverse=True)
                top, tv = cand[0][1], cand[0][0]
                cur = st["main_base"]
                if cur is None:
                    if tv >= 20:
                        st["main_base"] = top
                        log("主流锁定 0x%02x (1s 票=%d)" % (top, tv))
                        NEED_REPAIR["v"] = True
                elif top != cur:
                    cur_v = len(VOTE_RECENT.get(cur, ()))
                    silent = cur_v == 0
                    pinned = (IDR_LOCK["base"] == cur and
                              now - IDR_LOCK["t"] < IDR_PIN_SEC and not silent)
                    if pinned:
                        HYS["cand"] = None
                    elif (silent and tv >= 20 and now - HYS["last"] > 0.8) or \
                       (tv > 2.0 * max(1, cur_v) and tv >= 40 and now - HYS["last"] > 2.0):
                        if HYS["cand"] == top:
                            if now - HYS["since"] >= 0.6:
                                log("主流切换 0x%02x -> 0x%02x (1s 票=%d vs %d, 全场 %s)" % (
                                    cur, top, tv, cur_v,
                                    ",".join("0x%02x:%d" % (b, n) for n, b in cand[:5])))
                                st["main_base"] = top
                                HYS["last"] = now
                                NEED_REPAIR["v"] = True
                                HYS["cand"], HYS["since"] = None, now
                        else:
                            HYS["cand"], HYS["since"] = top, now
                    else:
                        HYS["cand"] = None
                else:
                    HYS["cand"] = None
            last_vote = now
        ACT["n"] = len([b for b, v in VOTE_RECENT.items() if len(v) > 3])
        if now - WARNT["t"] > 5.0 and ACT["n"] >= 3:
            WARNT["t"] = now
            log("⚠ 检测到 %d 路视频并存：相机被历次会话残留污染，请断电重开相机后再起流" % ACT["n"])
        if NEED_REPAIR["v"] and REPAIR and st["frames"] > 20 and st.get("forces", 0) < 30:
            do_force("丢帧自愈")


def retime(q, delta_ms):
    """把上行包族头里的三对 16 位时钟（偏移 8/10、16/18、24/26）整体前推 delta_ms。
    循环重放时如果不改时间戳，客户端时钟就会"倒退半个世纪"，相机会当成失效会话。"""
    if len(q) < 28:
        return q
    b = bytearray(q)
    d = int(delta_ms) & 0xFFFF
    for off in (8, 10, 16, 18, 24, 26):
        v = struct.unpack_from("<H", b, off)[0]
        struct.pack_into("<H", b, off, (v + d) & 0xFFFF)
    return bytes(b)


def session_keeper():
    """完整单次重放（照抄原生 63.5s 时序），然后**循环重放稳态段**保活。
    ★★第十轮定案：
      · 磁带不裁剪（CUT=0）——裁剪会连带切掉冷启动那段"切 app 模式"的顺序；
      · 重放 0~LOOP_TO（默认 54s，55s 之后是原始会话的收尾段，重放它会把流关掉）；
      · 之后循环 CYCLE_FROM~CYCLE_TO（默认 6.3~54s 健康期，一轮 ≈47.7s），含磁带自带的
        ack/treq 骨架，且每轮用相机实时 check 重算时钟偏移（RESTAMP=1）。
    这套组合是本轮唯一实测连续 150s+、15~25fps 不断流的形态。"""
    loop_to = LOOP_TO_SEC
    tape = [(t, q) for t, q in ALL_UP if t <= loop_to]
    # ★★第八轮关键修复：**开机重放也要做时钟对齐**（以前只有稳态循环做了 retime）。
    delta = 0
    if os.environ.get("LIVE_BOOT_RETIME", "1") == "1" and ACK.get("tpl"):
        t_end = time.monotonic() + 3.0
        while time.monotonic() < t_end and not ACK.get("last_chk"):
            time.sleep(0.05)
        if ACK.get("last_chk"):
            tpl = ACK["tpl"]
            ds = []
            for o, k in ((8, 0), (16, 1), (24, 2)):
                cur = (struct.unpack_from("<H", ACK["last_chk"], o)[0] + ACK_OFF[k]) & 0xFFFF
                cap = struct.unpack_from("<H", tpl, o)[0]
                ds.append((cur - cap) & 0xFFFF)
            ds.sort()
            delta = ds[len(ds) // 2]          # 取中位数，避开某个字段跨 16 位回绕
            log("开机时钟对齐 delta=%d（%s）" % (delta, ",".join(str(x) for x in ds)))
        else:
            log("开机时钟对齐失败：3s 内没收到 stream-check，按 0 偏移重放")
    start = time.monotonic()
    for t, q in tape:
        w = (start + t) - time.monotonic()
        if w > 0:
            time.sleep(min(w, 1.0))
        try:
            U.sendto(retime(q, delta) if delta else q, ("127.0.0.1", 9004))
        except OSError:
            pass
    log("完整重放结束(截到 %.1fs)，进入稳态循环保活" % loop_to)
    if delta == 0 and os.environ.get("LIVE_REARM", "1") == "1" and ACK.get("tpl"):
        t_end = time.monotonic() + 4.0
        while time.monotonic() < t_end and not ACK.get("last_chk"):
            time.sleep(0.05)
        if ACK.get("last_chk"):
            tpl = ACK["tpl"]
            ds = []
            for o, k in ((8, 0), (16, 1), (24, 2)):
                cur = (struct.unpack_from("<H", ACK["last_chk"], o)[0] + ACK_OFF[k]) & 0xFFFF
                cap = struct.unpack_from("<H", tpl, o)[0]
                ds.append((cur - cap) & 0xFFFF)
            ds.sort()
            delta = ds[len(ds) // 2]
            log("后置时钟对齐 delta=%d，重放模式包+订阅重新拉起预览" % delta)
            try:
                U.sendto(retime(MODE_PKT, delta), ("127.0.0.1", 9004))
            except OSError:
                pass
            time.sleep(0.30)
            for q in BURST:
                try:
                    U.sendto(retime(q, delta), ("127.0.0.1", 9004))
                except OSError:
                    pass
                time.sleep(0.004)
            log("→ 已用对齐时钟补发 模式包+订阅 %d 条" % len(BURST))
        else:
            log("后置对齐失败：仍无 stream-check，无法重拉预览")
    if APPMODE and os.environ.get("LIVE_APPMODE") == "2":
        try:
            U.sendto(MODE_PKT, ("127.0.0.1", 9004))
        except OSError:
            pass
        log("→ 已补发 app 模式包 %dB，等 300ms 补订阅" % len(MODE_PKT))
        time.sleep(0.30)
        for q in BURST:
            try:
                U.sendto(q, ("127.0.0.1", 9004))
            except OSError:
                pass
            time.sleep(0.004)
        log("→ 已补发订阅 %d 条，等相机新建编码器" % len(BURST))
    cycle = [(t - BOOT_SEC, q) for t, q in tape if t > BOOT_SEC]
    cycle_skip = {int(x) for x in os.environ.get("LIVE_CYCLE_SKIP", "").split(",") if x.strip()}
    cycle_from = os.environ.get("LIVE_CYCLE_FROM")
    if cycle_from and cycle_from.lower() in ("full", "whole"):
        # ★第十轮：整盘循环（含开场模式包与收尾），"每 span 秒由活客户端重新开一次预览"。
        cyc = [(t, q) for t, q in tape]
        cycle = cyc
        log("整盘循环模式：%d 条 / %.2fs 一轮" % (len(cyc), loop_to))
    elif cycle_from:
        cf = float(cycle_from)
        if cf > 0:
            ce = float(os.environ.get("LIVE_CYCLE_TO", str(loop_to)))
            cyc = [(t - cf, q) for t, q in tape if cf <= t <= ce and len(q) not in cycle_skip]
        else:
            cyc = []        # CYCLE_FROM=0：彻底关掉数据段循环，只留周期订阅
        subs = [(0.0, q) for t, q in tape if len(q) in KEEP_LONG]
        cycle = cyc + subs
    span = max(t for t, _ in cycle) if cycle else 1.0
    if span <= 0.02:
        # ★★第十轮 bug 修复：只有"周期订阅"（时间戳全是 0）时，旧代码把 span 设 1.0，
        #   但循环里没有任何 sleep → 1 秒内把 55 条订阅刷了上万遍（日志里出现
        #   "稳态循环 #28880"）。这等于对相机 UDP 打洪水。改成按 0.1s 一条匀开。
        cycle = [(i * 0.1, q) for i, (t, q) in enumerate(cycle)]
        span = max(0.1, len(cycle) * 0.1)
    log("稳态循环 %d 条 / %.2fs" % (len(cycle), span))
    # ★★第十轮：时钟再戳模式。每轮都拿相机刚发的 35B check 重算 delta，
    #   让上行包里的片上时钟始终贴着相机"现在"，模拟一个真的还在的客户端。
    restamp = os.environ.get("LIVE_RESTAMP", "0") == "1"

    def _fresh_delta():
        ck = ACK.get("last_chk")
        tpl = ACK.get("tpl")
        if not ck or not tpl:
            return None
        ds = []
        for o, i in ((8, 0), (16, 1), (24, 2)):
            cur = (struct.unpack_from("<H", ck, o)[0] + ACK_OFF[i]) & 0xFFFF
            cap = struct.unpack_from("<H", tpl, o)[0]
            ds.append((cur - cap) & 0xFFFF)
        ds.sort()
        return ds[len(ds) // 2]

    k = 0
    while True:
        cstart = time.monotonic()
        k += 1
        delta = k * span * 1000.0
        if restamp:
            fd = _fresh_delta()
            if fd is not None:
                delta = fd
        for t, q in cycle:
            w = (cstart + t) - time.monotonic()
            if w > 0:
                time.sleep(min(w, 0.5))
            try:
                U.sendto(retime(q, delta), ("127.0.0.1", 9004))
            except OSError:
                pass
        if k % 5 == 0:
            log("稳态循环 #%d 完成" % k)


def do_force(reason=""):
    """请求一次 fresh IDR（force_I_once），带最小间隔限流。返回是否真的发起了。"""
    now = time.monotonic()
    if now - LAST_FORCE["t"] < FORCE_MIN_GAP:
        return False
    LAST_FORCE["t"] = now
    st["forces"] = st.get("forces", 0) + 1
    log("请求 fresh IDR（%s）" % reason)

    def _run():
        subprocess.run("cd /usr/lib && LD_PRELOAD=/data/local/tmp/hk/libdcam_hook.so:libqcom_adapter.so "
                       "timeout 9 dji_cst -d cst -u -f -m duss_liveview_transmit_request_force_I_once "
                       "-l /tmp/live_force.log >/dev/null 2>&1", shell=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    threading.Thread(target=_run, daemon=True).start()
    return True


def kick_worker():
    if KICK_DELAY > 0:
        time.sleep(KICK_DELAY)
        log("连接中 restart dji_camera3（逼 IDR）")
        subprocess.run(["systemctl", "restart", "dji_camera3"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if FORCE_PERIOD <= 0:
        return
    while True:
        time.sleep(FORCE_PERIOD)
        if st["frames"] > 20:          # 只有流真的跑起来了才谈得上保险丝
            do_force("周期保险丝")


def pick_source():
    """选择当前真实可用的 4ch 录音源（filter 源可能已被卸载）。"""
    try:
        out = subprocess.check_output(["pactl", "list", "short", "sources"],
                                      stderr=subprocess.DEVNULL).decode("utf-8", "ignore")
    except Exception:
        return AUDIO_DEV
    names = [l.split("\t")[1] for l in out.splitlines() if "\t" in l]
    for want in (AUDIO_DEV, "regular0"):
        if want in names:
            return want
    return names[-1] if names else AUDIO_DEV


def audio_worker():
    if os.environ.get("LIVE_AUDIO", "1") != "1":
        log("音频采集已关闭（LIVE_AUDIO=0）")
        return
    while True:
        dev = pick_source()
        cmd = ["parec", "--device=" + dev, "--format=s24-32le",
               "--channels=4", "--rate=48000", "--file-format=raw"]
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            log("音频源已连接 %s" % dev)
            chunk = 48000 * 4 * 4 // 50     # 20ms
            while True:
                b = p.stdout.read(chunk)
                if not b:
                    break
                st["audio_bytes"] += len(b)
                broadcast("a", b)
        except Exception as e:
            log("音频异常: %s" % e)
        try:
            p.terminate()
        except Exception:
            pass
        time.sleep(1.5)


def serve(port, kind, new_client=None):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(4)
    log("服务监听 %d (%s)" % (port, kind))
    while True:
        c, _ = s.accept()
        try:
            c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        if kind == "v" and IDR_CACHE["buf"]:
            body = IDR_CACHE["buf"]
            try:
                c.sendall(b"\x01\x00" + struct.pack("<I", len(body)) + body)
                log("新客户端补发缓存 IDR %dB" % len(body))
            except OSError:
                pass
        if kind in ("v", "a"):
            with lock:
                clients[kind].append(c)
        if new_client:
            threading.Thread(target=new_client, args=(c,), daemon=True).start()


def ctrl_handle(c):
    buf = b""
    while True:
        try:
            d = c.recv(1024)
        except OSError:
            return
        if not d:
            return
        buf += d
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            cmd = line.decode("utf-8", "ignore").strip().lower()
            if cmd == "stat":
                rep = ("pkts=%d big=%d frames=%d cframes=%d idr=%d force=%d incomplete=%d key=%s main=%s "
                       "audio_MB=%.2f vclients=%d aclient=%d | no0=%d gap=%d pair=%d trunc=%d nomain=%d nal=%d" % (
                           st["pkts"], st["big"], st["frames"], st.get("cframes", 0), st["idr"],
                           st.get("forces", 0),
                           st["incomplete"],
                           hex(st["key_base"]) if st["key_base"] else "-",
                           hex(st["main_base"]) if st["main_base"] else "-",
                           st["audio_bytes"] / 1e6, len(clients["v"]), len(clients["a"]),
                           st["r_no0"], st["r_gap"], st["r_pair"], st["r_trunc"],
                           st["r_nomain"], st["r_nal"]))
                rep += " | ack=%d treq=%d" % (ACK["sent"], TREQ["n"])
                rep += " | b=" + ",".join("%02x:%d/%d" % (b, BASE_OK.get(b, 0), BASE_DROP.get(b, 0))
                                          for b in sorted(set(BASE_OK) | set(BASE_DROP)))
            elif cmd.startswith("idr"):
                try:
                    open("/dev/shm/idr_cur.h264", "wb").write(IDR_CACHE["buf"])
                    rep = "idr %dB live=%s -> /dev/shm/idr_cur.h264" % (
                        len(IDR_CACHE["buf"]), IDR_LIVE["ok"])
                except OSError as e:
                    rep = "idr 落盘失败 %s" % e
            elif cmd.startswith("dump"):
                n = int(cmd.split()[1]) if len(cmd.split()) > 1 else 600
                DUMP["left"] = n
                rep = "dump %d packets -> %s" % (n, DUMP_PATH)
            elif cmd == "asm":
                rep = "asm=%s" % ASM
            elif cmd == "chk":
                rep = "chk=%s ack=%s n=%d" % (
                    (ACK.get("last_chk") or b"").hex(), (ACK.get("last_ack") or b"").hex(),
                    ACK["sent"])
            elif cmd == "kick":
                threading.Thread(target=lambda: subprocess.run(
                    ["systemctl", "restart", "dji_camera3"]), daemon=True).start()
                rep = "kick"
            elif cmd.startswith("treq"):
                try:
                    TREQ_CTL["hz"] = float(cmd.split()[1])
                except (IndexError, ValueError):
                    TREQ_CTL["hz"] = 0.0
                rep = "treq_hz=%.1f" % TREQ_CTL["hz"]
            elif cmd == "force":
                threading.Thread(target=lambda: subprocess.run(
                    "cd /usr/lib && LD_PRELOAD=/data/local/tmp/hk/libdcam_hook.so:libqcom_adapter.so "
                    "timeout 9 dji_cst -d cst -u -f -m duss_liveview_transmit_request_force_I_once "
                    "-l /tmp/live_force.log", shell=True), daemon=True).start()
                rep = "force_I"
            elif cmd == "quit":
                rep = "bye"
                threading.Timer(0.3, lambda: os._exit(0)).start()
            elif cmd == "help":
                rep = "stat | kick | force | quit | chk | dump N | asm | treq N"
            else:
                rep = "unknown"
            try:
                c.sendall((rep + "\n").encode())
            except OSError:
                return


def treq_worker():
    """周期重发"直播传输请求"，计数递增（模拟 app 持续请求，而不是抓包尸块）。
    ★★2026-11-22 实测定论（r75 扫参）：treq 是帧率节流阀，也是危险开关。
      5Hz→1.0fps、10Hz→26.1、20→20.0、30→8.0、45→0.9、60→6.2、0→21.5。
      所以默认 10Hz；高于 30Hz 会把相机会话打崩。"""
    if not TREQ_ON:
        return
    hz = float(os.environ.get("LIVE_TREQ_HZ", "25"))
    TREQ_CTL["hz"] = hz          # 运行时可被 ctrl "treq N" 改写
    while True:
        hz = TREQ_CTL["hz"]
        time.sleep(1.0 / max(0.2, hz) if hz > 0 else 0.5)
        if hz <= 0:
            continue                     # treq 0 = 停发（诊断：验证是否真的靠它节流）
        send_treq()
        if TREQ["n"] % 30 == 1:
            log("传输请求 keep-alive n=%d  ack_sent=%d  hz=%.1f" % (TREQ["n"], ACK["sent"], hz))


for a in ((VIDEO_PORT, "v", None), (CTRL_PORT, "c", ctrl_handle), (AUDIO_PORT, "a", None)):
    threading.Thread(target=serve, args=a, daemon=True).start()
for fn in (receiver, watchdog, session_keeper, kick_worker, audio_worker, treq_worker, dump_worker):
    threading.Thread(target=fn, daemon=True).start()
log("实时服务就绪 5010视频 / 5011控制 / 5012音频  window=%.3fs  asm=%s" % (WINDOW, ASM))
while True:
    time.sleep(5)
    log("stat pkts=%d big=%d frames=%d idr=%d incomplete=%d audio=%.1fMB v=%d a=%d" % (
        st["pkts"], st["big"], st["frames"], st["idr"], st["incomplete"],
        st["audio_bytes"] / 1e6, len(clients["v"]), len(clients["a"])))
