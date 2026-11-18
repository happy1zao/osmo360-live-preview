#!/usr/bin/env python3
# gen38.py —— 0x38 关键帧请求造包器（L=23, 残差 B44F）
import struct, sys
sys.path.insert(0, "/data/local/tmp")
try:
    from gen92 import crc0, xor8
except ImportError:
    POLY = 0x8408
    def crc0(data):
        c = 0
        for b in data:
            c ^= b
            for _ in range(8):
                c = ((c >> 1) ^ POLY) & 0xFFFF if c & 1 else c >> 1
        return c
    def xor8(b, n=7):
        v = 0
        for i in range(n):
            v ^= b[i]
        return v & 0xFF

R23 = 0xB44F
TPL = bytes.fromhex(
    "2b 80 40 34 70 7f 05 d5 68 7f 70 7f 00 00 00 00 ae 01 00 00 "
    "55 17 04 38 02 41 04 92 40 09 a8 00 04 02 00 00 00 00 00 00 00 b7 ac".replace(" ", ""))


def build38(seq):
    p = bytearray(TPL)
    p[26] = seq & 0xFF
    crc = crc0(bytes(p[22:41])) ^ R23     # DUML[2:21]
    p[41:43] = struct.pack("<H", crc)
    p[7] = xor8(p)
    return bytes(p)


if __name__ == "__main__":
    reals = [
        "2b 80 40 34 70 7f 05 d5 68 7f 70 7f 00 00 00 00 ae 01 00 00 55 17 04 38 02 41 04 92 40 09 a8 00 04 02 00 00 00 00 00 00 00 b7 ac",
        "2b 80 40 34 00 82 05 58 f8 81 00 82 00 00 00 00 00 01 00 00 55 17 04 38 02 41 56 92 40 09 a8 00 04 02 00 00 00 00 00 00 00 b5 05",
        "2b 80 40 34 68 83 05 31 60 83 68 83 00 00 00 00 2d 01 00 00 55 17 04 38 02 41 83 92 40 09 a8 00 04 02 00 00 00 00 00 00 00 05 63",
    ]
    ok = 0
    for h in reals:
        real = bytes.fromhex(h.replace(" ", ""))
        seq = real[26]
        got = build38(seq)
        match = got[20:] == real[20:]
        ok += match
        print("seq=%#04x DUML匹配=%s  crc实=%s 算=%s" % (
            seq, match, real[41:43].hex(), got[41:43].hex()))
    print("验证 %d/3" % ok)
