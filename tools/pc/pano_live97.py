#!/usr/bin/env python3
"""pano_live97.py —— Osmo 360（一代）2:1 全景 2048×1024 实时预览启动器（零落盘 / 不读 LRF）

原理（本轮实测结论）
--------------------
1) 相机内部的 proxy 编码器（gst 管线 PARSE #9：appsrc name=proxy_yuv_src ->
   djicamc2venc name=proxy_encoder -> capsfilter name=proxy_enc_config）就是
   「2:1 代理流」的唯一来源，输出 2048×1024 H.264。
2) 钩子 probe97.so 做两件事：
   a. 把这条管线出口 tee 成三路，其中一路 h264parse -> tcpserversink 127.0.0.1:8888
      （h264parse config-interval=-1 把 AVCC 转成带 SPS/PPS 的 AnnexB，ffmpeg 才认）；
   b. 把 .OSV/.LRF/.MET/.trinf 的 open 全部改到 /data/local/tmp/spool 并假写
      （数据丢弃、只维护逻辑大小）→ 卡上文件恒为 0 字节，物理上不落盘。
3) 2:1 流只在「录制会话」内产生，所以本工具会起一段长录会话
   （pano_hold_cases.json 的 pano_hold_record：VIDEO_PANO + 1X1_1920P + P120，
     Record.wait_stop_ms=3600000），会话本身不写卡。

踩过的坑（务必保留）
--------------------
* 只要让 .LRF 真的写到任何地方（哪怕换成 /dev/shm、哪怕和 .OSV 同一个 tmpfs），
  录制都会在 ~1.7s 被相机自己掐停（probe93/95/96 三次 A/B/C 对照已证）。
  所以本方案 LRF 也一律假写，2:1 全部从管线 tee 出来，文件层完全不碰。
* storage-service 在开机时若自己没挂上 /mnt/media_rw/emulated，会把
  emulated 标成 state=6(ERROR)，cst 看到的 storage[0] 就是 status 13 / free 0，
  任何录制都 -1002，随后 topmode 断言崩溃。修复办法：先停服务、umount，
  再重启 storage_service，让它自己挂上（state 2）。本工具自动做这个体检+自愈。

用法
----
    python pano_live97.py                 # 体检 + 起会话 + ffplay 实时预览
    python pano_live97.py --once          # 只跑一段（窗口关掉即退出）
    python pano_live97.py --dump 8        # 不起窗口，直接落 8 张 PNG 到 samples\\rec\\live
    python pano_live97.py --stop          # 只停会话
"""
import argparse
import os
import subprocess
import sys
import time

ADB = r"E:\123pan\Downloads\DJIAc5P_UI_Debian-master\DJIAc5P_UI_Debian-master\adb\adb.exe"
SERIAL = "123456789ABCDEF"
HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.normpath(os.path.join(HERE, "..", "..", "bin", "ffmpeg-master-latest-win64-gpl-shared", "bin"))
FFPLAY = os.path.join(BIN, "ffplay.exe")
FFMPEG = os.path.join(BIN, "ffmpeg.exe")
HOOK_LOCAL = os.path.normpath(os.path.join(HERE, "..", "..", "probe", "probe97.so"))
HOOK_REMOTE = "/usr/lib/libdcam_hook.so"
PORT_LOCAL, PORT_DEV = 18888, 8888
CASE_FILE = "/data/local/tmp/pano_hold_cases.json"
CASE_NAME = "pano_hold_record"


def adb(*args, timeout=40, check=False):
    r = subprocess.run([ADB, "-s", SERIAL, *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {args} -> {r.returncode}: {r.stderr.strip()}")
    return r


def sh(cmd, timeout=40):
    return adb("shell", cmd, timeout=timeout).stdout


# ---------------------------------------------------------------- 存储体检/自愈
def heal_storage():
    """emulated 的 storage-service 状态必须是 2（mounted）。不是就 umount 让它自己重挂。

    关键：必须先停 camera3 / media_server 再 umount，否则 umount 会 EBUSY，
    storage-service 随后会对「仍然挂载着」的分区跑 fsck → fsck 失败 → state=6，
    于是 cst 看到 storage[0] status 13 / free 0，任何录制都 -1002。
    """
    states = sh("grep -a 'update_storage_state: id = emulated' /blackbox/system/system.log | tail -1")
    state = states.split("new state = ")[-1].strip() if "new state = " in states else None
    print(f"[storage] emulated state={state}")
    if state == "2":
        return True
    print("[storage] 状态异常，执行自愈：停服务 -> umount -> 重启 storage_service")
    sh("for p in $(pgrep -f 'dji_cs''t'); do kill $p; done; "
       "systemctl stop dji_camera3.service; systemctl stop dji_media_server.service 2>/dev/null; sleep 4; "
       "sync; umount /mnt/media_rw/emulated; "
       "platform=$(mountpoint -q /mnt/media_rw/emulated && echo STILL || echo OK); echo umount:$platform; "
       "systemctl restart storage_service.service; sleep 10", timeout=90)
    sh("systemctl start dji_media_server.service 2>/dev/null; "
       "systemctl reset-failed dji_camera3.service; systemctl restart dji_camera3.service")
    time.sleep(14)
    states = sh("grep -a 'update_storage_state: id = emulated' /blackbox/system/system.log | tail -1")
    print(f"[storage] after: {states.strip()}")
    return "new state = 2" in states


# ---------------------------------------------------------------- 钩子部署
def ensure_hook():
    """md5 一致就跳过；不一致则把本地 probe97.so 推进去（需要 remount rw）。"""
    if not os.path.isfile(HOOK_LOCAL):
        print(f"[hook] 本地 {HOOK_LOCAL} 不存在，跳过部署（假定设备上已是 probe97）")
        return
    import hashlib
    with open(HOOK_LOCAL, "rb") as fh:
        md5_local = hashlib.md5(fh.read()).hexdigest()
    md5_remote = (sh(f"md5sum {HOOK_REMOTE}").split() or [""])[0]
    if md5_local and md5_local == md5_remote:
        print("[hook] probe97 已在位")
        return
    print("[hook] 部署 probe97")
    adb("push", HOOK_LOCAL, "/data/local/tmp/probe97.so", check=True)
    sh(f"mount -o remount,rw /; cp -f /data/local/tmp/probe97.so {HOOK_REMOTE}; "
       f"mount -o remount,ro /; systemctl reset-failed dji_camera3.service; "
       f"systemctl restart dji_camera3.service")
    time.sleep(14)


def camera_alive():
    return "active" in sh("systemctl is-active dji_camera3.service")


# ---------------------------------------------------------------- 会话控制
def start_session():
    sh("for p in $(pgrep -f 'dji_cs''t'); do kill $p; done")
    time.sleep(1)
    sh(f"cd /usr/bin && LD_PRELOAD={HOOK_REMOTE} setsid nohup ./dji_cst "
       f"--cases-file {CASE_FILE} -m {CASE_NAME} > /dev/shm/cst_live97.log 2>&1 < /dev/null &")
    print("[session] 已起长录会话（不写卡）")


def stop_session():
    sh("for p in $(pgrep -f 'dji_cs''t'); do kill $p; done")
    print("[session] 已停止")


def wait_stream(timeout=25):
    """等 proxy 分片重新开始增长，说明管线已出流。"""
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        cur = sh("ls -l /dev/shm/dcam_rec 2>/dev/null | tail -1")
        if cur and cur != last and "proxy_" in cur:
            return True
        last = cur
        time.sleep(1)
    return False


# ---------------------------------------------------------------- 播放/取证
def ensure_forward():
    adb("forward", "--remove-all")
    adb("forward", f"tcp:{PORT_LOCAL}", f"tcp:{PORT_DEV}")
    print(f"[tcp] adb forward {PORT_LOCAL} -> {PORT_DEV}")


def drop_forward():
    adb("forward", "--remove-all")


def play():
    cmd = [FFPLAY, "-f", "h264", "-probesize", "1000000", "-analyzeduration", "1000000",
           "-window_title", "Osmo360_PanoLive_2048x1024",
           "-fflags", "nobuffer", "-flags", "low_delay",
           f"tcp://127.0.0.1:{PORT_LOCAL}"]
    return subprocess.run(cmd).returncode


def dump_frames(n, outdir, tries=3):
    """连上去时可能正好落在 GOP 中间（报 non-existing PPS）；h264parse 的
    config-interval=-1 会在下一个 IDR 前补发 SPS/PPS，所以把探测窗口放长一点，
    失败就重建 forward 重试。"""
    os.makedirs(outdir, exist_ok=True)
    for i in range(1, tries + 1):
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "h264", "-probesize", "8000000", "-analyzeduration", "20000000",
               "-i", f"tcp://127.0.0.1:{PORT_LOCAL}",
               "-frames:v", str(n), os.path.join(outdir, "live_%02d.png")]
        try:
            subprocess.run(cmd, timeout=120)
        except subprocess.TimeoutExpired:
            print(f"[dump] 第 {i} 次超时")
        got = sorted(f for f in os.listdir(outdir) if f.startswith("live_") and f.endswith(".png"))
        if got:
            print(f"[dump] {len(got)} 帧 -> {outdir}")
            return True
        print(f"[dump] 第 {i} 次没解出帧，重建 forward 重试")
        for f in os.listdir(outdir):
            if f.startswith("live_"):
                os.remove(os.path.join(outdir, f))
        ensure_forward()
        time.sleep(3)
    print("[dump] 失败：没解出帧")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dump", type=int, default=0, help="不起窗口，落 N 张 PNG")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--no-heal", action="store_true")
    args = ap.parse_args()

    if args.stop:
        stop_session(); drop_forward(); return

    if not args.no_heal:
        heal_storage()
    if not camera_alive():
        sh("systemctl reset-failed dji_camera3.service; systemctl restart dji_camera3.service")
        time.sleep(14)
    ensure_hook()
    ensure_forward()
    start_session()
    if not wait_stream():
        sys.exit("[!] 等不到 proxy 流，检查 camera3 状态与 /dev/shm/dcam_rec")
    print("[stream] proxy 2:1 已出流")

    try:
        if args.dump:
            dump_frames(args.dump, os.path.normpath(os.path.join(HERE, "..", "..", "samples", "rec", "live")))
        else:
            play()
            if not args.once:
                print("[hint] 窗口关闭即停会话；再跑一次即重新开流")
    finally:
        stop_session()
        drop_forward()


if __name__ == "__main__":
    main()
