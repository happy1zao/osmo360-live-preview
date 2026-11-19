# 2026-11-20 追加突破：预览工程化落地 / Preview engineering landed

> 中英双语。只写突破结果，不写失败路线。承接 `breakthrough-2026-11-19.md`。
> Bilingual. Working results only. Continues `breakthrough-2026-11-19.md`.

## 1. 两路流彻底厘清 / Two streams finally separated

**中文** — 过去灰屏/花屏的总根源是把两路 H264 混为一谈。它们几何与参数集完全不同：

| 流 | 几何 | 熵编码 | SPS | PPS |
|---|---|---|---|---|
| **预览小流（手机看到的）** | **2048×1024 整帧**（双鱼眼 2:1） | CABAC | `67640028acb40100040d3501010106d0a135` | `68ee06f2c0` |
| 全景分片流 | 2048×32 条带 ×32 | CAVLC | `67640028acb40100b4d40404041b4284d7` | `68ce3880`（deblock=0） |

以 IDR 开头的预览小流样本，用 SPS0+`68ee06f2c0` 直接解出**完全干净的双鱼眼画面**
（人物、显示器、书架、台灯清晰），即“接近手机连接”的目标画质，已自证。

**English** — The root cause of past gray/mosaic frames was conflating two H264 streams with
different geometry and parameter sets. The preview small stream (what the phone shows) is a
**full 2048×1024 dual-fisheye frame, CABAC**, SPS0 + PPS `68ee06f2c0`. An IDR-started sample
decodes to a **perfectly clean dual-fisheye image** — the target "phone-like" quality, self-verified.

## 2. 正确重组器 / Correct reassemblers

**中文** — 下行 19B 头：`byte[16]=fid`、`byte[17]=通道`、`byte[18]=分片号`。
- 预览小流：`k=byte[17]>>7`（左右鱼眼），`i=byte[18]`（帧内包序，0 起），`i==0` 起新帧，缺包整帧丢弃。
- 全景分片流：`frag=byte[18]&0x1F`（高 3 位是片类型），按 fid 对齐续包，丢分片更少。

**English** — 19-byte downstream header: `byte[16]=fid`, `byte[17]=channel`, `byte[18]=fragment`.
Preview stream uses `k=byte[17]>>7`, `i=byte[18]` (full byte); panorama strips use `byte[18]&0x1F`
(high 3 bits are slice-type flags). Two reassemblers: `pc_recv7.py` and `reasm5.py`.

## 3. CAVLC PPS 搜索 / CAVLC PPS search

**中文** — `pps_find.py` 以 rawvideo 出帧为判据扫描，关键发现：CAVLC 流的
`deblocking_filter_control_present_flag` 必须 = 0，否则 `deblocking_filter_idc 13 out of range` 拒帧。
全景流最佳 PPS = `68ce3880`。

**English** — `pps_find.py` scans entropy×deblock×qp×chroma. Key finding: for the CAVLC stream
`deblocking_filter_control_present_flag` must be 0, otherwise frames are rejected. Best panorama PPS = `68ce3880`.

## 4. 通用 DUML CRC + 配置注入 / Generic DUML CRC + config injection

**中文** — **通用 CRC = `crc16(init=0x3692, poly=0x8408)` over `DUML[0:-2]`**（含 0x55 与长度字节），
匹配所有长度样本。`extract_cfg.py` 提取手机 12 种上行命令（含 ASCII "APP" 注册、扩展启动、话题订阅）；
`build_cfg.py` 据此把配置命令改到本会话外壳，注入后切片大小随内容变化，证明配置生效。

**English** — Generic CRC = `crc16(init=0x3692, poly=0x8408)` over `DUML[0:-2]`, matching all lengths.
`extract_cfg.py` extracts 12 phone uplink commands (APP registration, extended start, topic subscribe);
`build_cfg.py` re-targets them to the local session; slice sizes then vary with content, proving config takes effect.

## 5. 本地预览播放器 + 控制台 / Local preview player + console

**中文** — 新增 `preview.py`（PC 端）：打开即见预览窗口，附控制台输入 `help`：
`stat / kick / rehand / ackn / reqms / kf / save / quit`。相机端 `lv_relay.py` 把
“手机握手订阅 + 52ms 周期请求 + ACKN=4 ACK + 周期重放握手 + 双鱼眼组帧”合一，
经 TCP 5010（视频）/5011（控制）送出。`start_preview.bat` 一键启动。
`python preview.py --file samples/live20.h264` 可无需相机立即回放干净双鱼眼画面。

**English** — New `preview.py` (PC): opens a preview window with a `help` console
(`stat / kick / rehand / ackn / reqms / kf / save / quit`). Camera-side `lv_relay.py` combines
phone-handshake subscribe + 52 ms periodic request + ACKN=4 ACK + periodic handshake replay +
dual-fisheye framing, served over TCP 5010 (video) / 5011 (control). `start_preview.bat` is one-click.
`python preview.py --file samples/live20.h264` replays a clean dual-fisheye capture without the camera.

## 6. 维持流与 IDR / Flow sustain and IDR

**中文** — 两套 ACK 均可维持 530–600 包/秒。编码器只在实例创建时吐 IDR；独立接入只拿 P 帧会灰屏。
中继用 `kick`（重启 camera3+重握手）与周期重放握手捕获 IDR；一旦带上 IDR，画面即干净（播放器已自证）。

**English** — Both ACK schemes sustain 530–600 pkt/s. The encoder emits IDR only at instance creation;
a late-attached session gets P-frames only (gray). The relay catches IDR via `kick`
(restart camera3 + re-handshake) and periodic handshake replay; once an IDR is present the picture is clean
(the player path is self-verified).
