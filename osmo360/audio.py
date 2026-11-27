#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""audio —— Osmo 360 四麦实时音频采集

设备侧实测（docs/86、docs/33）：
  PulseAudio source `regular0.audio-processing` = s24-32le / 4ch / 48000Hz
  通道映射 front-left / front-right / side-left / side-right。
  用 `parec`（只录不播，不发声）采集，无需 UVC/UAC。

用法：
  # 相机端后台采集到文件（PPID=1 不被 adb 回收）
  cap = AudioCapture(adb); cap.start_camera_capture(secs=60)
  cap.pull("samples/audio.raw")
  frames = cap.decode("samples/audio.raw")   # list[np.ndarray] (N,4) float32
"""
import os, struct, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = "regular0.audio-processing"
RATE = 48000
CHANNELS = 4
FORMAT = "s24-32le"   # 4 字节/采样，24bit 有效


class AudioCapture:
    def __init__(self, adb, remote_raw="/data/local/tmp/audio_raw.pcm",
                 remote_port=5012, local_port=5012):
        self.adb = adb
        self.remote_raw = remote_raw
        self.remote_port = remote_port
        self.local_port = local_port

    def start_camera_capture(self, secs=60):
        """相机端后台 parec 采集。用 timeout 限时，setsid 脱离 adb。"""
        cmd = ("setsid timeout %d parec --device=%s --format=%s --channels=%d --rate=%d "
               "--file-format=raw > %s 2>/dev/null </dev/null & echo started"
               % (secs, SOURCE, FORMAT, CHANNELS, RATE, self.remote_raw))
        return self.adb.sh(cmd)

    def pull(self, local_path, timeout=300):
        return self.adb.pull(self.remote_raw, local_path, timeout=timeout)

    def stream_forward(self, remove_all=False):
        """建立音频 forward（若改为实时流模式）。与视频 forward 共用同一进程。"""
        return self.adb.forward(self.local_port, self.remote_port, remove_all=remove_all)

    @staticmethod
    def decode_bytes(d, channels=CHANNELS):
        """内存版：s24-32le 4ch 字节流 → (N,channels) float32 [-1,1]（实时流用）。"""
        import numpy as np
        frame_bytes = 4 * channels
        n = len(d) // frame_bytes
        if not n:
            return np.zeros((0, channels), np.float32)
        a = np.frombuffer(d[:n * frame_bytes], dtype="<i4").reshape(n, channels).copy()
        a = (a << 8) >> 8
        return a.astype(np.float32) / float(1 << 23)

    @staticmethod
    def decode(raw_path, channels=CHANNELS):
        """s24-32le 4ch → np.ndarray (N,channels) float32 [-1,1]。"""
        return AudioCapture.decode_bytes(open(raw_path, "rb").read(), channels)

    @staticmethod
    def rms(frames):
        """每通道 RMS，用于声源方向/能量分析。"""
        import numpy as np
        return np.sqrt(np.mean(frames ** 2, axis=0))

    @staticmethod
    def tdoa(frames, rate=RATE):
        """四通道互相关估计声源方向（返回 (delay_samples, corr) 对，相对 ch0）。"""
        import numpy as np
        out = []
        ref = frames[:, 0] - frames[:, 0].mean()
        for c in range(1, frames.shape[1]):
            x = frames[:, c] - frames[:, c].mean()
            n = 1 << (len(x) - 1).bit_length()
            f = np.fft.rfft(x, n) * np.conj(np.fft.rfft(ref, n))
            cc = np.fft.irfft(f, n)
            lag = int(np.argmax(np.abs(cc)))
            if lag > n // 2:
                lag -= n
            out.append((lag, float(cc[lag])))
        return out
