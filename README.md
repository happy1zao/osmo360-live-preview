# Osmo 360（OQ101 / Archway）逆向实验台

> 目标设备：DJI Osmo 360 一代，机型 **OQ101** / 代号 **Archway** / 平台 **QCS8550**
> 系统：QTI Linux（Debian 底 + systemd），内核 5.15.148，USB-C ADB 已 root。
> 研究性质：自有设备上的互操作与个人研究；不含授权绕过、DRM 破解或固件分发。

**先读这两份，其余按需：**

| 文档 | 用途 |
|---|---|
| [`docs/00_START_HERE.md`](docs/00_START_HERE.md) | 一页上手：机器是什么、现在能做什么、命令怎么跑、目录在哪 |
| [`docs/01_STATUS.md`](docs/01_STATUS.md) | 进度快照：已验证 / 进行中 / 已判死 / 未决问题 |

（旧的 `docs/00_总索引.md` 与 `HANDOFF.md` 只覆盖到第八轮，**已过时**，留作过程档案。）

---

## 这是什么

不是"给相机做 UI"，而是把它当成**可编程的多模态传感器**：拿得到原始画面、能长期稳定跑、能被脚本化调用。

四条硬约束：只走 adb；不破坏 adb；不碰 GUI（会切掉 USB adb）；所有改动可回滚。

## 当前能力（2026-11-26，第十九轮）

| 能力 | 状态 | 入口 |
|---|---|---|
| 实时预览（单镜 960×720@30） | ✅ 唯一验证过的实时画面 | `live_preview.bat` |
| 无 GUI 开录 | ✅ | `dji_cst -m duss_record_*`（见 START_HERE §6） |
| 4K/1920 采集**真 119.88fps** | ✅ 已审计（1833 帧 / 15.30s） | [`rammer/R01_真120帧审计.md`](rammer/R01_真120帧审计.md) |
| 2:1 全景实时预览（LRF 边写边读） | 🟡 离线已解出真帧，整链待跑通 | `tools/pc/r148_pano_preview.py` |
| 直接读机身屏 / GPU 转码 / DPU 回写 / 9004 视频 | ❌ 已判死 | `docs/04_流控闸门与预览工程化/115` |

## 目录

```
docs/       00_START_HERE → 01_STATUS → 01..04 四册（编号即阅读顺序）
rammer/     画质上限专题（真 120 帧审计、采集档位、10bit 链路）
probe/      设备端探针：C 源码 + 编译产物 + 设备端 python 脚本
tools/pc/   PC 端分析脚本 r*.py（只增不改）
osmo360/    PC 端 Python 包（transport / video / session / live）
samples/    证据与语料（体量大，按主题分目录）
```

## 给感知模型用（osmo360 包）

`osmo360/` 目前能给出 2:1 双鱼眼 numpy RGB 帧（来自已验证的解码路径）：

```python
from osmo360 import Pipeline
p = Pipeline()
for frame in p.offline_frames("samples/live20.h264"):
    model.infer(frame)          # (1024, 2048, 3) uint8
```

## 红线

- 不要 `pkill dji_gui_on_disp0` —— 会切掉 USB adb 模式。
- 不要改 `dji_media_service.json` 的 `liveview.supportable` —— `dji_media_server` 启动即崩。
- `dji_camera3` 崩会连带 USB gadget 切大容量存储：**等 3–5 分钟自愈，不要拔插**。
