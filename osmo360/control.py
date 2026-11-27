#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""control —— 相机全参数控制（封装设备端 camtool.py）

本模块是 Python 侧薄封装：参数读写通过设备端 `camtool.py` 调用官方
libdcam 客户端完成，PC 侧只做命令拼装与安全黑名单拦截。

TODO(设备端脚本未随仓库发布)：`camtool.py` 不在本仓库内（见 probe/README.md
“未随仓库发布的设备端脚本”）。本模块可以正常 import；只有真正调用
`dump/modes/get/set/...` 等方法时，才要求目标设备上已存在该脚本。
本地离线分析路径不依赖本模块。

安全黑名单（扬声器/提示音/重启/格式化等）在 PC 侧再拦一道。
"""
import re

CAMTOOL = "/data/local/tmp/camtool.py"

# 与 camtool.py 一致的安全黑名单（在 PC 侧也拦一道）
BLOCK = ("play_volume", "play_device", "prompt_tone", "beeper", "speaker",
         "play_sound", "reboot", "reset", "format_storage", "erase",
         "audio_play", "snapshot_enable")


class CameraControl:
    def __init__(self, adb, camtool=CAMTOOL):
        self.adb = adb
        self.camtool = camtool

    def _run(self, *args, timeout=60):
        cmd = "python3 %s %s" % (self.camtool, " ".join(str(a) for a in args))
        return self.adb.sh(cmd, timeout=timeout)

    def dump(self, timeout=180):
        """读全部可访问参数，返回原始文本（camtool 同时写设备侧 cam_params.json）。"""
        return self._run("dump", timeout=timeout)

    def modes(self, timeout=60):
        return self._run("modes", timeout=timeout)

    def get(self, getter, timeout=30):
        self._assert_safe(getter)
        return self._run("get", getter, timeout=timeout).strip()

    def set(self, setter, value, timeout=30):
        self._assert_safe(setter)
        if not isinstance(value, int):
            raise ValueError("参数值必须是 int")
        return self._run("set", setter, value, timeout=timeout).strip()

    def audio_channels(self, n, timeout=30):
        if n not in (1, 2, 4):
            raise ValueError("音频通道只能是 1/2/4")
        return self._run("audio_channels", n, timeout=timeout).strip()

    def raw(self, v, timeout=30):
        self._assert_safe("picture_storage_format")
        return self._run("raw", v, timeout=timeout).strip()

    def _assert_safe(self, name):
        low = name.lower()
        for b in BLOCK:
            if b in low:
                raise PermissionError("参数 %s 命中安全黑名单 %s，拒绝执行" % (name, b))

    @staticmethod
    def known_getters():
        """从 camtool.py 源码提取 getter 名（设备侧或本地副本）。

        TODO: 本地副本 probe/camtool.py 未随仓库发布；本方法在缺少该文件时
        会抛出 FileNotFoundError，仅在自备设备端脚本后可用。
        """
        import os
        local = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "probe", "camtool.py")
        if not os.path.exists(local):
            raise FileNotFoundError(
                "probe/camtool.py 未随仓库发布；known_getters() 需要自备该脚本副本")
        text = open(local, encoding="utf-8").read()
        m = re.search(r"KNOWN_GETTERS\s*=\s+\"\"\"(.*?)\"\"\"", text, re.S)
        return m.group(1).split() if m else []
