# 状态与里程碑 · Status

> 状态口径：✅ 仓库内保留对应链路代码；🟡 离线/单段已验证，整链仍在进行；⛔ 已尝试并受阻。
> 本文只引用仓库内已发布的脚本；未发布的早期脚本见 `tools/pc/README.md`。

## 链路现状

| 环节 | 状态 | 说明 | 对应代码 |
|---|---|---|---|
| adb / root / 文件搬运 | ✅ | 设备可 adb root，脚本可推到 `/data/local/tmp` | `osmo360/transport.py` |
| 进程与库清单、9004 协议雏形 | ✅ | 下行大包头解析、通道模型、分片重组 | `osmo360/video.py` |
| 离线双鱼眼帧重组 | ✅ | 从已抓包逐帧解码，conceal=0 | `osmo360/pipeline.py`、`osmo360/video.py` |
| 12 秒流控闸门补丁 | ✅ | 只改进程内存；回滚 = 重启 `dji_system` | `probe/gate_off.py` |
| 相机端中继推流 | ✅ | 组帧 → TCP 5010（视频）/ 5011（控制） | `probe/lv_relay.py`、`probe/run_relay.sh` |
| PC 中继预览窗口 | ✅ | ffmpeg 解码 + 控制台命令 | `preview.py` |
| LRF 边写边读 2:1 预览 | 🟡 | v4 按 GOF 锚点捞 NAL，录制中可出 2048×1024；连续 P 帧长稳仍在观察 | `tools/pc/r156_lrf_live2.py` |
| 离线帧率真伪审计 | ✅ | 对自备灰度 raw 做帧数/时长/重复帧/帧差-间隔分析 | `tools/pc/r149_fps_audit.py`、`tools/pc/r150_fps_lag.py` |
| 零写盘代理流 tee | ✅ | hook 探针把 proxy 管线 tee 出一路 H.264；`.OSV/.LRF/.MET` 假写不落盘 | `probe/hook_probe97.c`、`tools/pc/pano_live97.py` |
| UI 档位 P100→P120 原地改写 | ✅ | 定长数组，不增删条目 | `tools/pc/r185_ui_swap_120.py` |
| 脱离手机自建会话 | 🟡 | 完整冷启动时序重放可触发持续推流；连续 P 帧解码仍待解 | `osmo360/session.py`、`osmo360/live.py` |
| 设备端 GPU/EGL/Vulkan 转码 | ⛔ | 无 `libvulkan_adreno.so`，EGL 三后端全失败，weston 为 pixman 软件合成 | — |

## 已知受阻点

- **GPU 转码**：相机内没有可用 Vulkan/EGL 后端，设备端硬解/硬转不可行；解码在 PC 侧完成。
- **连续 P 帧长稳**：首帧 IDR 之后的全景分片 P 帧与预览小流不是同一路，连续解码稳定性仍在迭代。
- **样本不入库**：历史里程碑引用的抓包、码流、截图均为私有采集，不在仓库内（见 `samples/README.md`）。

## 目录索引

- 主动指南：[`00_START_HERE.zh.md`](00_START_HERE.zh.md)
- 详细原理：[`preview-guide.zh.md`](preview-guide.zh.md)
- 轮次归档：[`archive/`](archive/)
