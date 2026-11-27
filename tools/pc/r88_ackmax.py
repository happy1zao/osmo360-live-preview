# -*- coding: utf-8 -*-
"""r88 —— 第十轮配置下的 ack 参数 A/B 台（每档跑 SECS 秒，量真实 fps 与 check 自激速率）。

背景：r87 的取证显示，稳定期的每一帧其实**片全齐**（14+13/14，无 gap），
但相机只发 ~160 个视频大包/秒 ≈ 6fps，同时它自己在 86Hz 地猛发 35B stream-check。
也就是说瓶颈不在拼帧，而在相机侧的发送节奏。本工具用来量 ack 限速 / treq 对各档 fps 的影响。

用法：python tools/pc/r88_ackmax.py
  env SECS=每档秒数（默认 60）
  env ARMS="名字=额外env;名字=额外env"（默认内置四档 ack 变体）
"""
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from osmo360 import live as live_mod

ADB = os.environ.get("ADB") or shutil.which("adb") or "adb"
SER = os.environ.get("ADB_SERIAL")  # None = single device, omit -s
SECS = float(os.environ.get("SECS", "60"))

BASE_ENV = ("LIVE_CUT=0 LIVE_LOOP_TO=54.0 LIVE_CYCLE_FROM=6.30 LIVE_CYCLE_TO=54.0 "
            "LIVE_RESTAMP=1 LIVE_TREQ=1 LIVE_TREQ_HZ=10 LIVE_LOCK_BASE=0 LIVE_SEED=0 "
            "LIVE_APPMODE=0 LIVE_ACK=1 LIVE_FORCE=0 LIVE_REPAIR=0 LIVE_ASM=pair")

ARMS = [
    ("clk/无限制(现状)", "LIVE_ACK_MODE=clk LIVE_ACK_MAX=0"),
    ("clk/限速8Hz", "LIVE_ACK_MODE=clk LIVE_ACK_MAX=8"),
    ("clk/限速3Hz", "LIVE_ACK_MODE=clk LIVE_ACK_MAX=3"),
    ("dup/限速8Hz", "LIVE_ACK_MODE=dup LIVE_ACK_MAX=8"),
]
if os.environ.get("ARMS"):
    ARMS = [(x.split("=", 1)[0], x.split("=", 1)[1]) for x in os.environ["ARMS"].split(";")]


def adb(*a, t=60):
    prefix = [ADB] + (["-s", SER] if SER else [])
    r = subprocess.run(prefix + list(a), capture_output=True, text=True, timeout=t)
    return (r.stdout + r.stderr).strip()


def parse(stat, key, default=0):
    m = re.search(r"%s=(\d+)" % key, stat)
    return int(m.group(1)) if m else default


def run_one(name, extra):
    adb("shell", "pkill -f 'live_serve[r].py'")
    adb("shell", "systemctl restart dji_sw_uav")
    time.sleep(3)
    adb("shell", "cd /data/local/tmp && setsid env %s %s python3 -u live_server.py "
                 "/dev/shm/up9004.bin > /dev/shm/live_server.log 2>&1 < /dev/null & echo ok"
                 % (BASE_ENV, extra))
    time.sleep(1)
    adb("shell", "systemctl restart dji_camera3")
    time.sleep(12)                       # 让编码器重建 + 首帧 IDR 落地
    L = live_mod.LiveStream()
    for p in (5010, 5011, 5012):
        L.adb.forward(p, p)
    time.sleep(0.5)
    s0 = L.stat()
    f0, b0, a0 = parse(s0, "frames"), parse(s0, "big"), parse(s0, "ack")
    time.sleep(SECS)
    s1 = L.stat()
    f1, b1, a1 = parse(s1, "frames"), parse(s1, "big"), parse(s1, "ack")
    print("[%s] frames %d -> %d (+%d, %.1f fps)  big +%d (%.1f MB)  ack +%d (%.1f/s)" % (
        name, f0, f1, f1 - f0, (f1 - f0) / SECS, b1 - b0,
        (b1 - b0) * 1472 / 1e6, a1 - a0, (a1 - a0) / SECS), flush=True)
    print("     末态: %s" % s1, flush=True)


for nm, ex in ARMS:
    try:
        run_one(nm, ex)
    except Exception as e:
        print("[%s] 失败 %s" % (nm, e), flush=True)
