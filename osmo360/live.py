#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""live —— 低延迟实时预览 + 实时音频（脱手机，常驻流式）

与 dev_capture() 的区别：dev_capture 是「抓 55 秒 pcap → 拉回 → 批量解码」，
本模块是「相机端常驻服务实时组帧 → TCP 5010/5012 → PC 增量解码」，
端到端延迟 ≈ 一帧组帧时间 + 网络 + 一次解码（实测目标 < 200ms）。

设备端 probe/live_server.py 负责：
  * 完整上行重放维持会话（bootstrap + steady 循环）
  * 连接中 kick camera3 逼出 IDR，周期性 force_I 重新同步
  * UDP 9004 下行按 pair 拼法实时组帧 → TCP 5010（兼容 preview.py）
  * parec 4ch/48k/s24-32le → TCP 5012（20ms 块，只录不播）

PC 端用法：
    from osmo360 import LiveStream
    live = LiveStream()
    live.start()                      # 一键：推脚本/样本、起服务、建 forward、等 IDR
    live.show(play=False)             # 音画同窗：双鱼眼画面 + 4 通道电平条（默认不发声）
    # 或分别取流喂模型：
    for frame in live.frames():       # (1024, 2048, 3) uint8 RGB
        model.infer(frame)
    for pcm, rms in live.audio_chunks():
        audio_model.infer(pcm)        # pcm: (N, 4) float32
    live.stop()

命令行：python -m osmo360.live [--play] [--no-window] [--secs N]
"""
import os, sys, time, socket, struct, subprocess, threading, queue, argparse

from .transport import Adb
from .video import FFMPEG, FULL_W, FULL_H
from .audio import AudioCapture, RATE, CHANNELS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTE_SCRIPT = "/data/local/tmp/live_server.py"
REMOTE_PCAP = "/dev/shm/ae2d.pcap"
REMOTE_UPLINK = "/dev/shm/up9004.bin"      # 258KB 精简上行包（替代 58MB pcap）
LOCAL_SCRIPT = os.path.join(ROOT, "probe", "live_server.py")
LOCAL_PCAP = os.path.join(ROOT, "samples", "mimo_cold_handshake.pcap")
LOCAL_UPLINK = os.path.join(ROOT, "samples", "up9004.bin")
LOCAL_SEED = os.path.join(ROOT, "samples", "idr_seed.h264")
REMOTE_SEED = "/dev/shm/idr_seed.h264"
PORTS = (5010, 5011, 5012)
FB = FULL_W * FULL_H * 3
# 断流判定窗口（秒）：超过这么久没解出帧就自动重建链路
STALL_SEC = float(os.environ.get("LIVE_STALL", "8"))


def _readn(f, n):
    buf = bytearray()
    while len(buf) < n:
        c = f.read(n - len(buf))
        if not c:
            break
        buf += c
    return bytes(buf)


class LiveStream:
    def __init__(self, adb=None, ffmpeg=None, handshake=None):
        self.adb = adb or Adb()
        self.ffmpeg = ffmpeg or FFMPEG
        self.handshake = handshake or LOCAL_PCAP
        self._ready = False

    # ---------- 启动 / 停止 ----------
    def start(self, wait=16, reuse=True):
        """一键幂等：推脚本 → 起相机端服务（先 bind 收包） → 再重启 camera3 抓首帧 IDR。

        ★★第五轮定性的正确顺序（很关键，别改回去）：
          相机那条视频流是**一条长期在跑的**编码器，中间加入的客户端只能拿到 P 帧，
          没有 SPS/PPS/IDR 就永远解成灰残影（这正是"花屏/发灰"的真因）。
          唯一可靠拿 IDR 的办法：**先让我们的 socket 就位，再重启 dji_camera3**，
          让编码器重建，新编码器的首帧就是 IDR —— 我们必须在它出生前就开始收包。
          旧流程"先 kick 再等 4 秒起服务"正好把 IDR 错过，所以永远发灰。

        reuse=True：如果服务已经在推流且拿到过 IDR，直接接管，不折腾相机。
        """
        rc, out = self.adb.devices()
        if self.adb.serial not in out or "\tdevice" not in out:
            raise RuntimeError("相机不在线：%s" % out.strip())
        for p in PORTS:                     # forward 每进程重建（本机 adb daemon 会被回收）
            self.adb.forward(p, p)
        if reuse and self._healthy():
            print("[live] 复用已在跑的相机端服务（已有 IDR，跳过重启）", flush=True)
            return True
        self.adb.sh("python3 /data/local/tmp/gate_off.py >/dev/null 2>&1")
        # 只重启 dji_sw_uav（9004 的会话状态机在其中，清掉历史客户端绑定）；
        # ★不再提前重启 camera3——它必须在我们的 socket 就位之后再重启。
        self.adb.sh("systemctl restart dji_sw_uav >/dev/null 2>&1; sleep 2", timeout=40)
        self.adb.push(LOCAL_SCRIPT, REMOTE_SCRIPT, timeout=60)
        if os.path.exists(LOCAL_UPLINK):
            self.adb.push(LOCAL_UPLINK, REMOTE_UPLINK, timeout=60)
            self.adb.sh("rm -f %s; echo cleaned" % REMOTE_PCAP)
            up_src = REMOTE_UPLINK
        else:
            self.adb.push(self.handshake, REMOTE_PCAP, timeout=300)
            up_src = REMOTE_PCAP
        # ★pkill 的匹配串必须写成 live_serve[r].py：直接写 live_server.py 会**匹配到
        #   正在执行这条命令的 adb shell 自己**（命令行里含同样字样），把自己杀掉（exit 143）。
        self.adb.sh("pkill -f 'live_serve[r].py' 2>/dev/null; sleep 0.5")
        # ★相机端开关（2026-11-22 第十轮 A/B 的胜出组合，实测连续 150s+ 不断流、15~25fps、conceal=0）：
        #   CUT=0      **不裁剪磁带**。裁剪会连带切掉冷启动那段"切 app 模式"的顺序，
        #              实测只剩 5~16 帧（这是之前"十几帧天花板"的一部分原因）。
        #   LOOP_TO=54 磁带截到 54s（55s 之后是原始会话的收尾段，重放它会把流关掉）。
        #   CYCLE_FROM=6.30 CYCLE_TO=54  重放到 54s 后循环 6.3~54s 这段"健康期"（含周期订阅、
        #              含磁带自带的 ack/treq 骨架），一轮 ≈47.7s，一轮接一轮永不停。
        #   RESTAMP=1  每轮都用相机刚发的 35B stream-check 重算时钟偏移，而不是按轮次累加估计，
        #              让上行包里的片上时钟始终贴着相机"现在"（否则相机会判客户端时钟过时）。
        #   ACK=1 ACK_MODE=clk  ack 用虚拟客户端时钟（真机 ack 是同一个 2.34MHz 单调钟的三次采样）。
        #   TREQ=1 TREQ_HZ=10   10Hz 取帧请求：高于 30Hz 会把相机打崩，10Hz 是扫参得到的最佳值。
        #   LOCK_BASE=0 ★★base 是环形槽位不是实例，必须全收；只收 main 会丢 5/6 帧（=卡）。
        #   SEED=0     跨会话种子 SPS 不匹配，塞给新客户端只会更花。
        renv = os.environ.get("LIVE_REMOTE_ENV", "").strip()
        if not renv:
            renv = ("LIVE_CUT=0 LIVE_LOOP_TO=54.0 LIVE_CYCLE_FROM=6.30 LIVE_CYCLE_TO=54.0 "
                    "LIVE_RESTAMP=1 LIVE_TREQ=1 LIVE_TREQ_HZ=10 LIVE_LOCK_BASE=0 LIVE_SEED=0 "
                    "LIVE_APPMODE=0 LIVE_ACK=1 LIVE_ACK_MODE=clk LIVE_FORCE=0 LIVE_REPAIR=0 "
                    "LIVE_ASM=pair")
        # ★A/B 开关：LIVE_EXTRA 里的 KEY=VAL 会覆盖上面的默认（shell env 后者胜）
        #   例：LIVE_EXTRA="ASM=split LIVE_WINDOW=0.25"
        extra = os.environ.get("LIVE_EXTRA", "").strip()
        if extra:
            renv = (renv + " " + extra).strip()
        self.adb.sh("setsid env %s python3 -u %s %s > /dev/shm/live_server.log 2>&1 "
                    "</dev/null & echo started" % (renv, REMOTE_SCRIPT, up_src))
        for p in PORTS:
            self.adb.forward(p, p)
        time.sleep(1.5)                     # 让 receiver 线程先真正跑起来（收包要先就位）
        if os.environ.get("LIVE_NO_RESTART", "0") == "1":
            # ★第八轮：不重启相机。用于"相机刚被单独重启过、编码器已经是干净单实例"的场景——
            #   每多重启一次 dji_camera3 就可能多留一个不销毁的 venc 实例（实测出现过
            #   real_fps=148 的失控实例，把 SoC 打满、主流掉到 0~4fps）。IDR 用 ctrl force 单次取。
            print("[live] 跳过重启 dji_camera3（LIVE_NO_RESTART=1），直接接入在跑的流", flush=True)
        else:
            print("[live] 服务已就位，重启 dji_camera3 让编码器重建（首帧 IDR 会被我们接住）",
                  flush=True)
            self.adb.sh("systemctl restart dji_camera3", timeout=40)
        # 等"真实视频流 + IDR"，不是光有 big 在涨
        import re
        t0 = time.time()
        big = 0
        got_idr = False
        while time.time() - t0 < wait:
            time.sleep(1)
            s = self.stat()
            m = re.search(r"big=(\d+)", s)
            i = re.search(r"idr=(\d+)", s)
            big = int(m.group(1)) if m else 0
            idr = int(i.group(1)) if i else 0
            if big > 100 and idr >= 1:
                self._ready = True
                got_idr = True
                print("[live] 就绪（已抓到 IDR）：%s" % s, flush=True)
                self.last_kick = time.time()
                return True
        self._ready = bool(big > 100)
        print("[live] %s：%s" % ("有流但没抓到 IDR（画面可能发灰）" if self._ready else "未就绪",
                                self.stat()), flush=True)
        self.last_kick = time.time()
        return self._ready

    def _healthy(self):
        """相机端服务是否已经在推流且拿到过真 IDR（决定能否免重启接管）。

        ★注意（2026-11-22 第六轮纠正）：以前这里把"b= 里出现多个 base"当成
          "多路编码器污染"，那是错的——离线核对每个 fid 只对应一个 base、base 在
          6 个槽位之间轮转，说明**只有一条流，base 是环形槽位**。所以这里不再
          用 base 数量判断健康，只看"有没有 IDR + 帧在涨"。
        """
        import re
        s = self.stat()
        m = re.search(r"frames=(\d+)", s)
        b = re.search(r"big=(\d+)", s)
        i = re.search(r"idr=(\d+)", s)
        if not (m and b and i):
            return False
        if int(i.group(1)) < 1 or int(m.group(1)) < 20 or int(b.group(1)) < 200:
            return False
        self._ready = True
        print("[live] 当前状态：%s" % s, flush=True)
        return True

    def stat(self):
        try:
            s = socket.create_connection(("127.0.0.1", 5011), timeout=2)
            s.sendall(b"stat\n")
            r = s.recv(4096).decode("utf-8", "ignore").strip()
            s.close()
            return r
        except OSError:
            return "(服务未就绪)"

    def stop(self):
        # 同 start()：必须用 live_serve[r].py，否则会杀掉执行这条命令的 adb shell 本身
        self.adb.sh("pkill -f 'live_serve[r].py' 2>/dev/null; echo stopped")
        self._ready = False

    def ctrl(self, cmd, timeout=3):
        """向相机端控制口(5011)发一条命令，返回一行回执（诊断/取证用）。"""
        try:
            s = socket.create_connection(("127.0.0.1", 5011), timeout=timeout)
            s.sendall((cmd.strip() + "\n").encode())
            r = s.recv(4096).decode("utf-8", "ignore").strip()
            s.close()
            return r
        except OSError as e:
            return "(ctrl 失败 %s)" % e

    def log_tail(self, n=20):
        return self.adb.sh("tail -%d /dev/shm/live_server.log 2>/dev/null" % n)[1]

    # ---------- 视频（增量解码，低延迟） ----------
    def frames(self, out_w=None, out_h=None):
        """yield (out_h, out_w, 3) uint8 RGB 帧；ffmpeg 常驻增量解码。

        out_w/out_h: 解码输出尺寸（默认 2048×1024 全量）。预览建议传一半，
        因为 2048×1024 rgb24 一帧 6.3MB，19fps 就是 120MB/s，光搬字节就压垮 Python。
        """
        import numpy as np
        out_w = out_w or FULL_W
        out_h = out_h or FULL_H
        fb = out_w * out_h * 3
        sock = socket.create_connection(("127.0.0.1", 5010), timeout=5)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # ★ffmpeg 的报错别再丢进 /dev/null：之前"错误很多、花屏"全靠猜。
        #   落盘到 logs/live_ff.log，出问题直接看这个文件。
        logdir = os.path.join(ROOT, "logs")
        os.makedirs(logdir, exist_ok=True)
        errf = open(os.path.join(logdir, "live_ff.log"), "wb")
        ff = subprocess.Popen(
            [self.ffmpeg, "-hide_banner", "-loglevel", "warning", "-err_detect", "ignore_err",
             "-flags2", "+showall",
             # ★低延迟：禁输入缓冲/探测、低延迟解码、输出立即 flush
             "-fflags", "+nobuffer+flush_packets", "-flags", "low_delay",
             "-probesize", "32", "-analyzeduration", "0",
             "-f", "h264", "-i", "pipe:0",
             # ★强制输出到固定尺寸：真实 SPS 是 2042×1022（不是 2048×1024），
             #   按固定字节数读 rawvideo 会整帧错位 → 画面错乱/花屏。
             "-vf", "scale=%d:%d" % (out_w, out_h),
             "-f", "rawvideo", "-fps_mode", "passthrough",
             "-flush_packets", "1", "-pix_fmt", "rgb24", "pipe:1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errf)
        outq = queue.Queue(maxsize=4)          # ★只留 4：宁可丢旧帧也不让延迟累积
        fd = ff.stdout.fileno()

        def decoder():
            # ★零拷贝读：原来 _readn 用 `buf += c` 一点点累加，一帧 6MB 要 memcpy 几百 MB，
            #   实测把端到端帧率压到 10fps 以下。这里用 os.readv 直接写进 numpy 的 memoryview。
            arr = np.empty((out_h, out_w, 3), np.uint8)
            mv = memoryview(arr).cast("B")
            while True:
                off = 0
                while off < fb:
                    try:
                        # Windows 没有 os.readv；BufferedReader.readinto 直接把管道数据写进
                        # numpy 的 memoryview，同样零拷贝（一次 syscall 可能读不满，故循环）。
                        got = ff.stdout.readinto(mv[off:])
                    except (OSError, ValueError, AttributeError):
                        got = 0
                    if not got:
                        off = -1
                        break
                    off += got
                if off < 0:
                    break
                try:
                    outq.put(arr.copy(), timeout=1)     # copy 只一次，换来给下游安全对象
                except queue.Full:
                    pass
            outq.put(None)

        def collector():
            buf = b""
            while True:
                try:
                    d = sock.recv(65536)
                except OSError:
                    break
                if not d:
                    break
                buf += d
                while len(buf) >= 6 and buf[0] == 0x01:
                    ln = struct.unpack("<I", buf[2:6])[0]
                    if len(buf) < 6 + ln:
                        break
                    nal, buf = buf[6:6 + ln], buf[6 + ln:]
                    try:
                        ff.stdin.write(nal)
                        ff.stdin.flush()
                    except OSError:
                        break
            try:
                ff.stdin.close()
            except OSError:
                pass

        threading.Thread(target=decoder, daemon=True).start()
        threading.Thread(target=collector, daemon=True).start()
        # ★断流自愈：相机端的直播会话约 2~3 分钟后可能被关掉（实测）。
        #   这里只要 STOP 秒内没有解码出帧，就认为链路已死，把 socket 读超时打出来，
        #   由 main() 触发一次 kick-first 重建（这是目前唯一能救回来的软件手段）。
        try:
            while True:
                try:
                    f = outq.get(timeout=STALL_SEC)
                except queue.Empty:
                    print("[live] ⚠ %.0fs 无帧，判定链路已断，准备自动重建" % STALL_SEC, flush=True)
                    break
                if f is None:
                    break
                yield f
        finally:
            try:
                sock.close()
            except OSError:
                pass
            try:
                ff.kill()
            except OSError:
                pass

    # ---------- 音频（实时 PCM） ----------
    def audio_chunks(self):
        """yield (pcm(N,4) float32, rms(4,))；20ms 一块，只录不播。"""
        s = socket.create_connection(("127.0.0.1", 5012), timeout=5)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        buf = b""
        frame_bytes = CHANNELS * 4
        while True:
            try:
                d = s.recv(65536)
            except OSError:
                break
            if not d:
                break
            buf += d
            n = len(buf) // frame_bytes * frame_bytes
            if n:
                pcm = AudioCapture.decode_bytes(buf[:n])
                buf = buf[n:]
                if len(pcm):
                    yield pcm, AudioCapture.rms(pcm)

    # ---------- 音画同窗 ----------
    def show(self, play=False, secs=None, scale=0.5, gain=64.0):
        """低延迟音画同窗：双鱼眼画面 + 4 通道实时电平条。

        play=False（默认）：只显示电平，不发声（遵守"不要发出声音"）。
        play=True：把 PCM 送 ffplay 播放（**会发出声音**）。
        """
        import cv2, numpy as np
        ffp = None
        if play:
            ffp = subprocess.Popen(
                [self.ffmpeg.replace("ffmpeg.exe", "ffplay.exe"), "-nodisp", "-autoexit",
                 "-loglevel", "error", "-f", "s32le", "-ar", str(RATE),
                 "-ac", str(CHANNELS), "-i", "pipe:0"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rms = np.zeros(CHANNELS, np.float32)
        aq = queue.Queue(maxsize=64)

        def audio():
            try:
                for pcm, r in self.audio_chunks():
                    rms[:] = np.maximum(rms * 0.7, r)   # 峰值保持衰减
                    if ffp:
                        try:
                            ffp.stdin.write(_pcm_to_s32(pcm, gain))
                            ffp.stdin.flush()
                        except OSError:
                            pass
                    try:
                        aq.put_nowait(r)
                    except queue.Full:
                        pass
            except OSError:
                pass

        threading.Thread(target=audio, daemon=True).start()
        t0 = time.time()
        n = 0
        # ★直接在 ffmpeg 里缩到目标尺寸：省掉 cv2.resize，也把每帧搬运量从 6.3MB 降到 1.6MB
        dw = max(2, int(FULL_W * scale) // 2 * 2)
        dh = max(2, int(FULL_H * scale) // 2 * 2)
        try:
            for frame in self.frames(out_w=dw, out_h=dh):
                n += 1
                img = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                h, w = img.shape[:2]
                # 底部 4 条实时电平（绿→黄→红）
                cv2.rectangle(img, (0, h - 46), (w, h), (18, 18, 18), -1)
                for i in range(CHANNELS):
                    x0 = 12 + i * (w - 24) // CHANNELS
                    # 相机录音增益当前极低（RMS≈5e-5），用软件增益把底噪/语音抬到可见可听
                    level = min(1.0, float(rms[i]) * gain * 200)
                    bw = (w - 24) // CHANNELS - 14
                    cv2.rectangle(img, (x0, h - 34), (x0 + bw, h - 20), (50, 50, 50), -1)
                    if level > 0:
                        col = (0, 200, 80) if level < 0.6 else ((0, 200, 255) if level < 0.85 else (0, 0, 255))
                        cv2.rectangle(img, (x0, h - 34), (x0 + int(bw * level), h - 20), col, -1)
                    cv2.putText(img, "CH%d" % (i + 1), (x0, h - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)
                cv2.putText(img, "LIVE %d frames  audio %s" % (n, "ON" if play else "monitor"),
                            (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 120), 2)
                cv2.imshow("Osmo360 live (q to quit)", img)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
                if secs and time.time() - t0 > secs:
                    break
        finally:
            cv2.destroyAllWindows()
            if ffp:
                try:
                    ffp.stdin.close()
                except OSError:
                    pass
        return n


def _pcm_to_s32(pcm, gain=1.0):
    """float32 [-1,1] → s32le；gain 用于补偿相机侧过低的录音增益。"""
    import numpy as np
    return (np.clip(pcm * gain, -1, 1) * float(1 << 31)).astype("<i4").tobytes()


def main():
    ap = argparse.ArgumentParser(description="Osmo360 低延迟实时预览 + 实时音频")
    ap.add_argument("--play", action="store_true", help="播放音频（会发出声音）")
    ap.add_argument("--gain", type=float, default=64.0,
                    help="音频软件增益（相机录音增益极低，默认 64x）")
    ap.add_argument("--no-window", action="store_true", help="不显示音画同窗（只统计）")
    ap.add_argument("--secs", type=float, default=0, help="运行时长秒数，0=直到按 q")
    ap.add_argument("--handshake", default=None)
    args = ap.parse_args()
    live = LiveStream(handshake=args.handshake)
    t_all = time.time()
    total, rnd = 0, 0
    while True:
        rnd += 1
        # 第一轮允许"接管已在跑的服务"；之后每轮都是断流后的重建，走完整的 kick-first 路径
        ok = live.start(reuse=(rnd == 1))
        if not ok:
            print("[live] 第 %d 轮启动未就绪，日志：\n%s" % (rnd, live.log_tail()), flush=True)
            if rnd >= 3:
                print("[live] 连续 3 轮失败，放弃。请确认相机在线 / 未切 WiFi 模式。", flush=True)
                return 1
            time.sleep(3)
            continue
        try:
            if args.no_window:
                n = 0
                for _ in live.frames():
                    n += 1
                    if args.secs and time.time() - t_all > args.secs:
                        break
                total += n
                print("[live] 第 %d 轮收到 %d 帧；%s" % (rnd, n, live.stat()), flush=True)
            else:
                n = live.show(play=args.play, secs=args.secs or None, gain=args.gain)
                total += n
                print("[live] 第 %d 轮显示 %d 帧；%s" % (rnd, n, live.stat()), flush=True)
        except KeyboardInterrupt:
            break
        finally:
            live.stop()
        if args.secs and time.time() - t_all > args.secs:
            break
        print("[live] 累计 %d 帧，自动重建链路 …" % total, flush=True)
        time.sleep(1)
    print("[live] 结束，累计 %d 帧 / %.1fs" % (total, time.time() - t_all), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
