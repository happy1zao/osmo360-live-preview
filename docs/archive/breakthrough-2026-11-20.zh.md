> **历史归档（中文）** / Archived round log (Chinese).
> 本文是早期逆向轮次记录。文中引用的部分早期脚本与样本未随仓库发布，
> 清单见 [../../tools/pc/README.md](../../tools/pc/README.md) 与 [../../samples/README.md](../../samples/README.md)；
> 设备序列号已脱敏为 `<device-serial>`。命令请勿直接照跑，以根目录 README 为准。

# 2026-11-20 追加突破：预览工程化落地 / Preview engineering landed

> 只写已确证的突破结果，不写失败路线。承接 `breakthrough-2026-11-19.zh.md`。

## 1. 两路流彻底厘清 / Two streams finally separated

过去灰屏/花屏的总根源是把两路 H264 混为一谈。它们几何与参数集完全不同：

| 流 | 几何 | 熵编码 | SPS | PPS |
|---|---|---|---|---|
| **预览小流（手机看到的）** | **2048×1024 整帧**（双鱼眼 2:1） | CABAC | `67640028acb40100040d3501010106d0a135` | `68ee06f2c0` |
| 全景分片流 | 2048×32 条带 ×32 | CAVLC | `67640028acb40100b4d40404041b4284d7` | `68ce3880`（deblock=0） |

以 IDR 开头的预览小流样本，用 SPS0+`68ee06f2c0` 直接解出**完全干净的双鱼眼画面**
（人物、显示器、书架、台灯清晰），即“接近手机连接”的目标画质，已自证。

## 2. 正确重组器 / Correct reassemblers

下行 19B 头：`byte[16]=fid`、`byte[17]=通道`、`byte[18]=分片号`。
- 预览小流：`k=byte[17]>>7`（左右鱼眼），`i=byte[18]`（帧内包序，0 起），`i==0` 起新帧，缺包整帧丢弃。
- 全景分片流：`frag=byte[18]&0x1F`（高 3 位是片类型），按 fid 对齐续包，丢分片更少。

## 3. CAVLC PPS 搜索 / CAVLC PPS search

`pps_find.py`（未发布）以 rawvideo 出帧为判据扫描，关键发现：CAVLC 流的
`deblocking_filter_control_present_flag` 必须 = 0，否则 `deblocking_filter_idc 13 out of range` 拒帧。
全景流最佳 PPS = `68ce3880`。

## 4. 通用 DUML CRC + 配置注入 / Generic DUML CRC + config injection

**通用 CRC = `crc16(init=0x3692, poly=0x8408)` over `DUML[0:-2]`**（含 0x55 与长度字节），
匹配所有长度样本。`extract_cfg.py`、`build_cfg.py`（均未发布）提取手机 12 种上行命令（含 ASCII "APP" 注册、扩展启动、话题订阅）；
`build_cfg.py` 据此把配置命令改到本会话外壳，注入后切片大小随内容变化，证明配置生效。

## 5. 本地预览播放器 + 控制台 / Local preview player + console

新增 `preview.py`（PC 端）：打开即见预览窗口，附控制台输入 `help`：
`stat / kick / rehand / ackn / reqms / kf / save / quit`。相机端 `lv_relay.py` 把
“手机握手订阅 + 52ms 周期请求 + ACKN=4 ACK + 周期重放握手 + 双鱼眼组帧”合一，
经 TCP 5010（视频）/5011（控制）送出。`start_preview.bat` 一键启动。
`python preview.py --file samples/live20.h264` 可无需相机立即回放干净双鱼眼画面。

## 6. 维持流与 IDR / Flow sustain and IDR

两套 ACK 均可维持 530–600 包/秒。编码器只在实例创建时吐 IDR；独立接入只拿 P 帧会灰屏。
中继用 `kick`（重启 camera3+重握手）与周期重放握手捕获 IDR；一旦带上 IDR，画面即干净（播放器已自证）。
