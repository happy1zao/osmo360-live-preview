#!/usr/bin/env python3
"""r184_proxy_trim.py —— 从 proxy H.264 AnnexB 里找到 SPS/PPS+IDR 起点后截断

用法: python r184_proxy_trim.py <in.h264> <out.h264>
逻辑: 扫所有 start code，记录每个 NAL 类型；找到「SPS(7) 紧跟 PPS(8) 之后出现 IDR(5)」的
      第一个 GOF 起点，从该字节起截断输出，这样 ffmpeg 能正常解码。
注意: H.264 的 nal_unit_type 是 b[0] & 0x1F（初版误用 (b[0]>>1)&0x3F，已修）。
"""
import sys
from collections import Counter

SC = b"\x00\x00\x00\x01"


def nals(data):
    idx = 0
    pos = data.find(SC, idx)
    starts = []
    while pos != -1:
        starts.append(pos)
        pos = data.find(SC, pos + 4)
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(data)
        if e - s < 5:
            continue
        b = data[s + 4:e]
        yield s, (b[0] & 0x1F), e


def main():
    src, dst = sys.argv[1], sys.argv[2]
    data = open(src, "rb").read()
    types = Counter()
    first_gof = None
    prev = []
    for s, t, e in nals(data):
        types[t] += 1
        prev.append(t)
        prev = prev[-3:]
        if t == 5 and 7 in prev and 8 in prev:
            first_gof = s
            break
    print("NAL types:", dict(sorted(types.items())))
    if first_gof is None:
        print("no SPS/PPS+IDR found; writing from first SPS")
        for s, t, e in nals(data):
            if t == 7:
                first_gof = s
                break
    if first_gof is None:
        print("no SPS at all")
        first_gof = 0
    out = data[first_gof:]
    open(dst, "wb").write(out)
    print(f"trim at {first_gof}, out {len(out)} bytes -> {dst}")


if __name__ == "__main__":
    main()
