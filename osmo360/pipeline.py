#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""pipeline —— 一键串联：给美学感知模型输出 2:1 双鱼眼 numpy 帧

三种工作模式：
  offline(pcap)   从已抓好的 pcap 逐帧解码（稳定、可复现、模型输入首选）
  dev_capture()   脱手机自建会话抓 IDR → 离线出帧（需相机屏幕处于取景界面）
  start_watch()   相机端守株待兔，抓手机 Mimo 会话首帧 IDR → 出帧

使用者（美学模型）只需：
    from osmo360 import Pipeline
    p = Pipeline()
    for frame in p.offline_frames("samples/livecap.pcap"):
        model.infer(frame)     # frame: np.ndarray (1024, 2048, 3) 2:1 双鱼眼 RGB
"""
import os, time

from .transport import Adb, find_adb
from .video import (reassemble_pcap, decode_h264, load_stream, iter_h264_frames,
                    assemble_full_stream, to_strip_geometry,
                    FFMPEG, FULL_W, FULL_H, STRIP_H)
from .audio import AudioCapture
from .control import CameraControl
from .session import DevSession

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Pipeline:
    def __init__(self, adb=None, ffmpeg=None):
        self.adb = adb or Adb()
        self.ffmpeg = ffmpeg
        self.audio = AudioCapture(self.adb)
        self.control = CameraControl(self.adb)
        self.dev = DevSession(self.adb)

    # ---------- 离线：给模型喂帧（最稳） ----------
    def offline_frames(self, pcap, ch=None, geometry="full", leading1=None):
        """从 pcap 解码帧，逐帧 yield numpy RGB array。

        geometry="full"（默认）= 预览小流：2048×1024 整帧 25fps，一帧就是
        完整 2:1 双鱼眼。geometry="strip" = 全景分片流：每条通道 2048×32 条带。
        输入也可以是裸 .h264 文件，由 load_stream 自动识别；解码走流式生成器，
        内存恒定一帧。
        """
        h264, src = load_stream(pcap, ch=ch, leading1=leading1)
        if not h264:
            return
        if geometry == "strip":
            h264 = to_strip_geometry(h264)
        h = STRIP_H if geometry == "strip" else FULL_H
        yield from iter_h264_frames(h264, ffmpeg=self.ffmpeg or FFMPEG, w=FULL_W, h=h)

    # ---------- 脱手机自建会话 ----------
    def dev_capture(self, handshake_pcap=None, out_pcap="samples/live_selfhost.pcap", secs=55):
        """一键脱手机抓流：推样本→完整上行重放→连接中重启→取回 pcap→解出双鱼眼帧。"""
        hs = handshake_pcap or os.path.join(ROOT, "samples", "mimo_cold_handshake.pcap")
        got = self.dev.capture_selfhost(hs, out_pcap, secs=secs)
        if not got:
            print("抓包失败：设备端无 pcap 返回。replay 尾部：\n%s" % self.dev.replay_stat())
            return
        h264, info = assemble_full_stream(got)
        if not h264:
            print("会话内无关键帧通道（相机未推流）。replay 尾部：\n%s" % self.dev.replay_stat())
            return
        yield from iter_h264_frames(h264, ffmpeg=self.ffmpeg or FFMPEG, w=FULL_W, h=FULL_H)

    # ---------- 实时：守株待兔（手机 Mimo 场景） ----------
    def start_watch(self, secs=90):
        """相机端启动抓包监听（PPID=1，不被 adb 回收）。调用后再开流。"""
        self.adb.sh("pkill -f 'tcpdump' 2>/dev/null; rm -f /dev/shm/idrwatch.pcap")
        return self.adb.sh(
            "setsid timeout %d tcpdump -i any -s 0 -w /dev/shm/idrwatch.pcap udp port 9004 "
            "</dev/null >/dev/null 2>&1 & echo started" % secs)

    def collect_watch(self, local_pcap="samples/idrwatch.pcap", timeout=300):
        """取回监听期间抓到的 pcap。"""
        return self.adb.pull("/dev/shm/idrwatch.pcap", local_pcap, timeout=timeout)

    def live_frames(self, local_pcap="samples/idrwatch.pcap"):
        self.collect_watch(local_pcap)
        yield from self.offline_frames(local_pcap)

    # ---------- 音频 ----------
    def capture_audio(self, secs=60, local_raw="samples/audio_raw.pcm"):
        """采集 4ch/48k 音频并解码，返回 (frames, rms, tdoa)。只录不播、不发声。"""
        self.audio.start_camera_capture(secs=secs)
        time.sleep(secs + 2)
        self.audio.pull(local_raw)
        frames = AudioCapture.decode(local_raw)
        return frames, AudioCapture.rms(frames), AudioCapture.tdoa(frames)

    # ---------- 控制 ----------
    @property
    def cam(self):
        return self.control
