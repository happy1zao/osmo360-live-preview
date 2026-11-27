"""r148_pano_preview.py —— 一键：让相机持续开录 + 实时预览它正在写的 2:1 全景流。

流程：
  1) 设备端推 lrf_tail.py；
  2) 用 `dji_cst -m duss_record_...` 起录，**10 秒内杀掉 cst**——
     因为这条 case 自带的 Record 块会在 sleep 结束后主动发 stop，
     杀掉进程反而让相机"没人叫停"，于是一直录下去（实测录了 615 秒才自行收尾）；
  3) 找最新的 *.LRF；
  4) 交给 r147_lrf_live.py 做常驻跟随 + SPS 预注入 + IDR 对齐 + 解码显示。

环境变量：ADB / ADB_SERIAL（缺省走 PATH、单设备）。
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

ADB = os.environ.get("ADB") or shutil.which("adb") or "adb"
SERIAL = os.environ.get("ADB_SERIAL")
HERE = os.path.dirname(os.path.abspath(__file__))
REC = os.path.join(HERE, "..", "..", "probe", "lrf_tail.py")
LIVE = os.path.join(HERE, "r147_lrf_live.py")
DCIM = "/mnt/media_rw/emulated/DCIM/CAM_001"
CASE = "duss_record_3840x1920_video_pano_p120_mp4"


def _adb_prefix():
    return [ADB] + (["-s", SERIAL] if SERIAL else [])


def sh(cmd, t=20):
    p = subprocess.run(_adb_prefix() + ["shell", cmd], stdout=subprocess.PIPE, timeout=t)
    return p.stdout.decode("utf8", "ignore").strip()


def ensure_tailer():
    out = sh("ls -l /data/local/tmp/lrf_tail.py 2>/dev/null")
    if "lrf_tail.py" not in out:
        subprocess.run(_adb_prefix() + ["push", REC, "/data/local/tmp/lrf_tail.py"], check=False)
        print("[wrap] 已推 lrf_tail.py")


def start_record():
    """起录后杀掉 cst，让相机不停机。"""
    sh("cd /usr/bin && setsid env LD_PRELOAD=/usr/lib/libdcam_hook.so ./dji_cst -m %s "
       ">/dev/shm/cst_auto.log 2>&1 < /dev/null & echo started" % CASE)
    time.sleep(10)
    sh("pkill -f dji_cs[t] ; echo killed")
    time.sleep(2)
    st = sh("grep -a 'record_status_change' /dev/shm/cst_auto.log | tail -1")
    print("[wrap] 录制状态：%s" % st)


def newest_lrf():
    out = sh("ls -t %s/*.LRF 2>/dev/null | head -1" % DCIM)
    return out.splitlines()[0].strip() if out else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="")
    ap.add_argument("--sps", required=True, help="传给 r147 的 SPS/PPS 片段（见 r147 --sps）")
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--secs", type=int, default=60)
    ap.add_argument("--scale", type=int, default=960)
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--save-every", type=int, default=0)
    args = ap.parse_args()

    ensure_tailer()
    if not args.no_record:
        start_record()

    path = args.file or newest_lrf()
    if not path:
        print("[wrap] 找不到 LRF 文件")
        return
    print("[wrap] 跟随文件：%s" % path)

    cmd = [sys.executable, LIVE, "--file", path, "--sps", args.sps,
           "--secs", str(args.secs), "--scale", str(args.scale)]
    if args.no_window:
        cmd.append("--no-window")
    if args.save_every:
        cmd += ["--save-every", str(args.save_every)]
    subprocess.run(cmd)


if __name__ == "__main__":
    main()
