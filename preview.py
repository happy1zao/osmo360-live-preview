#!/usr/bin/env python3
# preview.py —— PC 端 Osmo360 实时预览窗口 + 控制台
# 用法：python preview.py [--adb 路径] [--width 1024] [--no-forward]
#       python preview.py --file samples/live20.h264          # 回放，立刻看到干净双鱼眼
#       python preview.py --file samples/live20.h264 --snap out.png --snap-frames 120   # 无窗口抓帧自检
# 依赖：numpy opencv-python；ffmpeg（环境变量 FFMPEG 或 PATH）；adb 连接相机
# 原理：相机端 lv_relay.py 组好双鱼眼 H264 帧 -> TCP:5010 -> 本机 ffmpeg 解码 -> 显示
#       控制台 TCP:5011 可发 stat/kick/rehand/ackn/reqms/kf 等命令
import socket, struct, subprocess, threading, sys, os, time, queue, argparse, shutil
import numpy as np
import cv2

ROOT = os.path.dirname(os.path.abspath(__file__))
# ffmpeg：环境变量 FFMPEG → PATH → 字面 "ffmpeg"（外部二进制，非 pip 包）
FF = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
# adb：环境变量 ADB → PATH → 字面 "adb"（失败时给清晰提示而不是崩栈）
DEFAULT_ADB = os.environ.get("ADB") or shutil.which("adb") or "adb"
W, H = 2048, 1024
FRAME_BYTES = W * H * 3

ap = argparse.ArgumentParser()
ap.add_argument("--adb", default=DEFAULT_ADB)
ap.add_argument("--width", type=int, default=1024)
ap.add_argument("--no-forward", action="store_true")
ap.add_argument("--file", default=None, help="回放已抓取的 IDR 起始 H264 文件（演示干净预览）")
ap.add_argument("--snap", default=None, help="解码到第 N 帧后保存 PNG 并退出（无人值守自检）")
ap.add_argument("--snap-frames", type=int, default=120, help="--snap 的帧号，默认 120")
args = ap.parse_args()


def adb_forward():
    for p in (5010, 5011):
        try:
            r = subprocess.run([args.adb, "forward", "tcp:%d" % p, "tcp:%d" % p],
                               capture_output=True, timeout=15)
            if r.returncode != 0:
                print("  [警告] adb forward %d 失败: %s" % (p, (r.stderr or b"").decode("utf-8", "ignore").strip()))
        except FileNotFoundError:
            raise SystemExit(
                "找不到 adb：请用 --adb 完整路径指定，或把 adb 加入 PATH。\n"
                "  cannot find adb / pass --adb <path>\adb.exe or add adb to PATH")
        except (subprocess.TimeoutExpired, OSError) as e:
            print("  [警告] adb forward %d 异常: %s" % (p, e))


def connect(port, retries=40):
    for _ in range(retries):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=3)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return s
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("无法连接相机中继端口 %d，请先在相机端运行 run_relay.sh" % port)


class Preview:
    def __init__(self):
        self.canvas = np.zeros((H, W, 3), np.uint8)
        self.running = True
        self.frames = 0
        self.lock = threading.Lock()
        self.ctrl = None
        self.ff = None

    def start_ffmpeg(self, src_file=None):
        # 统一走 stdin 管道；回放也由 python 限速喂，避免 ffmpeg 对裸 H264 做 seek
        # （-stream_loop 在裸 H264 上会报 "Operation not permitted" 直接退出）
        cmd = [FF, "-hide_banner", "-loglevel", "error", "-err_detect", "ignore_err",
               "-flags2", "+showall", "-f", "h264", "-i", "pipe:0",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-an", "pipe:1"]
        # ffmpeg 的告警落盘，出问题时可以直接看（不影响运行）
        self.errlog = open(os.path.join(ROOT, "ff_last.log"), "wb")
        self.ff = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=self.errlog)

    def feed_loop(self, sock):
        buf = b""
        while self.running:
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
                nal = buf[6:6 + ln]
                buf = buf[6 + ln:]
                try:
                    self.ff.stdin.write(nal)
                    self.ff.stdin.flush()
                except OSError:
                    self.running = False
                    break
            if len(buf) > 8_000_000:
                buf = b""

    def decode_loop(self):
        while self.running:
            raw = self.ff.stdout.read(FRAME_BYTES)
            if not raw or len(raw) < FRAME_BYTES:
                break
            frame = np.frombuffer(raw, np.uint8).reshape(H, W, 3)
            with self.lock:
                self.canvas = frame.copy()
                self.frames += 1
            if args.snap and self.frames >= args.snap_frames:
                cv2.imwrite(args.snap, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
                print("\n  已抓帧保存到", args.snap)
                os._exit(0)

    def console_loop(self):
        print("\n=== Osmo360 预览控制台 ===  输入 help 查看命令，quit 退出")
        while self.running:
            try:
                cmd = input("osmo360> ").strip()
            except (EOFError, KeyboardInterrupt):
                self.running = False
                break
            if not cmd:
                continue
            low = cmd.lower()
            if low in ("quit", "exit"):
                self.running = False
                break
            if low == "help":
                print("=== 状态与流控 ===")
                print("  stat       相机端统计（big/ok/drop/gap/idr/clients）")
                print("  ackn <n>   ACK 批量计数（默认 4）")
                print("  reqms <s>  周期请求间隔（默认 0.052 秒）")
                print("=== 关键帧 / 握手 ===")
                print("  kick       重启 camera3 并重握手（逼 IDR）")
                print("  rehand     仅重发握手")
                print("  kf         发 0x38 关键帧请求")
                print("=== 本地 ===")
                print("  save       保存当前预览截图到 samples/")
                print("  help       显示本帮助")
                print("  quit       退出")
                continue
            if low == "save":
                p = os.path.join(ROOT, "samples", "preview_%d.png" % int(time.time()))
                with self.lock:
                    cv2.imwrite(p, cv2.cvtColor(self.canvas, cv2.COLOR_RGB2BGR))
                print("  已保存", p)
                continue
            if self.ctrl is None:
                print("  回放模式仅支持 help/save/quit")
                continue
            try:
                self.ctrl.sendall((cmd + "\n").encode())
                self.ctrl.settimeout(2)
                rep = self.ctrl.recv(4096).decode("utf-8", "ignore").strip()
                print("  ", rep)
            except OSError as e:
                print("  控制通道错误:", e)

    def feed_file(self, path):
        """回放：按约 2MB/s 限速分段喂给 ffmpeg，播完保持最后一帧。"""
        data = open(path, "rb").read()
        CHUNK = 256 * 1024
        self.ff.stdin.write(data[:CHUNK]); self.ff.stdin.flush()
        for i in range(CHUNK, len(data), CHUNK):
            if not self.running:
                return
            try:
                self.ff.stdin.write(data[i:i + CHUNK])
                self.ff.stdin.flush()
            except OSError:
                return
            time.sleep(0.12)
        print("\n  回放数据已发送完毕（保持最后一帧）。实时模式请关闭 --file。")

    def run(self):
        if args.file:
            print("回放模式:", args.file)
            self.start_ffmpeg(src_file=args.file)
            threading.Thread(target=self.feed_file, args=(args.file,), daemon=True).start()
            threading.Thread(target=self.decode_loop, daemon=True).start()
            if args.snap:
                # 自检模式：不开窗，等到第 N 帧存图后进程自行退出
                while self.running:
                    time.sleep(0.2)
                return
            threading.Thread(target=self.console_loop, daemon=True).start()
            self._display()
            return
        if not args.no_forward:
            adb_forward()
        print("连接相机中继 ...")
        vid = connect(5010)
        self.ctrl = connect(5011)
        self.start_ffmpeg()
        threading.Thread(target=self.feed_loop, args=(vid,), daemon=True).start()
        threading.Thread(target=self.decode_loop, daemon=True).start()
        threading.Thread(target=self.console_loop, daemon=True).start()
        self._display()

    def _display(self):
        disp_w = args.width
        disp_h = disp_w // 2
        cv2.namedWindow("Osmo360 Live Preview", cv2.WINDOW_AUTOSIZE)
        moved = False
        t0 = time.time()
        while self.running:
            with self.lock:
                img = self.canvas.copy()
                n = self.frames
            disp = cv2.resize(img, (disp_w, disp_h), interpolation=cv2.INTER_AREA)
            disp = cv2.cvtColor(disp, cv2.COLOR_RGB2BGR)
            el = time.time() - t0
            fps = n / el if el > 0 else 0
            cv2.putText(disp, "frames=%d  %.1f fps" % (n, fps),
                        (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
            cv2.imshow("Osmo360 Live Preview", disp)
            if not moved:
                # 高 DPI / 多屏下 opencv 常把窗口放到屏幕外，首次显示后强制挪到可见位置
                cv2.moveWindow("Osmo360 Live Preview", 60, 60)
                cv2.setWindowProperty("Osmo360 Live Preview", cv2.WND_PROP_TOPMOST, 1)
                moved = True
            if cv2.waitKey(30) & 0xFF == 27:
                self.running = False
        cv2.destroyAllWindows()
        try:
            self.ff.stdin.close()
            self.ff.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    Preview().run()
