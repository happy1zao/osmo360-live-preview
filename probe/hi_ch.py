#!/usr/bin/env python3
# hi_ch.py <pcap> —— 提取通道号>=0x28 的所有包，按通道重组并报告 NAL 类型
import struct, sys, collections, re, os

d = open(sys.argv[1], "rb").read()
link = struct.unpack("<I", d[20:24])[0]
off = 24
ch = collections.defaultdict(list)
while off + 16 <= len(d):
    ts, tu, inc, orig = struct.unpack("<IIII", d[off:off + 16]); off += 16
    pp = d[off:off + inc]; off += inc
    ip = pp[16:] if link == 113 else pp[14:]
    if len(ip) < 20 or ip[0] >> 4 != 4 or ip[9] != 17:
        continue
    u = ip[(ip[0] & 15) * 4:]
    if len(u) < 8 or struct.unpack(">H", u[:2])[0] != 9004:
        continue
    q = u[8:]
    if len(q) < 30:
        continue
    c = q[17]
    if c < 0x28 or c >= 0x80:
        continue
    body = q[19:] if q[19:23] == b"\x00\x00\x00\x01" else q[20:]
    ch[c].append((ts + tu / 1e6, q[18], body))
os.makedirs("samples/hich", exist_ok=True)
for c, lst in sorted(ch.items()):
    lst.sort()
    blob = b"".join(b for _, _, b in lst)
    types = collections.Counter()
    for m in re.finditer(b"\x00\x00\x00\x01", blob):
        s = m.start() + 4
        if s < len(blob):
            types[blob[s] & 0x1f] += 1
    p = "samples/hich/c%02x.h264" % c
    open(p, "wb").write(blob)
    print("ch=%#04x 包%4d 字节%8d NAL=%s -> %s" % (c, len(lst), len(blob), dict(types), p))
