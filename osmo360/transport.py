#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""transport —— adb 连接与端口转发管理

注意事项（来自早期轮次记录）：每条 shell 命令结束会回收 adb daemon，
跨命令建立的 `adb forward` 必然丢失。因此凡是需要 forward 的连续操作，
必须在同一个 Python 进程内完成（本模块即为此设计）。

adb 解析顺序：显式参数 → 环境变量 `ADB` → PATH 中的 `adb` → 字面 "adb"。
设备序列号：环境变量 `ADB_SERIAL`，或调用方显式传入；不传则不带 `-s`。
"""
import os, subprocess, time, shutil

#: 兼容旧引用；默认不写死任何本机路径，由 find_adb() 按顺序解析。
ADB_PATH = None


def find_adb(explicit=None):
    """adb 可执行文件查找顺序：显式 → 环境变量 ADB → PATH → 字面 "adb"。"""
    if explicit:
        return explicit
    env = os.environ.get("ADB")
    if env and os.path.exists(env):
        return env
    return shutil.which("adb") or "adb"


class Adb:
    def __init__(self, path=None, serial=None):
        self.path = find_adb(path)
        # 序列号优先环境变量 ADB_SERIAL；None 表示单设备、不带 -s。
        self.serial = serial if serial is not None else os.environ.get("ADB_SERIAL")
        if self.serial:
            self._prefix = [self.path, "-s", self.serial]
        else:
            self._prefix = [self.path]

    def run(self, *args, timeout=60):
        try:
            p = subprocess.run(self._prefix + list(args), capture_output=True,
                               text=True, timeout=timeout, encoding="utf-8", errors="ignore")
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired:
            return 1, "TIMEOUT"

    def sh(self, cmd, timeout=60):
        """注意设备端 storage-cli-test 等交互命令必须以 exit 结尾，否则永久等待。"""
        return self.run("shell", cmd, timeout=timeout)

    def devices(self, timeout=20):
        return self.run("devices", timeout=timeout)

    def wait_device(self, limit=180):
        t0 = time.time()
        while time.time() - t0 < limit:
            rc, out = self.devices()
            if (not self.serial or self.serial in out) and "\tdevice" in out:
                return True
            time.sleep(3)
        return False

    def forward(self, local, remote, remove_all=False, timeout=20):
        if remove_all:
            self.run("forward", "--remove-all", timeout=timeout)
        return self.run("forward", "tcp:%d" % local, "tcp:%d" % remote, timeout=timeout)

    def forward_list(self, timeout=20):
        return self.run("forward", "--list", timeout=timeout)[1]

    def push(self, src, dst, timeout=300):
        return self.run("push", src, dst, timeout=timeout)

    def pull(self, src, dst, timeout=300):
        return self.run("pull", src, dst, timeout=timeout)

    def start_tcpdump(self, dest_path, pcap_remote, iface="any", extra="udp", secs=None):
        """在相机端后台起 tcpdump（PPID=1，不被 adb 回收）。
        dest_path: 相机上 tcpdump 可执行文件路径；pcap_remote: 输出 pcap 路径。"""
        timeout_cmd = "timeout %d " % secs if secs else ""
        cmd = ("setsid %stcpdump -i %s -s 0 -w %s %s </dev/null >/dev/null 2>&1 & echo started"
               % (timeout_cmd, iface, pcap_remote, extra))
        return self.sh(cmd)

    def stop_tcpdump(self):
        return self.sh("pkill -f 'tcpdump' 2>/dev/null; echo stopped")
