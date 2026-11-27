#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r156_lrf_live2.py —— .LRF 实时预览 v4：按 GOF 锚点"捞 NAL"流式解码

与 v3(r147) 的区别（关键）：
  v3 假设 mdat 是纯 AVCC，顺序吃 → 遇到每帧之间的 protobuf 立刻错位，只出 1 帧。
  v4 承认"每帧前有一段 proto"，改为：
    · 只接受长度合法的 NAL（4B 前缀 + 类型 1/5/6/7/8/9，长度 2KB~400KB）；
    · 未命中就跳 1 字节 —— 相当于自动越过 proto 区；
    · 遇到 GOF 锚点（00 00 00 12 67 SPS + 00 00 00 05 68 PPS + IDR）时，
      把 SPS/PPS/IDR 一起喂进去，让解码器在每个关键帧重新对齐。
  SPS/PPS 由 LRF 自行内嵌，**不再依赖外部借来的 SPS**（这是 v3 的另一个坑）。

用法：
  python tools/pc/r156_lrf_live2.py --file <正在写的 .LRF> --secs 30 --scale 960 --save-every 30

环境变量：ADB（adb 路径，缺省走 PATH）、ADB_SERIAL（缺省单设备）、
          FFMPEG（ffmpeg 路径，缺省走 PATH）。
"""
import argparse
import os
import struct
import shutil
import subprocess
import sys
import threading
import time

ADB = os.environ.get("ADB") or shutil.which("adb") or "adb"
SERIAL = os.environ.get("ADB_SERIAL")  # None = single device, omit -s
FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
TAILER = "/data/local/tmp/lrf_tail.py"

START = bytes.fromhex("00000001")
MIN_LEN, MAX_LEN = 2000, 400000
OK_TYPES = (1, 5, 6, 7, 8, 9)


def _adb_prefix():
    return [ADB] + (["-s", SERIAL] if SERIAL else [])


def sh(cmd):
    return subprocess.run(_adb_prefix() + ["shell", cmd],
                          capture_output=True, text=True, timeout=20).stdout.strip()


def size_of(path):
    out = sh("stat -c %%s '%s' 2>/dev/null" % path)
    return int(out) if out.isdigit() else 0


def read_head(path, n=65536):
    """取文件前 n 字节（用 dd 避免把大文件整读）"""
    r = subprocess.run(_adb_prefix() + ["exec-out", "dd if='%s' bs=4096 count=%d 2>/dev/null"
                        % (path, n // 4096)], capture_output=True)
    return r.stdout


def find_mdat(head):
    i = 0
    while i + 8 <= len(head):
        size, typ = struct.unpack(">I4s", head[i:i + 8])
        if typ == b"mdat":
            return i + 8
        if size < 8:
            return None
        i += size
    return None


def parse_gof(p, i=0):
    """把 p[i:] 当作 GOF 锚点解析：SPS + PPS + IDR。

    不写死长度（不同段/不同档位的 SPS 长度会变），只要求：
      · 第一项 type=7(SPS)，长度 8~64
      · 第二项 type=8(PPS)，长度 4~32
      · 第三项 type=5(IDR)，长度 2KB~400KB
    返回 (三个 AnnexB blob, 总消耗字节) 或 (None, 0)
    """
    if i + 16 > len(p):
        return None, 0
    try:
        l1 = struct.unpack(">I", bytes(p[i:i + 4]))[0]
        if not (8 <= l1 <= 64) or (p[i + 4] & 0x1F) != 7:
            return None, 0
        q = i + 4 + l1
        l2 = struct.unpack(">I", bytes(p[q:q + 4]))[0]
        if not (4 <= l2 <= 32) or (p[q + 4] & 0x1F) != 8:
            return None, 0
        q2 = q + 4 + l2
        l3 = struct.unpack(">I", bytes(p[q2:q2 + 4]))[0]
        if not (MIN_LEN < l3 < MAX_LEN) or (p[q2 + 4] & 0x1F) != 5:
            return None, 0
    except Exception:
        return None, 0
    total = (q2 + 4 + l3) - i
    if i + total > len(p):
        return None, 0
    blobs = [START + bytes(p[i + 4:i + 4 + l1]),
             START + bytes(p[q + 4:q + 4 + l2]),
             START + bytes(p[q2 + 4:q2 + 4 + l3])]
    return blobs, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--secs", type=int, default=30)
    ap.add_argument("--scale", type=int, default=960)
    ap.add_argument("--save-every", type=int, default=0, dest="save_every")
    ap.add_argument("--save-dir", default="samples/rec/live4", dest="save_dir")
    ap.add_argument("--back", type=int, default=524288, help="从写入点往前读多少字节（越小延迟越低）")
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--dump", default="", help="把实际喂给 ffmpeg 的字节另存一份，便于离线复盘")
    args = ap.parse_args()
    dump = open(args.dump, "wb") if args.dump else None
    if args.file.lower() == "auto":
        # 取 DCIM 下最新的 .LRF（正在写的那一个）
        out = sh("ls -t /mnt/media_rw/emulated/DCIM/CAM_001/*.LRF 2>/dev/null | head -1")
        if not out:
            print("[v4] 没有找到 .LRF，先让相机开录")
            return
        args.file = out.strip()
        print("[v4] 自动选择 %s" % args.file)

    # mdat 起点
    mdat = None
    for _ in range(40):
        head = read_head(args.file)
        mdat = find_mdat(head)
        if mdat:
            break
        time.sleep(0.25)
    if not mdat:
        print("[v4] 找不到 mdat")
        return
    print("[v4] mdat @%d" % mdat, flush=True)

    sz0 = size_of(args.file)
    start = max(mdat, sz0 - args.back)
    print("[v4] 文件 %.1fMB，从 %d 起" % (sz0 / 1048576.0, start), flush=True)
    sh("ls -l %s 2>/dev/null | head -1" % TAILER)

    out_w = args.scale
    out_h = (out_w // 2) // 2 * 2
    ff = subprocess.Popen(
        [FFMPEG, "-hide_banner", "-loglevel", "error",
         "-probesize", "32", "-analyzeduration", "0",   # ★不要用 -fflags nobuffer：
         #   实测在管道输入下会把 99% 的包丢掉（425 帧 → 2 帧），且不出任何报错
         "-f", "h264", "-i", "pipe:0",
         "-vf", "scale=%d:%d" % (out_w, out_h),
         "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)

    rx = bytearray()
    lock = threading.Lock()
    stop = [False]

    def reader():
        while not stop[0]:
            try:
                b = ff.stdout.read(6144)
            except Exception:
                break
            if not b:
                break
            with lock:
                rx.extend(b)

    threading.Thread(target=reader, daemon=True).start()

    proc = subprocess.Popen(_adb_prefix() + ["exec-out",
                             "python3 -u %s '%s' %d" % (TAILER, args.file, start)],
                            stdout=subprocess.PIPE, bufsize=0)

    if args.save_every:
        os.makedirs(args.save_dir, exist_ok=True)
        import cv2
        import numpy as np

    fb = out_w * out_h * 3
    t0 = last = time.time()
    frames = saved = 0
    fed_nals = 0
    buf = bytearray()
    primed = [False]        # 是否已见过第一个 GOF（见过才允许开始喂）
    win = not args.no_window

    def pump():
        while not stop[0]:
            b = proc.stdout.read(262144)
            if not b:
                time.sleep(0.02)
                continue
            buf.extend(b)
            if len(buf) > 8 * 1048576:          # 防内存堆积
                del buf[:len(buf) - 6 * 1048576]

    threading.Thread(target=pump, daemon=True).start()

    try:
        while time.time() - t0 < args.secs:
            # ---- 取帧显示 ----
            with lock:
                while len(rx) >= fb:
                    f = bytes(rx[:fb])
                    del rx[:fb]
                    frames += 1
                    if args.save_every and frames % args.save_every == 0:
                        img = np.frombuffer(f, np.uint8).reshape(out_h, out_w, 3)
                        fn = os.path.join(args.save_dir, "f%04d.png" % frames)
                        cv2.imwrite(fn, img)
                        saved += 1
                    if win:
                        img = np.frombuffer(f, np.uint8).reshape(out_h, out_w, 3)
                        cv2.imshow("osmo360 pano (LRF live)", img)
                        cv2.waitKey(1)

            # ---- 喂 NAL ----
            fed = 0
            while fed < 6 * 262144 and len(buf) > 64:
                if not primed[0]:
                    # 等第一个 GOF 锚点：扫描 "SPS + PPS + IDR" 三连
                    found = False
                    limit = min(len(buf), 4 * 1048576)
                    for k in range(max(0, limit - 40)):
                        blobs, used = parse_gof(buf, k)
                        if blobs:
                            del buf[:k]
                            found = True
                            break
                    if not found:
                        if len(buf) > 2 * 1048576:
                            del buf[:1048576]      # 太长了还没锚点，丢一半继续找
                        break
                    primed[0] = True
                blobs, used = parse_gof(buf, 0)
                if blobs:
                    try:
                        ff.stdin.write(b"".join(blobs))
                        if dump:
                            dump.write(b"".join(blobs)); dump.flush()
                        ff.stdin.flush()
                        fed_nals += 3
                        fed += used
                    except Exception as e:
                        print("[v4] 写管道失败：%s" % e)
                        stop[0] = True
                        break
                    del buf[:used]
                    continue
                if len(buf) < 8:
                    break
                ln = struct.unpack(">I", bytes(buf[:4]))[0]
                t = buf[4] & 0x1F
                if MIN_LEN < ln < MAX_LEN and t in OK_TYPES and 4 + ln <= len(buf):
                    try:
                        ff.stdin.write(START + bytes(buf[4:4 + ln]))
                        if dump:
                            dump.write(START + bytes(buf[4:4 + ln])); dump.flush()
                        ff.stdin.flush()
                    except Exception as e:
                        print("[v4] 写管道失败：%s" % e)
                        stop[0] = True
                        break
                    fed_nals += 1
                    fed += 4 + ln
                    del buf[:4 + ln]
                else:
                    del buf[:1]          # 跳过 proto 区

            if time.time() - last >= 1.0:
                last = time.time()
                print("[v4] %.1fs frame=%d fps=%.1f nals=%d buf=%dKB"
                      % (time.time() - t0, frames, frames / max(1e-6, time.time() - t0),
                         fed_nals, len(buf) // 1024), flush=True)
    finally:
        stop[0] = True
        try:
            proc.kill()
        except Exception:
            pass
        try:
            ff.stdin.close()
        except Exception:
            pass
        time.sleep(0.2)
        try:
            ff.kill()
        except Exception:
            pass
        if args.save_every:
            print("[v4] saved %d 张 → %s" % (saved, args.save_dir))


if __name__ == "__main__":
    main()
