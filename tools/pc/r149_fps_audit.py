"""r149_fps_audit.py —— 判定"是否真 120 帧"。

思路：解出来的总帧数 ÷ 时长 = 名义帧率；再逐帧比对相邻帧差异，
如果大量帧几乎一模一样（差异 < 阈值），说明是重复帧凑出来的"假高帧率"。
同时给出"真正有新内容的帧率"（unique fps）。

用法:
    python tools/pc/r149_fps_audit.py <192x192 灰度 raw> [width] [duration_secs]
"""
import sys
import numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else ""
if not path:
    sys.exit("usage: r149_fps_audit.py <gray-raw> [width=192] [duration=15.30]")
W = int(sys.argv[2]) if len(sys.argv) > 2 else 192
dur = float(sys.argv[3]) if len(sys.argv) > 3 else 15.30

raw = np.fromfile(path, dtype=np.uint8)
n = raw.size // (W * W)
fr = raw[:n * W * W].reshape(n, W, W).astype(np.int16)
print("总帧数 %d，时长 %.2fs → 名义 %.2f fps" % (n, dur, n / dur))

d = np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2))
print("相邻帧差：min %.2f  p10 %.2f  中位 %.2f  max %.2f" % (d.min(), np.percentile(d, 10), np.median(d), d.max()))

for thr in (0.30, 0.60, 1.0, 2.0, 4.0):
    uniq = int((d > thr).sum()) + 1
    print("阈值 %.1f：有新内容的帧 %d（%.1f%%）→ unique fps ≈ %.1f" % (thr, uniq, 100.0 * uniq / n, uniq / dur))

# 重复帧连续的分布：连续相同帧的最大长度
same = d <= 0.30
run = best = 0
for s in same:
    run = run + 1 if s else 0
    best = max(best, run)
print("最长连续重复段：%d 帧" % best)

# 每 1/30 秒的间隔采样，看差异是否周期性归零（说明内部 30fps 上采样到 120）
step = int(round(n / (dur * 30)))
print("按 30fps 间隔(每 %d 帧)采样时，差异中位：%.2f" % (step, np.median(d[::step])))
print("按 120fps 相邻采样时，差异中位：%.2f" % np.median(d))
