import os, struct, sys

# Find the liveview config struct where target_bitrate=8000000 is followed by
# idr_interval=1000000 and rewrite idr_interval to 25.
# NEGATIVE RESULT: this has no effect on an already-created encoder instance,
# because the config is latched once at instance creation. Kept for reference.
A = 8000000
B = 1000000
NEW = 25

pid = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.popen("pidof dji_camera3").read().split()[0])
maps = [l.split() for l in open("/proc/%d/maps" % pid)]
mem = open("/proc/%d/mem" % pid, "rb+", 0)

done = 0
for m in maps:
    if len(m) < 2 or m[1][0] != "r" or m[1][1] != "w":
        continue
    a, b = [int(x, 16) for x in m[0].split("-")]
    if b - a > 64 * 1024 * 1024:
        continue
    try:
        mem.seek(a)
        buf = mem.read(b - a)
    except Exception:
        continue
    i = buf.find(struct.pack("<I", A))
    while i >= 0:
        j = i + 4
        if j + 4 <= len(buf) and struct.unpack("<I", buf[j:j + 4])[0] == B:
            addr = a + j
            mem.seek(addr)
            mem.write(struct.pack("<I", NEW))
            print("idr_interval -> 25 @ %#x (pid %d)" % (addr, pid))
            done += 1
        i = buf.find(struct.pack("<I", A), i + 4)
print("patched %d location(s)" % done)
