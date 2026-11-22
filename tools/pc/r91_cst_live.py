# -*- coding: utf-8 -*-
"""r91 —— 用相机自带测试工具 dji_cst 直接"拉起 liveview"，看能不能救回视频通路。

背景（第十一轮）：
  相机冷启动后 dji_camera3 每秒一次 `subscribe_module(0x0100) failed, ret:0xe0`，
  我们的回放只能拿到音频、拿不到视频。0x0100 是 App host（见 doc98），
  而 /system/etc/dji_media_service.json 里媒体服务默认 `uvc_only`、liveview 不被支持。

而 /system/etc/cst_cases.json（相机自带的用例表）里有 24 条 liveview 序列，例如
  duss_liveview_6000x3000_pano_video_p25  = [SetModeProfile VIDEO_PANO, SetVideoFmt 3000P P25, ...]
这些序列是**相机自己的 DUSS 用例**，可以用 dji_cst 跑（需要 LD_PRELOAD=libdcam_hook.so）。
本工具就是跑它，同时盯着：① 我们客户端收到多少帧；② 相机还报不报 no_app_host。

用法：
  python tools/pc/r91_cst_live.py                 # 起我们的回放 + 跑 cst 序列
  env CASE=duss_liveview_3840x1920_pano_video_p100
  env HOLD=45        # cst 里 -L liveview 持续秒数
  env NOSERVER=1     # 只跑 cst，不起我们的回放（对照）
  env SECS=45
"""
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from osmo360 import live as live_mod

ADB = r"E:\123pan\Downloads\DJIAc5P_UI_Debian-master\DJIAc5P_UI_Debian-master\adb\adb.exe"
SER = "123456789ABCDEF"
CASE = os.environ.get("CASE", "duss_liveview_6000x3000_pano_video_p25")
HOLD = int(os.environ.get("HOLD", "45"))
SECS = float(os.environ.get("SECS", "45"))
NOSERVER = os.environ.get("NOSERVER", "") not in ("", "0")

RENV = ("LIVE_CUT=0 LIVE_LOOP_TO=54.0 LIVE_CYCLE_FROM=6.30 LIVE_CYCLE_TO=54.0 "
        "LIVE_RESTAMP=1 LIVE_TREQ=1 LIVE_TREQ_HZ=10 LIVE_LOCK_BASE=0 LIVE_SEED=0 "
        "LIVE_APPMODE=0 LIVE_ACK=1 LIVE_ACK_MODE=clk LIVE_FORCE=0 LIVE_REPAIR=0 "
        "LIVE_ASM=pair")


def adb(*a, t=120):
    r = subprocess.run([ADB, "-s", SER] + list(a), capture_output=True, text=True, timeout=t)
    return (r.stdout + r.stderr).strip()


def count(pat):
    raw = adb("shell", "grep -c '%s' /blackbox/camera/log/cam.log" % pat)
    m = re.search(r"\d+", raw)
    return int(m.group(0)) if m else 0


def parse(stat, key):
    m = re.search(r"%s=(\d+)" % key, stat)
    return int(m.group(1)) if m else 0


sub0 = count("subscribe_module(0x0100) failed")
lv0 = count("liveview")

if not NOSERVER:
    adb("shell", "pkill -f 'live_serve[r].py'")
    adb("shell", "cd /data/local/tmp && setsid env %s python3 -u live_server.py "
                 "/dev/shm/up9004.bin > /dev/shm/live_server.log 2>&1 < /dev/null & echo ok" % RENV)
    time.sleep(8)                     # 让回放把 app 模式/订阅那一段放完

# 跑 cst 序列（自带 hook；-m 表示 sequence case，-L 让 liveview 保持 HOLD 秒）
adb("shell", "cd /data/local/tmp && setsid env LD_LIBRARY_PATH=/usr/lib LD_PRELOAD=libdcam_hook.so "
             "dji_cst -m %s -L %d -l /dev/shm/cst_lv.log > /dev/shm/cst_out.log 2>&1 "
             "< /dev/null & echo ok" % (CASE, HOLD))

L = live_mod.LiveStream()
for p in (5010, 5011, 5012):
    L.adb.forward(p, p)
time.sleep(0.5)
s0 = L.stat()
f0 = parse(s0, "frames")
t0 = time.monotonic()
last = f0
while time.monotonic() - t0 < SECS:
    time.sleep(5)
    s = L.stat()
    f = parse(s, "frames")
    print("  +%2.0fs frames=%d (+%d) big=%s idr=%s vclients=%s" % (
        time.monotonic() - t0, f, f - last, parse(s, "big"), parse(s, "idr"),
        parse(s, "vclients")), flush=True)
    last = f
s1 = L.stat()
sub1 = count("subscribe_module(0x0100) failed")
lv1 = count("liveview")
print("[r91] %s | frames +%d / %.0fs = %.1f fps | no_app_host +%d | liveview 相关行 +%d" % (
    CASE, parse(s1, "frames") - f0, SECS, (parse(s1, "frames") - f0) / SECS, sub1 - sub0, lv1 - lv0),
    flush=True)
print("     末态: %s" % s1, flush=True)
print("     cst 输出尾: %s" % adb("shell", "tail -n 6 /dev/shm/cst_out.log")[-800:], flush=True)
