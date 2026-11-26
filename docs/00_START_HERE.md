# 00 START HERE —— Osmo 360（一代）逆向与画质工程总入口

> 这份文档的唯一目的：**任何人或下一个 AI，读完这一页就能接手干活。**
> 读完你应该知道：这台机器是什么、现在已经打通什么、什么路是死路、命令怎么跑、东西放在哪。

最后更新：2026-11-26 21:40 CST（第十九轮）

---

## 1. 一句话定位

把 DJI Osmo 360（一代，QCS8550 + 定制 Debian）逆向成一台**可编程的多模态传感器**：
拿得到原始/高质量画面、能长期稳定跑、只走 adb、不破坏 adb、可回滚。

## 2. 这台机器是什么（硬事实）

| 项 | 值 |
|---|---|
| SoC | 高通 QCS8550，双鱼眼（前后各一） |
| 系统 | 定制 Debian，`root`，`/dev/sda8 on /` ext4 rw |
| adb | 序列号 `123456789ABCDEF`，`adb root` 可用；**adb 一旦切走只能等 3–5 分钟自愈，别拔插** |
| 屏幕 | DSI `314x556` 竖屏，weston `--use-pixman`（软件合成） |
| 存储 | SD `/mnt/media_rw/emulated` ≈106G；`/dev/shm` 1.2G；`/data` 仅 94M overlay |
| 关键进程 | `dji_camera3`(ISP/管线)、`dji_media_server`(录制/协议)、`dji_gui_on_disp0`(GUI/机身屏)、`dji_sys`、`qmmf-server`、`gstd` |
| 相机时钟 | 与 PC 基本同步（都是 CST） |

**绝对红线**：不要 `pkill dji_gui_on_disp0`（会切掉 USB adb 模式）；不要改 `/system/etc/dji_media_service.json` 的 `liveview.supportable`（`dji_media_server` 会启动即崩）；camera3 崩会连带 USB gadget 切成大容量存储。

## 3. 能力矩阵（当前真实状态）

| 能力 | 状态 | 入口 |
|---|---|---|
| adb / root / 文件搬运 | ✅ 完全可用 | `adb -s 123456789ABCDEF` |
| LD_PRELOAD hook `dji_camera3` | ✅ 稳定套路 | `probe/hook_probe*.c` → `/usr/lib/libdcam_hook.so` |
| 单镜实时预览 960×720@30 | ✅ 唯一验证过的实时画面 | `live_preview.bat` / `python -m osmo360.live` |
| 切档 single ⇄ pano | ✅ | `dji_cst -m duss_switch_pano_mode_type_single/pano` |
| **无 GUI 直接开录** | ✅ 本轮打通 | `dji_cst -m duss_record_*`（见 §6） |
| **录制品 4K/1920 真 120fps** | ✅ 已审计确认（1833 帧 / 15.30s） | `tools/pc/r149_fps_audit.py`、`r150_fps_lag.py` |
| **LRF 边写边读 → 2:1 实时预览** | 🟡 离线切片已解出真帧，整链待跑通 | `tools/pc/r147_lrf_live.py`、`r148_pano_preview.py` |
| 直接读机身屏画面（lv UBWC） | ❌ 物理不可行 | 见 `docs/04/115` |
| GPU/Vulkan 转码、DPU writeback | ❌ 本机不存在 | 同上 |
| 9004 视频流（模拟手机 App） | ❌ 只通音频，`vclients=0` | 见 `docs/04/114` |
| UVC / Wi‑Fi 传图 / 截屏拍屏 | ⛔ 用户明确否决，不再尝试 | —— |

## 4. 三条"立刻能跑"的命令

```powershell
# 0) 通用前置：adb 路径（不在 PATH 里）
$adb='E:\123pan\Downloads\DJIAc5P_UI_Debian-master\DJIAc5P_UI_Debian-master\adb\adb.exe'
& $adb devices      # 必须看到 123456789ABCDEF  device

# 1) 单镜实时预览（已验证可靠）
cd C:\Users\Administrator\Doubao\chats\2026-11-08\new-chat\osmo360_re
.\live_preview.bat                 # 或 python -m osmo360.live --secs 60

# 2) 让相机开录 + 实时读它正在写的 2:1 全景
python tools\pc\r148_pano_preview.py --secs 60

# 3) 校验任意一段录制的"帧率真伪"
python tools\pc\r149_fps_audit.py  <192x192 灰度 raw>  192  15.30
```

Python 用**带 cv2/numpy** 的那个：`C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe`
（系统 `python` 3.14 没有 cv2。）

## 5. 目录结构（分类约定）

```
osmo360_re/
├─ docs/                      ← 按阶段分四册，编号即阅读顺序
│   ├─ 00_START_HERE.md       ← 你在这里
│   ├─ 01_STATUS.md           ← 当前进度 / 未完成 / 未决问题
│   ├─ 01_系统硬件与基础协议/
│   ├─ 02_USB_UVC与控制面/
│   ├─ 03_预览流协议攻坚/
│   └─ 04_流控闸门与预览工程化/   ← 第九~十九轮的主战场，最新结论在 115/116
├─ rammer/                    ← 【画质上限专题】独立分区，不参与预览主线
│   ├─ README.md              ← 这一分区要干什么、边界在哪
│   ├─ R01_真120帧审计.md
│   └─ data/                  ← 审计用的小样本（原始大文件不入库）
├─ probe/                     ← 设备端探针：C 源码 + 编译产物 + 设备端脚本
├─ tools/pc/                  ← PC 端分析脚本（r*.py，编号即时间顺序）
├─ osmo360/                   ← PC 端 Python 包（transport/video/session/live/...）
├─ samples/                   ← 证据与语料（体量大，按主题分子目录）
├─ bin/                       ← ffmpeg 等外部工具
└─ *.bat                      ← 一键入口
```

命名规则：`docs/NNN_第N轮_主题.md`；`probe/hook_probeNN.c` 与 `probeNN.so` 同号；
`tools/pc/rNNN_*.py` 只增不改（新结论写新编号，旧脚本保留原样以免破坏可复现性）。

## 6. 必须知道的十条结论（省你几天）

1. **机身屏上那幅画面是 UBWC 压缩缓冲**（DRM `modifier=0x500000000000001`），CPU `mmap` 出来是压缩数据不是像素 → 所有"读 buffer 再解码"的路全废。
2. 本机**没有 GPU 驱动**：`libvulkan_adreno.so` 不存在，EGL 三个后端全失败，weston 是 pixman 软件合成。
3. DPU **没有 writeback 连接器**，无法把合成结果写回内存。
4. 9004（模拟手机 App）**只通音频**：视频要求"成为消费者"，相机判定 `vclients=0`，`SW_Alg_Send_Stream_Check_Ack err 2` 刷屏。
5. `cst` 的 `osmo_pano`/`osmo_pano_rec` 会先 `SetModeProfile`，此时 `fail -129`；**开录用 `duss_record_*` 系列**。
6. cst 的 Record 块自带 `wait_stop_ms`，**到点会自己发 stop**；要"一直录"就在 10 秒内把 cst 杀掉（`pkill -f dji_cs[t]`）。
7. 录制产物：`.OSV` 走主编码（1920×1920 hevc Main10 @119.88fps，约 11MB/s）+ `.LRF` 是 2:1 代理（2048×1024 h264 @29.97）。
8. `.LRF` 的 **moov 在收尾时才写**，但 mdat 边写边可读；载荷 = `DJI protobuf` + `AVCC(4B 长度前缀)`，**SPS/PPS 需借已完成片段的**。
9. 读正在写的文件**绝不能用 `tail -c +N`**（会线性扫，500MB 时直接卡死）→ 用设备端 `probe/lrf_tail.py`（seek + 跟随）。
10. ffmpeg 从管道读 h264 时，**首字节必须是 SPS/PPS**，否则立刻退出并把后续写入变 BrokenPipe（表现为"一帧不出也不报错"）。

## 7. 探针（hook）通用套路

```bash
# 编译：zig，必须把缓存指到可写目录
$zig cc -target aarch64-linux-gnu -shared -nostdlib -O0 -fno-sanitize=all \
        -fno-stack-protector -o probeNN.so hook_probeNN.c

# 部署：覆盖 LD_PRELOAD 载体后单独重启 camera3
adb push probeNN.so /data/local/tmp/
adb shell 'cp -f /data/local/tmp/probeNN.so /usr/lib/libdcam_hook.so && chmod 644 /usr/lib/libdcam_hook.so'
adb shell 'nohup systemctl restart dji_camera3 >/dev/null 2>&1 &'
# 另起一条 adb 核对：pgrep -x dji_camera3 + /dev/shm/dcam_hook_alive.txt 时间戳
```

`camera3` 的环境里已有 `LD_PRELOAD=libdcam_hook.so:libqcom_adapter.so`，所以覆盖这个文件名就等于换探针。
**稳定基线 = `probe16.so`**（lv 三分支 tee + rlv tee，不影响正常出图）。

## 8. 现在往哪走（按优先级）

1. 把 `r147/r148` 的 LRF 实时链跑通到 ≥25fps、延迟 <1s（离线部分已验证）。
2. 若要彻底去掉"读文件"这一层：把 tee 探针的前置载入从 `camera3` 改到 `dji_media_server`（录制管线实际在那里）。
3. `rammer/` 专题：把 4K/1920 120fps 的采集边界、ISP 调参、10bit 位深、码率上限逐项推开。
