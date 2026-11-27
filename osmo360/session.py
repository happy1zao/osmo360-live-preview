#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""session —— 脱手机自建会话：设备端一键「连接中重启拿 IDR」实验/预览

以下原理与前置条件来自早期逆向轮次记录（历史结论，未在本仓库环境复核）：
  * 编码器只在实例创建第一帧吐一次 SPS+PPS+IDR（idr_interval=1000000）。
  * 先让会话连上（lv_fix 重放握手 + 周期请求），再重启相机编码器进程，
    让实例在有消费者在场时重建，那唯一一次 IDR 落进抓包。
  * 相机屏幕需处于录像/拍照取景界面；停在设置页/QS 页时
    transmit 层不路由视频流（历史记录结论）。

安全边界：
  * 只 restart dji_sw_uav / dji_camera3（局部、自动恢复）
  * 绝不 restart dji_media（整机挂死）；绝不 reboot

TODO(设备端脚本未随仓库发布)：本模块的 capture_idr / capture_selfhost
依赖设备端 `exp_idr.sh`、`replay_up.py`、`expI_final.sh`，这些脚本不在
本仓库内（见 probe/README.md“未随仓库发布的设备端脚本”）。模块可以正常
import；只有真正调用上述捕获方法时才要求设备上已存在对应脚本。本地离线
解码路径不经过本模块。
"""
import os, time

from .transport import Adb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXP_SCRIPT = os.path.join(ROOT, "probe", "exp_idr.sh")

DEFAULT_PCAP = "/dev/shm/mimo.pcap"
AE2D_PCAP = "/dev/shm/ae2d.pcap"
GATE_OFF = "/data/local/tmp/gate_off.py"
EXP_REMOTE = "/data/local/tmp/exp_idr.sh"
REPLAY_REMOTE = "/data/local/tmp/replay_up.py"
EXP2_REMOTE = "/data/local/tmp/expI_final.sh"
# TODO: 以上三个设备端脚本（exp_idr.sh / replay_up.py / expI_final.sh）
# 未随仓库发布；以下捕获方法仅在自备脚本后可运行。


class DevSession:
    """设备端会话与 IDR 捕获。所有多步 adb 操作在同一 Python 进程内完成。"""

    def __init__(self, adb=None):
        self.adb = adb or Adb()

    # ---------- 前置 ----------
    def check_device(self):
        rc, out = self.adb.devices()
        return self.adb.serial in out and "\tdevice" in out

    def gate_patch(self):
        """重打 12 秒闸门补丁（每个新 boot 都要，只改内存）。"""
        rc, out = self.adb.sh("python3 %s 2>&1 | tail -2" % GATE_OFF, timeout=30)
        return out.strip()

    def push_handshake(self, local_pcap, remote=DEFAULT_PCAP):
        rc, out = self.adb.push(local_pcap, remote, timeout=300)
        return rc == 0

    def push_script(self, local=EXP_SCRIPT, remote=EXP_REMOTE):
        rc, out = self.adb.push(local, remote, timeout=60)
        return rc == 0

    # ---------- 捕获 ----------
    def capture_idr(self, local_pcap, out_pcap, secs=40, kick_delay=6, progress=None):
        """一键捕获：gate → push → restart dji_sw_uav → 会话 → kick → 取回 pcap。

        返回本地 pcap 路径；失败返回 None。progress(可选): 回调设备端进度日志。
        TODO: 依赖未发布的 exp_idr.sh，见模块头注释。
        """
        if not self.check_device():
            raise RuntimeError("相机不在线（adb devices 无 %s）" % self.adb.serial)
        self.gate_patch()
        self.push_script()
        self.push_handshake(local_pcap)
        remote_out = "/dev/shm/exp_%d.pcap" % int(time.time())
        self.adb.sh("rm -f /dev/shm/exp_done /dev/shm/exp.log %s" % remote_out)
        self.adb.sh("setsid sh %s %s %s %d 40 >/dev/null 2>&1 </dev/null & echo started"
                    % (EXP_REMOTE, DEFAULT_PCAP, remote_out, kick_delay))
        deadline = time.time() + secs + 45
        while time.time() < deadline:
            time.sleep(5)
            rc, out = self.adb.sh("ls /dev/shm/exp_done 2>/dev/null", timeout=15)
            if "exp_done" in out:
                break
            if progress:
                _, log = self.adb.sh("cat /dev/shm/exp.log 2>/dev/null", timeout=15)
                for line in (log or "").splitlines()[-1:]:
                    progress(line)
        rc, _ = self.adb.sh("pkill -f 'tcpdump -i lo' 2>/dev/null; pkill -f lv_fix 2>/dev/null")
        time.sleep(1)
        rc, out = self.adb.pull(remote_out, out_pcap, timeout=300)
        if rc != 0:
            return None
        return out_pcap

    def fx_stat(self):
        """读取最近一次实验的 lv_fix 统计尾部（big>0 才说明有视频大包）。"""
        _, out = self.adb.sh("tail -4 /dev/shm/fx.log 2>/dev/null", timeout=15)
        return out.strip()

    # ---------- 脱手机自建会话 ----------
    def capture_selfhost(self, handshake_pcap, out_pcap, secs=55):
        """完整上行重放 + 连接中重启，让相机把流推给我们的本机会话。

        握手样本必须是包含握手后全部 DUML 配置/注册/保活长命令的冷启动抓包。
        返回本地 pcap 路径；失败返回 None。
        TODO: 依赖未发布的 replay_up.py / expI_final.sh，见模块头注释。
        """
        if not self.check_device():
            raise RuntimeError("相机不在线（adb devices 无 %s）" % self.adb.serial)
        self.gate_patch()
        self.adb.push(os.path.join(ROOT, "probe", "replay_up.py"), REPLAY_REMOTE)
        self.adb.push(os.path.join(ROOT, "probe", "expI_final.sh"), EXP2_REMOTE)
        self.adb.push(handshake_pcap, AE2D_PCAP, timeout=300)
        remote_out = "/dev/shm/selfhost_%d.pcap" % int(time.time())
        self.adb.sh("rm -f /dev/shm/expI_done /dev/shm/expI.log %s" % remote_out)
        self.adb.sh("setsid sh %s %s %s %d >/dev/null 2>&1 </dev/null & echo started"
                    % (EXP2_REMOTE, AE2D_PCAP, remote_out, secs))
        deadline = time.time() + secs + 45
        while time.time() < deadline:
            time.sleep(5)
            _, out = self.adb.sh("ls /dev/shm/expI_done 2>/dev/null", timeout=15)
            if "expI_done" in out:
                break
        self.adb.sh("pkill -x tcpdump 2>/dev/null; pkill -f replay_up.py 2>/dev/null")
        rc, _ = self.adb.pull(remote_out, out_pcap, timeout=300)
        return out_pcap if rc == 0 else None

    def replay_stat(self):
        _, out = self.adb.sh("tail -3 /dev/shm/replayI.log 2>/dev/null", timeout=15)
        return out.strip()

    def ch_summary(self, pcap_remote):
        """设备端快速通道统计（字节 top10 + 是否有关键帧通道）。"""
        script = (
            "import struct,collections,re\n"
            "d=open('%PCAP%','rb').read(); link=struct.unpack('<I',d[20:24])[0]; off=24\n"
            "st=collections.defaultdict(lambda:[0,0,collections.Counter()])\n"
            "while off+16<=len(d):\n"
            "    ts,tu,il,og=struct.unpack('<IIII',d[off:off+16]); off+=16\n"
            "    p=d[off:off+il]; off+=il\n"
            "    ip=p[14:] if link==1 else p[16:]\n"
            "    if not ip or len(ip)<20 or ip[0]>>4!=4 or ip[9]!=17: continue\n"
            "    u=ip[(ip[0]&0xF)*4:]\n"
            "    if len(u)<8: continue\n"
            "    sp,dp=struct.unpack('>HH',u[:4]); q=u[8:]\n"
            "    if sp!=9004 or len(q)<20: continue\n"
            "    s=st[q[17]]; s[0]+=1; s[1]+=len(q)\n"
            "    for m in re.finditer(bytes.fromhex('00000001'), q[:80]):\n"
            "        t=q[m.start()+4]&0x1f\n"
            "        if t in (5,7): s[2][t]+=1\n"
            "for c,(n,b,nal) in sorted(st.items()):\n"
            "    if b>10000: print('ch=%02x pkts=%d bytes=%d sps=%d idr=%d'%(c,n,b,nal[7],nal[5]))\n"
        ).replace("%PCAP%", pcap_remote)
        _, out = self.adb.sh("python3 - <<'PY'\n%s\nPY" % script, timeout=60)
        return out

    # ---------- 连续预览（拿到 IDR 后续 P 帧即可持续解码） ----------
    def stream_stats(self):
        """读取相机端中继统计（若 lv_relay 在跑）。"""
        script = (
            "import socket\n"
            "s=socket.create_connection(('127.0.0.1',5011),2)\n"
            "s.sendall(b'stat\n')\n"
            "print(s.recv(4096).decode())\n"
        )
        _, out = self.adb.sh("python3 - <<'PY'\n%s\nPY" % script, timeout=15)
        return out.strip()
