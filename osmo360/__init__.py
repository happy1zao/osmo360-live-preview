#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""osmo360 —— DJI Osmo 360 有线感知包（供美学感知模型使用）

目标：完全脱离手机 App，通过一根 USB Type-C 线从相机拿到
  * 2:1 双鱼眼实时帧（numpy RGB array，直接喂模型）
  * 4ch/48kHz 实时音频
  * 全参数控制（官方 libdcam API 封装）

模块：
  transport  adb 连接 / forward 管理（规避 daemon 回收）
  session    会话建立与流启动（9004 握手重放）
  video      下行包重组 + 解码 → 双鱼眼帧
  audio      PulseAudio regular0 4ch 采集
  control    相机参数读写（封装 camtool，含安全黑名单）
  pipeline   一键串联，输出帧/音频迭代器

设计原则：
  * 所有需要连续 adb 操作的功能都在同一个进程内完成——本执行环境
    每结束一条 shell 命令会回收 adb daemon，跨命令的 forward 必丢。
  * IDR 只在会话建立第一帧出现一次，必须先监听、后开流。

用法（见 pipeline.py）：
    from osmo360 import Pipeline
    p = Pipeline()
    for frame in p.offline_frames("samples/live20.h264"):
        model.infer(frame)          # ndarray (1024, 2048, 3) uint8 RGB
"""
from .transport import Adb, find_adb, ADB_PATH
from .video import (FrameDecoder, reassemble_pcap, decode_h264, load_stream,
                    iter_h264_frames, assemble_full_stream,
                    stack_strips, to_strip_geometry)
from .audio import AudioCapture
from .control import CameraControl
from .session import DevSession
from .pipeline import Pipeline
from .live import LiveStream

__all__ = ["Adb", "find_adb", "ADB_PATH", "FrameDecoder", "reassemble_pcap",
           "decode_h264", "load_stream", "iter_h264_frames",
           "assemble_full_stream", "stack_strips", "to_strip_geometry",
           "AudioCapture", "CameraControl", "DevSession", "Pipeline", "LiveStream"]
__version__ = "0.1.0"
