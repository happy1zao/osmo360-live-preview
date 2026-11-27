#!/usr/bin/env python3
"""h265_push.py —— 相机端：把 /dev/shm/lv 里滚动的 H.265 分片按序推到 stdout

为什么按 mtime 而不是按文件名序号：
  相机端 camera3 每次重启，multifilesink 的序号会从 0 重来，
  而目录里可能残留上一轮的大序号老文件；按序号取最新会永久卡在老文件上。

规则：
  1) 按 mtime_ns 排序，只推比上次推过的更新的片；
  2) 永远跳过 mtime 最新的一片（可能正在写）；
  3) 必须从带 VPS 的片开始推，否则 ffmpeg 起解报 PPS id out of range；
  4) 若发现要推的片已被环形缓冲覆盖（间隔过大），重新等下一个 IDR。
"""
import os
import sys
import time

D = "/dev/shm/lv"
SLEEP = 0.02
VPS = bytes.fromhex("000000014001")


def scan():
    try:
        names = os.listdir(D)
    except OSError:
        return []
    out = []
    for n in names:
        if n.startswith("hevc_") and n.endswith(".h265"):
            p = os.path.join(D, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append((st.st_mtime_ns, n, st.st_size))
    out.sort()
    return out


def main():
    out = sys.stdout.buffer
    last_mt = None
    synced = False
    while True:
        files = scan()
        if len(files) < 2:
            time.sleep(SLEEP)
            continue
        todo = files[:-1]
        if last_mt is not None:
            todo = [f for f in todo if f[0] > last_mt]
        if not todo:
            time.sleep(SLEEP)
            continue
        for mt, name, size in todo:
            try:
                with open(os.path.join(D, name), "rb") as f:
                    data = f.read()
            except OSError:
                continue
            last_mt = mt
            if not data:
                continue
            if not synced:
                if VPS in data:
                    synced = True
                else:
                    continue
            out.write(data)
            out.flush()
        time.sleep(SLEEP)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
