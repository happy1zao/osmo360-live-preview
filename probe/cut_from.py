import struct, sys, subprocess, os

fn = sys.argv[1]
target = int(sys.argv[2]) if len(sys.argv) > 2 else None
d = open(fn, "rb").read()
link = struct.unpack("<I", d[20:24])[0]
if target is None:
    target = d.find(b"\x00\x00\x00\x01\x67")

off = 24
pkts = []
ch = None
while off + 16 <= len(d):
    rs = off
    ts, tu, inc, orig = struct.unpack("<IIII", d[off:off + 16])
    off += 16
    pp = d[off:off + inc]
    off += inc
    ip = pp[16:] if link == 113 else pp[14:]
    if len(ip) < 20 or ip[9] != 17:
        continue
    udp = ip[(ip[0] & 15) * 4:]
    p = udp[8:]
    if len(p) < 1000:
        continue
    if rs <= target < off:
        ch = p[17]
        print("target in channel %#x, p[16]=%#x" % (ch, p[16]))
    pkts.append(p)

if ch is None:
    sys.exit("target packet not located")

buf = bytearray()
for p in pkts:
    if p[17] != ch:
        continue
    buf += p[19:] if p[19:23] == b"\x00\x00\x00\x01" else p[20:]

i = buf.find(b"\x00\x00\x00\x01\x67")
print("channel stream %d bytes, SPS at %d" % (len(buf), i))
if i < 0:
    sys.exit("no SPS in assembled stream")
out = "samples/_dec.h264"
open(out, "wb").write(buf[i - 4:] if i >= 4 else buf[i:])
print("wrote", out, os.path.getsize(out))
