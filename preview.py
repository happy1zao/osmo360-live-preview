#!/usr/bin/env python3
# preview.py —— PC 端 Osmo360 实时预览窗口 + 控制台
# 用法：python preview.py [--adb 路径] [--width 1024] [--no-forward]
#       python preview.py --file samples/live20.h264   # 回放 IDR 起始样本，立即看到干净双鱼眼
# 依赖：numpy opencv-python；ffmpeg（仓库 bin 内或 PATH）；adb 连接相机
# 原理：相机端 lv_relay.py 组好双鱼眼 H264 帧 -> TCP:5010 -> 本机 ffmpeg 解码 -> 显示
#       控制台 TCP:5011 可发 stat/kick/rehand/ackn/reqms/kf 等命令
import socket, struct, subprocess, threading, sys, os, time, queue, argparse, shutil
import numpy as np
import cv2

ROOT = os.path.dirname(os.path.abspath(__file__))
FF = os.path.join(ROOT, "bin", "ffmpeg-master-latest-win64-gpl-shared", "bin", "ffmpeg.exe")
if not os.path.exists(FF):
    FF = shutil.which("ffmpeg") or "ffmpeg"
DEFAULT_ADB = shutil.which("adb") or "adb"
W, H = 2048, 1024
FRAME_BYTES = W * H * 3

ap = argparse.ArgumentParser()
ap.add_argument("--adb", default=DEFAULT_ADB)
ap.add_argument("--width", type=int, default=1024)
ap.add_argument("--no-forward", action="store_true")
ap.add_argument("--file", default=None, help="回放已抓取的 IDR 起始 H264 文件（演示干净预览）")
args = ap.parse_args()


def adb_forward():
    for p in (5010, 5011):
        subprocess.run([args.adb, "forward", "tcp:%d" % p, "tcp:%d" % p],
                       capture_output=True, timeout=15)


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

    def start_ffmpeg(self):
        self.ff = subprocess.Popen(
            [FF, "-hide_banner", "-loglevel", "error", "-err_detect", "ignore_err",
             "-flags2", "+showall", "-f", "h264", "-i", "pipe:0",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-an", "pipe:1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

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
                print("  stat       相机端统计（big/ok/drop/gap/idr/clients）")
                print("  kick       重启 camera3 并重握手（逼 IDR）")
                print("  rehand     重发握手")
                print("  ackn <n>   ACK 批量计数（默认4，越小越稳）")
                print("  reqms <s>  周期请求间隔（默认0.052）")
                print("  kf         请求关键帧")
                print("  save       保存当前预览截图")
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
        data = open(path, "rb").read()
        while self.running:
            try:
                self.ff.stdin.write(data)
                self.ff.stdin.flush()
            except OSError:
                break
            time.sleep(0.5)

    def run(self):
        if args.file:
            print("回放模式:", args.file)
            self.start_ffmpeg()
            threading.Thread(target=self.feed_file, args=(args.file,), daemon=True).start()
            threading.Thread(target=self.decode_loop, daemon=True).start()
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
