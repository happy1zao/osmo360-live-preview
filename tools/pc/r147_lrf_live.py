"""r147_lrf_live.py —— LRF 实时预览 v3（legacy，已被 v4 r156 取代；保留作对照）

链路：
  设备端 python3 lrf_tail.py <LRF> <offset>  ── 单条 adb exec-out 常驻流（O(1) seek + 跟随写入）
      ↓ 连续字节流
  本端 AVCC(4字节长度前缀) → AnnexB，按 IDR 补 SPS/PPS
      ↓
  ffmpeg 解码 + 缩小 → bgr24 rawvideo
      ↓
  独立读线程切帧 → cv2 显示 / 存 PNG

关键坑（都踩过）：
  * tail -c +N 在 500MB 文件上会线性扫，直接超时 → 必须用设备端 python seek；
  * 不能从 mdat 头读，要从"当前写指针往前 1MB"，再向前找最近的 IDR；
  * 读 ffmpeg stdout 必须独立线程，否则主循环阻塞后整条链停摆；
  * SPS/PPS 需由外部已完成片段提供（--sps 指向一段含 SPS/PPS 的 h264 片段）。

环境变量：ADB / ADB_SERIAL / FFMPEG（缺省均走 PATH）。
"""
import argparse
import os
import struct
import shutil
import subprocess
import threading
import time

ADB = os.environ.get("ADB") or shutil.which("adb") or "adb"
SERIAL = os.environ.get("ADB_SERIAL")  # None = single device, omit -s
FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
TAILER = "/data/local/tmp/lrf_tail.py"


def _adb_prefix():
    return [ADB] + (["-s", SERIAL] if SERIAL else [])


def sh(cmd, timeout=15):
    return subprocess.run(_adb_prefix() + ["shell", cmd],
                          stdout=subprocess.PIPE, timeout=timeout).stdout.decode("utf8", "ignore")


def size_of(path):
    s = sh("stat -c %%s '%s'" % path).strip()
    return int(s) if s.isdigit() else -1


def read_head(path, n=8192):
    return subprocess.run(_adb_prefix() + ["exec-out",
                           "python3 -c \"import sys;f=open('%s','rb');sys.stdout.buffer.write(f.read(%d))\"" % (path, n)],
                          stdout=subprocess.PIPE, timeout=15).stdout


def load_sps(p):
    d = open(p, "rb").read()
    codes = [i for i in range(len(d) - 3) if d[i:i + 4] == bytes.fromhex("00000001")]
    return d[:codes[2]] if len(codes) >= 3 else d


def nals(buf, sps):
    """buf 起点必须是 NAL 起点。返回 (annexb, consumed, count)。"""
    out = bytearray()
    pos = n = 0
    lim = len(buf)
    first = True
    while pos + 4 <= lim:
        ln = struct.unpack(">I", bytes(buf[pos:pos + 4]))[0]
        if ln < 2 or ln > (1 << 21) or pos + 4 + ln > lim:
            break
        t = buf[pos + 4] & 0x1F
        if t == 0:
            break
        if first or t == 5:
            out += sps
            first = False
        out += bytes.fromhex("00000001") + bytes(buf[pos + 4:pos + 4 + ln])
        pos += 4 + ln
        n += 1
    return bytes(out), pos, n


def resync(buf):
    lim = len(buf) - 12
    p = 0
    while p < lim:
        ln = struct.unpack(">I", bytes(buf[p:p + 4]))[0]
        if 2 < ln < (1 << 21) and 1 <= (buf[p + 4] & 0x1F) <= 12:
            q = p + 4 + ln
            if q + 5 <= len(buf):
                ln2 = struct.unpack(">I", bytes(buf[q:q + 4]))[0]
                if 2 < ln2 < (1 << 21) and 1 <= (buf[q + 4] & 0x1F) <= 12:
                    return p
        p += 1
    return -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--secs", type=int, default=30)
    ap.add_argument("--sps", required=True, help="含 SPS/PPS 的 h264 片段（外部借来的参数集）")
    ap.add_argument("--scale", type=int, default=960)
    ap.add_argument("--back", type=int, default=1024 * 1024, help="从写指针往回多少字节起步")
    ap.add_argument("--save-dir", default="samples/rec/live")
    ap.add_argument("--save-every", type=int, default=0)
    ap.add_argument("--no-window", action="store_true")
    args = ap.parse_args()

    sps = load_sps(args.sps)
    print("[v3] SPS/PPS %d B" % len(sps), flush=True)
    sh("ls -l %s 2>/dev/null | head -1" % TAILER)

    # 新开录的文件开头是 0 字节，等它写出 ftyp+free+mdat 头再解析
    for _try in range(40):
        head = read_head(args.file)
        i = 0
        mdat = None
        while i + 8 <= len(head):
            size, typ = struct.unpack(">I4s", head[i:i + 8])
            if typ == b"mdat":
                mdat = i + 8
                break
            if size < 8:
                break
            i += size
        if mdat:
            break
        time.sleep(0.25)
    print("[v3] mdat @%s" % mdat, flush=True)

    sz0 = size_of(args.file)
    start = max(mdat, sz0 - args.back)
    print("[v3] 文件 %.1fMB，从 %d 起" % (sz0 / 1048576.0, start), flush=True)

    out_w = args.scale
    out_h = (out_w // 2) // 2 * 2
    fb = out_w * out_h * 3

    ff = subprocess.Popen(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer",
         "-f", "h264", "-i", "pipe:0", "-vf", "scale=%d:%d" % (out_w, out_h),
         "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)

    rx = bytearray()
    lock = threading.Lock()
    stop_flag = [False]

    def reader():
        while not stop_flag[0]:
            try:
                b = ff.stdout.read(8192)
            except Exception:
                break
            if not b:
                break
            with lock:
                rx.extend(b)

    threading.Thread(target=reader, daemon=True).start()

    # 常驻跟随流
    proc = subprocess.Popen(_adb_prefix() + ["exec-out",
                             "python3 -u %s '%s' %d" % (TAILER, args.file, start)],
                            stdout=subprocess.PIPE, bufsize=0)

    if args.save_every:
        os.makedirs(args.save_dir, exist_ok=True)
        import cv2
        import numpy as np

    # ★首字节就必须是 SPS/PPS：否则 ffmpeg 在管道里认不出流，
    #   会立刻以 "Output file does not contain any stream" 退出，后续写管道全部 BrokenPipe。
    try:
        ff.stdin.write(sps)
        ff.stdin.flush()
    except Exception as e:
        print("[v3] 写 SPS 失败：%s" % e)
        return

    t0 = time.time()
    frames = 0
    saved = 0
    buf = bytearray()
    fed = 0
    aligned = [False]
    last = t0

    def pump():
        nonlocal buf, fed
        while not stop_flag[0]:
            b = proc.stdout.read(262144)
            if not b:
                time.sleep(0.03)
                continue
            buf += b
            if not aligned[0]:
                # 起点是任意偏移，先对齐到可信 NAL，再找最近的 IDR
                if len(buf) < 1024 * 1024:
                    continue
                r = resync(buf)
                if r < 0:
                    buf.clear()
                    continue
                del buf[:r]
                seek = 0
                idr = -1
                while seek + 5 < len(buf):
                    ln = struct.unpack(">I", bytes(buf[seek:seek + 4]))[0]
                    if 2 < ln < (1 << 21) and seek + 4 + ln <= len(buf):
                        if (buf[seek + 4] & 0x1F) == 5:
                            idr = seek
                            break
                        seek += 4 + ln
                    else:
                        seek += 1
                if idr < 0:
                    continue
                del buf[:idr]
                aligned[0] = True
            if len(buf) < 4:
                continue
            out, consumed, n = nals(buf, sps)
            if consumed == 0:
                if len(buf) > 2 * 1024 * 1024:
                    r = resync(buf)
                    if r > 0:
                        del buf[:r]
                    else:
                        buf.clear()
                continue
            del buf[:consumed]
            if out:
                try:
                    ff.stdin.write(out)
                    ff.stdin.flush()
                    fed += n
                except Exception:
                    stop_flag[0] = True
                    break

    threading.Thread(target=pump, daemon=True).start()

    while time.time() - t0 < args.secs:
        with lock:
            if len(rx) >= fb:
                img_bytes = bytes(rx[:fb])
                del rx[:fb]
            else:
                img_bytes = None
        if img_bytes is None:
            time.sleep(0.004)
            continue
        frames += 1
        if args.save_every and frames % args.save_every == 0:
            img = np.frombuffer(img_bytes, np.uint8).reshape(out_h, out_w, 3)
            p = os.path.join(args.save_dir, "live_%03d.png" % saved)
            cv2.imwrite(p, img)
            saved += 1
        if not args.no_window:
            try:
                import cv2
                import numpy as np
                img = np.frombuffer(img_bytes, np.uint8).reshape(out_h, out_w, 3)
                cv2.imshow("osmo360 live 2:1", img)
                if cv2.waitKey(1) & 0xFF == 27:
                    break
            except Exception:
                pass
        if time.time() - last > 2:
            el = time.time() - t0
            lag = (size_of(args.file) - start) - fed * 0  # 近似
            print("[v3] %.1fs frame=%d fps=%.1f nals=%d buf=%dKB"
                  % (el, frames, frames / max(el, 0.1), fed, len(buf) // 1024), flush=True)
            last = time.time()

    stop_flag[0] = True
    print("\n[v3] 结束 frame=%d %.1fs → %.1f fps, nals=%d" % (frames, time.time() - t0,
          frames / max(time.time() - t0, 0.1), fed))
    try:
        proc.kill()
    except Exception:
        pass
    try:
        ff.stdin.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
