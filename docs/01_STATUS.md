# 01 STATUS —— 进度快照与未决清单

> 只记"事实与状态"，不写过程流水账。过程细节在各轮 `docs/04/*.md`。
> 每次收工时更新这一页，别让接手的人去翻 60 份文档找答案。

更新：2026-11-26 21:40 CST / 第十九轮结束

---

## 一、里程碑（按轮次）

| 轮 | 拿到了什么 | 关键产物 |
|---|---|---|
| 1–5 | adb/root、进程与库清单、9004 协议雏形、离线拼片 | `osmo360/`、`r74_e2e_dec.py` |
| 6–9 | 分片判据（`pair` 口径）、conceal 语义、环形槽位 | `osmo360/video.py::_assemble` |
| 10 | **首次拿到连续双鱼眼 1254 帧 / 95.8s**（≈13fps，conceal=0） | `samples/g74_f317.png` |
| 11–13 | 回放失效根因（host 槽 + 资源锁）、Wi‑Fi 不可用、UVC 否决 | `docs/03/*` |
| 14 | camera3 内部取帧打通（`h265_push.py`，单镜 960×720@30） | `probe/h265_push.py` |
| 15–17 | 各类 hook 边界（looper 不可 hook、`stream_group_mgr_*` 三参数、camera3 崩会切 USB） | `docs/04/107~110` |
| 18 | 判死 LCD 直读：UBWC 不可 CPU 读、无 GPU、无 writeback、9004 无视频 | `docs/04/113~115` |
| **19** | **录制路径打通**：无 GUI 开录、4K/1920 真 120fps 已审计、LRF 可边写边读 | `docs/04/116`、`rammer/R01` |

## 二、已验证能力（可直接用）

- 单镜实时预览 960×720、30–33fps、`conceal=0`：`live_preview.bat`。
- 无 GUI 开录：`dji_cst -m duss_record_3840x1920_video_pano_p120_mp4`（10s 内杀 cst 可无限录）。
- 录制规格（实测）：`.OSV` 1920×1920 hevc Main10 **119.88fps**、约 140Mbps、11MB/s；`.LRF` 2048×1024 h264 29.97fps、约 1.2MB/s。
- 帧率真伪审计：`r149_fps_audit.py`（帧数/时长/重复帧）、`r150_fps_lag.py`（帧差-间隔曲线）。
- LRF 实时可读（离线已验证）：protobuf + AVCC NAL，借 SPS/PPS 前置即可解出真帧。

## 三、进行中（未跑通的最后一环）

- **LRF 实时预览整链**：常驻跟随 + SPS 预注入 + IDR 对齐已经写好（`r147/r148`），
  缺一次"录制稳定 + 连续解码"的完整实测（第十九轮被 USB 掉线打断）。
- 目标：≥25fps、延迟 <1s、可连续跑 10 分钟不掉。

## 四、已判死（别再试）

| 方向 | 判死依据 |
|---|---|
| CPU 直读机身屏画面 | UBWC 压缩缓冲，读出来是压缩数据（`docs/04/115`） |
| GPU / EGL / Vulkan 转码 | 无 `libvulkan_adreno.so`，EGL 三后端全失败 |
| DPU writeback | `/sys/class/drm/` 无 Writeback 连接器 |
| 9004 视频流 | 只通音频；`vclients=0`；stream-check ack 失败刷屏 |
| `qtivtransform` / `qtifcvtransform` | 前者必 SIGSEGV，后者不认 `compression=ubwc` |
| `looper_message_send` hook | 必崩 camera3（probe32 崩 5 次） |
| 改 `liveview.supportable=true` | `dji_media_server` 启动即 SEGV |
| 改 `gst_frwk_sp.json` 组模板 | 运行时组 id 由 camera3 现拼，改文件无效 |
| UVC / Wi‑Fi 传图 / 截屏拍屏 | 用户明确否决 |

## 五、未决问题（谁接手都可以从这里挑）

1. 录制管线到底建在哪个进程：camera3 里 `main_yuv_src` tee 全程 0 帧，而盘上文件正常增长 → 强烈怀疑在 `dji_media_server`，**未验证**（需要往 media_server 注入只读探针）。
2. `duss_record_*` 偶发卡在 `record_starting`（前一段未收尾时），缺少一个可靠的"重置录制状态"动作。
3. 是否存在比 `1X1_1920P` 更高的采集档：`cst_cases.json` 里有 `5120x3840`、`7680x3840` 等 case，未逐个验证能否在 pano 模式下跑起来。
4. 10bit 组 id 的兜底模板定义在哪个二进制里（sp.json 里没有这个 id）。
5. `liveview_stream_consumer` 的真正注册者（App DUSS / GUI / media service）未定位。
6. GitHub 仓库名与本地分类尚未完全对齐（仓库侧仍叫 `osmo360-live-preview`）。

## 六、给下一位的三条建议

1. 先读 `docs/00_START_HERE.md`，再读本页，然后只读 `docs/04/115` 和 `116` —— 其余按需。
2. 任何"读像素"的念头，先问一句：**这块 buffer 是 UBWC 吗？** 是就立刻放弃。
3. 任何"让相机吐流"的尝试，先看 `cam.log` 里有没有 `record_status_change` 和 `record_status`——录制状态是本项目唯一稳定可控的"开机键"。
