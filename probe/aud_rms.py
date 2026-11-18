#!/usr/bin/env python3
# aud_rms.py <raw s16le> [channels] —— 离线算 RMS/峰值，判断是否真有声音
import sys, struct, math

p = sys.argv[1]
ch = int(sys.argv[2]) if len(sys.argv) > 2 else 2
d = open(p, "rb").read()
n = len(d) // 2
vals = struct.unpack("<%dh" % n, d[:n * 2])
print("%s: %d 样本 (%.2fs @48k/%dch)" % (p, n, n / 48000 / ch, ch))
for c in range(ch):
    seg = vals[c::ch]
    if not seg:
        continue
    peak = max(abs(x) for x in seg)
    rms = math.sqrt(sum(x * x for x in seg) / len(seg))
    nz = sum(1 for x in seg if x != 0)
    print("  ch%d: peak=%6d rms=%8.2f 非零=%.1f%%" % (c, peak, rms, 100 * nz / len(seg)))
