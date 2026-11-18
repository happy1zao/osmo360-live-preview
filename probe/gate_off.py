#!/usr/bin/env python3
# gate_off.py -- disable the host-check kill branch of dji_sys (memory only, no file change)
#   original  0x5d2f8: b.ne #0x5d350 (540002c1)  ->  patch: b #0x5d350 (14000016)
# rollback: systemctl restart dji_system   (process restart restores original code)
import os, struct, sys, subprocess

OFF = 0x5d2f8
ORIG = 0x540002C1
PATCH = 0x14000016


def pid_of(name):
    out = subprocess.check_output(["pidof", name]).decode().split()
    return int(out[0]) if out else 0


def main():
    pid = pid_of("dji_sys")
    if not pid:
        print("dji_sys not running")
        return 1
    base = None
    for l in open("/proc/%d/maps" % pid).read().splitlines():
        if "/dji_sys" in l:
            base = int(l.split()[0].split("-")[0], 16)
            break
    if base is None:
        print("dji_sys mapping not found")
        return 1
    fd = open("/proc/%d/mem" % pid, "r+b", 0)
    fd.seek(base + OFF)
    cur = struct.unpack("<I", fd.read(4))[0]
    print("pid=%d base=0x%x current=%08x" % (pid, base, cur))
    if cur == PATCH:
        print("already patched")
        return 0
    if cur != ORIG:
        print("unexpected original bytes (different firmware?), aborting")
        return 1
    fd.seek(base + OFF)
    fd.write(struct.pack("<I", PATCH))
    fd.flush()
    fd.seek(base + OFF)
    print("after write=%08x OK" % struct.unpack("<I", fd.read(4))[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
