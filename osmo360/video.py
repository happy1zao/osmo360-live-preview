#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""video —— 双鱼眼 H264 重组与解码（协议要点见各函数注释）。"""
import os, struct, re, subprocess, collections, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_ffmpeg():
    env = os.environ.get("FFMPEG")
    if env and os.path.exists(env):
        return env
    return shutil.which("ffmpeg") or "ffmpeg"


FFMPEG = _resolve_ffmpeg()
SPS0 = bytes.fromhex("0000000167640028acb40100040d3501010106d0a135")
PPS0 = bytes.fromhex("0000000168ee06f2c0")
SPS_STRIP32 = bytes.fromhex("0000000167640028acb40100b4d40404041b4284d7")
STRIP_H = 32
FULL_W, FULL_H = 2048, 1024
DJI_HEAD = bytes([0, 0, 0, 1, 255])
NAL_START = bytes([0, 0, 0, 1])
_NAL_TYPES = (1, 5, 6, 7, 8)

print("video module placeholder loaded")
