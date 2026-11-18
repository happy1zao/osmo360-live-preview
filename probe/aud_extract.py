#!/usr/bin/env python3
# aud_extract.py <raw s32le 4ch> <out.wav> —— 取 ch0/ch2（真实麦克风），>>8 转 s16 立体声
import sys, struct, wave

src, dst = sys.argv[1], sys.argv[2]
d = open(src, "rb").read()
n = len(d) // 4
v = struct.unpack("<%di" % n, d[:n * 4])
frames = n // 4
out = bytearray()
for i in range(frames):
    a = v[i * 4 + 0] >> 8
    b = v[i * 4 + 2] >> 8
    a = max(-32768, min(32767, a))
    b = max(-32768, min(32767, b))
    out += struct.pack("<hh", a, b)
w = wave.open(dst, "wb")
w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000)
w.writeframes(bytes(out)); w.close()
print("写出 %s：%d 帧 / %.2fs 立体声(ch0+ch2)" % (dst, frames, frames / 48000))
