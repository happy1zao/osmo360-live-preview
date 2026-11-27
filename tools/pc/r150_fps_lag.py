"""r150_fps_lag.py —— 用"不同时间间隔下的帧差"判定 120fps 是否是真实时间采样。

真时间采样：间隔越大，画面变化越大（帧差随 lag 递增），并叠加一个噪声地板。
假 120fps（30fps 读 sensor、4 帧复制）：lag=1 时会出现大量 0 差异配对。

用法:
    python tools/pc/r150_fps_lag.py <192x192 灰度 raw>
"""
import sys
import numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else ""
if not path:
    sys.exit("usage: r150_fps_lag.py <gray-raw>")
W = 192
raw = np.fromfile(path, dtype=np.uint8)
n = raw.size // (W * W)
fr = raw[:n * W * W].reshape(n, W, W).astype(np.int16)
print("帧数 %d" % n)

print("lag(帧)  等价时间(ms)  帧差中位  帧差 p90   零差异占比")
for lag in (1, 2, 4, 8, 12, 30, 60, 120):
    if lag >= n:
        break
    d = np.abs(fr[lag:] - fr[:-lag]).mean(axis=(1, 2))
    zero = float((d < 0.05).mean() * 100)
    print("%-8d %-13.1f %-10.2f %-10.2f %.1f%%" % (lag, lag * 1000 / 119.88, np.median(d), np.percentile(d, 90), zero))
