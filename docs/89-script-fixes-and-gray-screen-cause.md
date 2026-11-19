# 89 本轮修复：脚本跑不起来 + 灰屏真正原因 + IDR 必发/缓存

日期：2026-11-20

## 一、用户报“python3 .py 报错”的真实原因

**这台 Windows 上没有 `python3` 命令**（`where python3` → 找不到），只有 `python`（3.13.18）。
所以命令要写成：

```
python preview.py
```

不是脚本语法错误。`python -m py_compile preview.py` 通过，`python preview.py --help` 正常。

## 二、实测发现并修掉的 4 个真 bug

| # | 缺陷 | 现象 | 修法 |
|---|---|---|---|
| 1 | `ffmpeg -stream_loop -1` 用在裸 H264 上 | ffmpeg 立刻退出，日志 `Operation not permitted` / `Terminating thread with error`，窗口没画面 | 回放改为 python 按约 2MB/s 分段喂 stdin，不再让 ffmpeg seek |
| 2 | adb 不在 PATH 时直接崩栈 | `FileNotFoundError: [WinError 2]` 一串堆栈 | 回落已知 adb 路径；真找不到时给中文提示并 `SystemExit` |
| 3 | 高 DPI 下 opencv 窗口可能开在屏幕外 | 进程和窗口标题都在，屏幕上却看不见 | 首次 `imshow` 后 `moveWindow(60,60)` + `WND_PROP_TOPMOST` |
| 4 | bat 三处硬伤 | ①`where` 在 PATH 里不存在 → 误报“找不到 python” ②LF 换行 → cmd 把行拆碎报 `'"' 不是内部命令` ③`setsid` Android 没有 | ①改成直接 `python --version` 探测 ②统一 CRLF/无 BOM ③改 `nohup sh ... &`，kill 与 start 分两次 adb shell |

## 三、另一个隐蔽坑：kill_relay.sh 把自己杀了

`adb shell "sh kill_relay.sh; ... python3 lv_relay.py ..."` 整条命令行都含 `lv_relay.py`，
旧脚本按 cmdline grep 匹配，会把执行它的 shell 一起 `kill -9`（退出码 137）。

新脚本要求 **cmdline 第一个词就是 python3/python** 才认定为中继本体，包装 shell 不会误伤。

## 四、灰屏的真正原因（重要）

历史结论一直说“IDR 只在实例创建时出现、晚接入拿不到”。本轮定位到**另一半**：

> **IDR 其实经常到了，但被重组器按“缺包整帧丢弃”规则丢掉了。**

IDR 帧通常是最长的一帧（包最多），最容易出现缺口。旧 `flush()`：

```python
if c["bad"]:
    st["drop"] += 1        # 整帧丢弃 —— 包括唯一的 IDR
```

于是：
1. IDR 到 → 被判 bad → 丢弃
2. 之后的 P 帧全部没有参考帧 → ffmpeg 只能输出灰
3. 看起来像“永远抓不到 IDR”

**修法**：关键帧（NAL 0x65 或 SPS 0x67）**即使有缺口也照发**。第一帧局部花，后续 P 帧立刻恢复正常。
另外把重复包（`i == prev`）不算作缺口。

## 五、IDR 缓存补发（新机制）

`lv_relay.py` 新增 `idr_cache[2]`：保存每只眼最近一次关键帧的完整 body。
任何**新接入的客户端**在 `accept` 后先收到 `SPS+PPS+cached_IDR`，再进实时流。

意义：不必“恰好在编码器创建的瞬间连着”，连上就有参考帧。
控制台的 `stat` 现在会显示缓存大小 `cache=x/y`，另有 `idr` 命令清空缓存。

## 六、ACK 参数实测

| 设置 | 结果 |
|---|---|
| ACKN=4，reqms=52ms | 约 22 帧/秒，drop≈5%/帧 |
| **ACKN=1，reqms=30ms** | **约 25 帧/秒**（ok 增量 ≈25/s），drop 明显下降 |

## 七、反面教材：dji_cst 的 pano 序列不能用来逼 IDR

跑 `duss_liveview_6000x3000_pano_video_p25` 之后，流变成了 **2048×32×32 的条带流**（CAVLC），
用 SPS0(2048×1024) 解出来是**横条纹灰带**（`samples/idr_snap3.png`），不是手机那路 2048×1024 预览。

结论：
- 手机/屏幕预览那一路（2048×1024 CABAC）由 **GUI 预览**创建，不是这条 cst 序列。
- 逼 IDR 的正确顺序：`restart dji_camera3` → **GUI 重新进入实时取景**（实例创建 + 吐 IDR）。
- 只 restart 而不让 GUI 恢复预览，会**停流**（实测 big 从 90320 到 90374 就不再增长）。

## 八、当前阻塞（需要人在相机上操作）

最后一次 kick 之后相机停在 **USB 连接界面**（gui.log: `usb busy true`、`DeviceRunning(false)`），
GUI 没有回到实时取景，编码器实例没重建、出流已停。

**恢复动作**：在相机屏幕上退出 USB 文件传输 / 回到拍摄界面，让实时取景重新出现。
之后 relay 一接入就会先收到缓存的 IDR（缓存为空则等下一次实例创建），画面应立刻干净。

## 九、文件变更

- `preview.py`：修 ffmpeg 回放、adb 回落与提示、窗口置顶/移动、新增 `--snap/--snap-frames` 无人值守抓帧
- `start_preview.bat`：重写探测逻辑、CRLF、去 setsid
- `probe/lv_relay.py`：关键帧必发、重复包忽略、IDR 缓存补发、`idr` 清缓存命令
- `probe/kill_relay.sh`：只杀真正的 python3 中继进程

备份：`backup/20261120_fix_bat_preview/`
