"""lrf_tail.py —— 设备端：从指定偏移持续跟随一个正在写入的文件，把新字节吐到 stdout。

为什么不用 tail -c +N：busybox 的 tail 要线性扫过前 N 字节，文件 500MB 时直接卡死。
python 的 seek 是 O(1)，并且可以常驻连接（PC 侧只需开一条 adb exec-out 流）。

用法：python3 lrf_tail.py <file> <offset>
"""
import sys
import time

path = sys.argv[1]
off = int(sys.argv[2])
f = open(path, "rb")
f.seek(off)
out = sys.stdout.buffer
CH = 128 * 1024
idle = 0
while True:
    d = f.read(CH)
    if d:
        out.write(d)
        out.flush()
        idle = 0
    else:
        idle += 1
        time.sleep(0.02 if idle < 50 else 0.1)
