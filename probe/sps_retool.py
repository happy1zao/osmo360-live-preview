#!/usr/bin/env python3
"""sps_retool.py —— 定位/重写 SPS 的宽高字段（保留其它所有比特）"""
import sys


class BR:
    def __init__(self, b):
        self.b, self.p = b, 0

    def bit(self):
        v = (self.b[self.p // 8] >> (7 - self.p % 8)) & 1
        self.p += 1
        return v

    def u(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | self.bit()
        return v

    def ue(self):
        z = 0
        while self.bit() == 0:
            z += 1
        v = 0
        for _ in range(z):
            v = (v << 1) | self.bit()
        return (1 << z) - 1 + v

    def se(self):
        v = self.ue()
        return (v + 1) // 2 if v % 2 else -(v // 2)


def bits_of(b, p0, p1):
    return [int((b[i // 8] >> (7 - i % 8)) & 1) for i in range(p0, p1)]


def ue_of(v):
    v += 1
    n = v.bit_length()
    return [int(c) for c in ("0" * (n - 1) + format(v, "0%db" % n))]


HIGH = (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135)


def locate(sps_payload):
    """sps_payload: 不含 0x67 NAL 头的 RBSP。返回 (w_pos, h_pos, tail_pos, w_mbs, h_map, info)"""
    r = BR(sps_payload)
    info = {}
    info["profile"] = r.u(8)
    info["cons"] = r.u(8)
    info["level"] = r.u(8)
    info["sps_id"] = r.ue()
    if info["profile"] in HIGH:
        info["chroma"] = r.ue()
        if info["chroma"] == 3:
            r.bit()
        r.ue()                       # bit_depth_luma_minus8
        r.ue()                       # bit_depth_chroma_minus8
        r.bit()                      # qpprime
        if r.bit():                  # seq_scaling_matrix_present
            n = 8 if info["chroma"] != 3 else 12
            for i in range(n):
                if r.bit():
                    size = 16 if i < 6 else 64
                    last = nextv = 8
                    for _ in range(size):
                        if nextv != 0:
                            nextv = (last + r.se() + 256) % 256
                        last = nextv if nextv != 0 else last
    info["log2_frame_num"] = r.ue() + 4
    info["poc_type"] = r.ue()
    if info["poc_type"] == 0:
        info["log2_poc_lsb"] = r.ue() + 4
    elif info["poc_type"] == 1:
        r.bit()                      # delta_pic_order_always_zero
        r.se()                       # offset_for_non_ref_pic
        r.se()                       # offset_for_top_to_bottom
        for _ in range(r.ue()):
            r.se()
    r.ue()                           # max_num_ref_frames
    r.bit()                          # gaps
    w_pos = r.p
    w_mbs = r.ue() + 1
    h_pos = r.p
    h_map = r.ue() + 1
    return w_pos, h_pos, r.p, w_mbs, h_map, info


def rebuild(sps, w_mbs, h_map):
    assert sps[0] == 0x67
    payload = sps[1:]
    w_pos, h_pos, tail_pos, ow, oh, info = locate(payload)
    b = bits_of(payload, 0, w_pos) + ue_of(w_mbs - 1) + ue_of(h_map - 1) + bits_of(payload, tail_pos, len(payload) * 8)
    while b and b[-1] == 0:
        b.pop()
    out = bytearray([0x67])
    for i in range(0, len(b), 8):
        ch = b[i:i + 8]
        v = 0
        for c in ch + [1] * (8 - len(ch)):
            v = (v << 1) | c
        out.append(v)
    return bytes(out), (ow, oh), info


if __name__ == "__main__":
    src = bytes.fromhex(sys.argv[1]) if len(sys.argv) > 1 else bytes.fromhex(
        "67 64 00 28 ac b4 01 00 04 0d 35 01 01 01 06 d0 a1 35")
    _, (ow, oh), info = rebuild(src, 128, 64)
    print("原: %dx%d  参数: %s" % (ow * 16, oh * 16, info))
    for hw in (64, 48, 32, 24, 16, 8, 4, 2, 1):
        out, _, _ = rebuild(src, 128, hw)
        print("  高 %4d -> %s （长 %d）" % (hw * 16, out.hex(" "), len(out)))
