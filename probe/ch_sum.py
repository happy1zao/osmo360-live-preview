import struct, collections, sys

fn = sys.argv[1]
d = open(fn, "rb").read()
off = 24
chan = {}
while off + 16 <= len(d):
    ts, tu, inc, orig = struct.unpack("<IIII", d[off:off + 16])
    off += 16
    pp = d[off:off + inc]
    off += inc
    ip = pp[14:]
    if len(ip) < 20 or ip[9] != 17:
        continue
    udp = ip[(ip[0] & 15) * 4:]
    p = udp[8:]
    if len(p) < 40:
        continue
    if p[19:23] == b"\x00\x00\x00\x01":
        body = p[19:]
    else:
        body = p[20:]
    chan.setdefault(p[17], bytearray())
    chan[p[17]] += body

for ch, buf in sorted(chan.items(), key=lambda kv: -len(kv[1])):
    s = buf.find(b"\x00\x00\x00\x01\x67")
    i = buf.find(b"\x00\x00\x00\x01\x65")
    print("ch %#04x  bytes %8d  SPS %8d  IDR %8d" % (ch, len(buf), s, i))
