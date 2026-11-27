#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""video —— 双鱼眼 H264 重组与解码

协议要点（全部来自实测/文档，勿重复推导）：
  * 下行 UDP 包（相机 9004 → 客户端）：19B 传输头
        byte[16]=fid(帧号)  byte[17]=ch(通道)  byte[18]=frag(分片号/标志)
  * 通道模型：base = ch & 0x7f，rank = ch >> 7。
  * frag 取低 5 位（q[18] & 0x1F）；高 3 位是标志，剥离后 0..31。
  * frag0 带 17B DJI 包装头，剥掉后从 SPS/PPS/IDR 起始码起；
    不能用最后一个起始码截断——会把 SPS/PPS 一起丢掉。
  * pair 流（预览小流）frag>0 每片前有 1B 分片头，需剥掉；条带流直接续接。
  * IDR 只在会话建立第一帧出现一次（idr_interval=1000000）。
"""
import os, struct, re, subprocess, collections, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_ffmpeg():
    """ffmpeg 解析：环境变量 FFMPEG → PATH → 字面 ffmpeg。"""
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
DJI_HEAD = bytes.fromhex("00000001ff")
NAL_START = bytes.fromhex("00000001")
_NAL_TYPES = (1, 5, 6, 7, 8)


def to_strip_geometry(h264, sps_from=SPS0, sps_to=SPS_STRIP32):
    """把流里的整帧 SPS 换成 2048×32 条带 SPS。"""
    return h264.replace(sps_from, sps_to, 1)


def _pcap_packets(path_or_bytes):
    """产出 (timestamp, src_port, dst_port, udp_payload)，兼容 linktype 113/1。"""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        d = bytes(path_or_bytes)
    else:
        d = open(path_or_bytes, "rb").read()
    if len(d) < 24 or d[:4] not in (bytes.fromhex("d4c3b2a1"), bytes.fromhex("a1b2c3d4")):
        return
    link = struct.unpack("<I", d[20:24])[0]
    off = 24
    while off + 16 <= len(d):
        ts, tu, il, og = struct.unpack("<IIII", d[off:off + 16]); off += 16
        p = d[off:off + il]; off += il
        ip = p[16:] if link == 113 else (p[14:] if link == 1 else None)
        if not ip or len(ip) < 20 or ip[0] >> 4 != 4 or ip[9] != 17:
            continue
        u = ip[(ip[0] & 0xF) * 4:]
        if len(u) < 8:
            continue
        sport, dport = struct.unpack(">HH", u[:4])
        yield ts + tu / 1e6, sport, dport, u[8:]


def strip_frag0(body):
    """frag0 去掉 DJI 17B 包装头，保留从第一个 NAL 起始码开始的完整字节。"""
    if body[:5] == DJI_HEAD:
        return body[17:]
    for m in re.finditer(NAL_START, body[:48]):
        if (body[m.start() + 4] & 0x1F) in _NAL_TYPES:
            return body[m.start():]
    return body


class FrameDecoder:
    """流式重组器：喂相机下行 UDP payload，产出完整帧的 h264 字节。"""

    def __init__(self, base=None, leading1=None, sp=9004):
        self.sp = sp
        self.base = base
        self.leading1 = leading1
        self.fid = None
        self.parts = {}
        self.frames = 0
        self.dropped = 0

    def feed(self, q):
        if len(q) < 20:
            return None
        ch = q[17]
        b = ch & 0x7F
        rank = ch >> 7
        if self.base is None:
            if (q[18] & 0x1F) == 0:
                nb = strip_frag0(q[19:])
                if (NAL_START + bytes.fromhex("67")) in nb[:64] and (NAL_START + bytes.fromhex("65")) in nb[:160]:
                    self.base = b
        if self.base is None or b != self.base:
            return None
        fid, frag = q[16], q[18] & 0x1F
        out = None
        if self.fid is not None and fid != self.fid:
            out = self.flush()
        self.fid = fid
        self.parts[(frag, rank)] = q[19:]
        return out

    def _assemble(self, parts):
        if not parts:
            return None
        if (0, 0) not in parts:
            return None
        if self.leading1 is None:
            l1 = any(r for (_, r) in parts)
        else:
            l1 = self.leading1
        frags = {f for (f, _) in parts}
        if len(frags) != max(frags) + 1:
            self.dropped += 1
            return None
        buf = bytearray(strip_frag0(parts[(0, 0)]))
        for key in sorted(parts):
            if key == (0, 0):
                continue
            bb = parts[key]
            buf += bb[1:] if l1 else bb
        seg = bytes(buf)
        if len(seg) <= 64 or (seg[4] & 0x1F) not in _NAL_TYPES:
            return None
        return seg

    def flush(self):
        seg = self._assemble(self.parts)
        self.parts = {}
        self.fid = None
        if seg:
            self.frames += 1
        return seg

    def finish(self):
        return self.flush()


def reassemble_pcap(path_or_bytes, ch=None, sp=9004, leading1=None):
    """从 pcap 重组 H264 帧字节流。返回 (h264_bytes, base)。"""
    dec = FrameDecoder(base=ch, leading1=leading1, sp=sp)
    out = bytearray()
    for ts, sport, dport, q in _pcap_packets(path_or_bytes):
        if sport != sp:
            continue
        seg = dec.feed(q)
        if seg:
            out += seg
    seg = dec.flush()
    if seg:
        out += seg
    return bytes(out), dec.base


def _all_pair_frames(path_or_bytes, sp=9004):
    C = collections.defaultdict(lambda: collections.defaultdict(dict))
    for ts, sport, dport, q in _pcap_packets(path_or_bytes):
        if sport != sp or len(q) < 20:
            continue
        ch = q[17]
        C[ch & 0x7F][q[16]][(q[18] & 0x1F, ch >> 7)] = q[19:]
    out = {}
    for base, fids in C.items():
        frames = []
        for fid in sorted(fids):
            parts = fids[fid]
            if (0, 0) not in parts:
                continue
            fs = {f for f, _ in parts}
            if len(fs) != max(fs) + 1:
                continue
            bb = bytearray(strip_frag0(parts[(0, 0)]))
            for k in sorted(parts):
                if k != (0, 0):
                    bb += parts[k][1:]
            s = bytes(bb)
            if len(s) > 64 and (s[4] & 0x1F) in _NAL_TYPES:
                frames.append((fid, s))
        if frames:
            out[base] = frames
    return out


def assemble_full_stream(path_or_bytes, key_base=None, main_base=None, sp=9004):
    """从自建会话 pcap 拼出连续 H264 流（IDR + 全部 P 帧）。返回 (h264_bytes, info)。"""
    allf = _all_pair_frames(path_or_bytes, sp=sp)
    if key_base is None:
        cand = [(len(fr), b) for b, fr in allf.items()
                if any((NAL_START + bytes.fromhex("67")) in s[:64] for _, s in fr)]
        key_base = max(cand)[1] if cand else None
    if main_base is None:
        cand = [(sum(1 for _, s in fr if (s[4] & 0x1F) == 1), b)
                for b, fr in allf.items() if b != key_base]
        main_base = max(cand)[1] if cand else None
    if key_base is None:
        return b"", {}
    key = [s for _, s in allf.get(key_base, []) if (NAL_START + bytes.fromhex("67")) in s[:64]]
    main = [s for _, s in allf.get(main_base, []) if (s[4] & 0x1F) == 1]
    info = dict(key_base=key_base, main_base=main_base, idr=len(key), p_frames=len(main))
    return b"".join(key + main), info


def load_stream(path_or_bytes, ch=None, leading1=None):
    """统一入口：pcap → 重组；裸 .h264 文件 → 原样返回。返回 (h264_bytes, 来源标记)。"""
    d = (bytes(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray))
         else open(path_or_bytes, "rb").read())
    if d[:4] in (bytes.fromhex("d4c3b2a1"), bytes.fromhex("a1b2c3d4")):
        return reassemble_pcap(d, ch=ch, leading1=leading1)
    return d, "raw"


def iter_h264_frames(h264_bytes, ffmpeg=FFMPEG, w=FULL_W, h=FULL_H, tmp=None):
    """流式逐帧解码，yield (h, w, 3) uint8 RGB，内存恒定为一帧。"""
    import numpy as np
    tmp = tmp or os.path.join(ROOT, "samples", "_scratch", "_osmo_stream.h264")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "wb") as f:
        f.write(h264_bytes)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "h264", "-i", tmp,
           "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    fb = w * h * 3

    def readn(n):
        buf = bytearray()
        while len(buf) < n:
            c = p.stdout.read(n - len(buf))
            if not c:
                break
            buf += c
        return bytes(buf)

    try:
        while True:
            b = readn(fb)
            if len(b) < fb:
                break
            yield np.frombuffer(b, np.uint8).reshape(h, w, 3).copy()
    finally:
        try:
            p.stdout.close()
            p.wait(timeout=5)
        except Exception:
            p.kill()


def decode_h264(h264_bytes, out_prefix=None, frames=5, width=None, ffmpeg=FFMPEG, geometry="full"):
    """解码 H264 字节流。返回 (rgb_frames, saved_paths)。"""
    import numpy as np
    if geometry == "strip":
        h264_bytes = to_strip_geometry(h264_bytes)
    tmp = out_prefix + ".h264" if out_prefix else "_osmo_dec.h264"
    with open(tmp, "wb") as f:
        f.write(h264_bytes)
    saved = []
    rgb = []
    cmd_base = [ffmpeg, "-y", "-f", "h264", "-i", tmp]
    if out_prefix:
        subprocess.run(cmd_base + ["-frames:v", str(frames), "-fps_mode", "passthrough",
                                   out_prefix + "_%02d.png"], capture_output=True, text=True, timeout=120)
        import glob
        saved = sorted(glob.glob(out_prefix + "_*.png"))
        for fn in saved:
            rgb.append(np.array(__import__("cv2").imread(fn))[:, :, ::-1].copy())
        return rgb, saved
    w, h = (width or FULL_W), (STRIP_H if geometry == "strip" else FULL_H)
    cmd = cmd_base + ["-frames:v", str(frames), "-fps_mode", "passthrough",
                      "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    p = subprocess.run(cmd, capture_output=True, timeout=120)
    fb = w * h * 3
    for i in range(0, len(p.stdout) - fb + 1, fb):
        rgb.append(np.frombuffer(p.stdout[i:i + fb], np.uint8).reshape(h, w, 3).copy())
    return rgb, saved


def stack_strips(strip_frames, height=STRIP_H):
    """把多条 2048×32 条带帧竖堆成 2048×(N*32) 图。"""
    import numpy as np
    if not strip_frames:
        return None
    return np.concatenate(strip_frames, axis=0).astype(strip_frames[0].dtype)
