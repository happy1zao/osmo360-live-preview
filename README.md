# Osmo 360 · Live Preview / 实时预览

> ## ⚠️ DISCLAIMER / 免责声明
>
> **EN** — This repository documents reverse-engineering research performed on a device **owned by the author**,
> and is published **strictly for study and research purposes**. It exists to explain how a consumer camera
> implements its internal live-preview pipeline. The technique described modifies **only the memory of a
> running process** — nothing is written to storage, nothing is flashed — and a service restart fully reverts it.
> Do **not** apply this to any device you do not own or are not authorized to modify. You are solely responsible
> for complying with local law, the manufacturer's terms of service and any warranty conditions.
> The author provides **no warranty of any kind** and accepts **no liability** for damage to hardware, loss of
> data, voided warranty, bricked devices, or any legal consequence arising from the use of this material.
> Do **not** use it for paid unlocking services, commercial circumvention, or any infringing purpose.
> All product names, trademarks and firmware are the property of their respective owners. This project is
> **not affiliated with, endorsed by, or sponsored by** DJI. Rights holders may open an issue to request removal.
>
> **中文** — 本仓库记录的是在**本人合法持有**的设备上完成的研究，**仅供学习与研究**，用于说明消费级相机的
> 内部实时预览链路是如何实现的。文中手法**只修改运行中进程的内存**，不写磁盘、不刷写固件，重启服务即完全恢复。
> 请勿对**非本人所有或未获授权**的设备使用。使用本材料所涉及的合规性、保修与法律风险，由使用者自行承担。
> 作者**不提供任何形式的担保**，**不对**硬件损坏、数据丢失、保修失效、设备变砖或任何法律后果负责。
> 禁止用于付费解锁服务、商业破解或任何侵权用途。相关产品名称、商标与固件归各自权利人所有；本项目与 DJI
> **无任何隶属、背书或赞助关系**。权利方可通过 issue 申请下架。

---

## 一、这是什么 / What is this

**中文** — 一套可在 PC 上直接拿到 DJI Osmo 360（Qualcomm QCS8550 / DJI 定制 Linux）**实时预览流**的方法与工具：
不依赖手机 App、不截屏、不拍屏，从相机自身的内存采集实时画面，并附带完整的控制面说明（参数、模式、录像）。

**English** — A method and a small toolset to obtain a **live preview stream** directly from a DJI Osmo 360
(Qualcomm QCS8550 / DJI customized Linux) on a PC — no phone app, no screen capture, no photographing a screen.
Frames are taken from the camera's own memory pipeline, plus the complete control surface (parameters, modes, recording).

---

## 二、突破结果 / What was achieved

| 项 / Item | 结果 / Result |
|---|---|
| 会话不被踢 / Session survival | 相机原本 12 秒断一次，现已**完全消除**，连续 78 秒无中断 / the built-in 12-second session kill is **eliminated**; 78 s continuous with zero gaps |
| 出面速率 / Frame rate | 600–666 大包/秒 ≈ **26 fps** / 600–666 large packets per second ≈ 26 fps |
| 丢包 / Packet loss | **重复包 0、缺口 0** / duplicate packets = 0, gaps = 0 |
| 关键帧 / Keyframe | 可稳定取得 **SPS + PPS + IDR** / SPS + PPS + IDR reliably captured |
| 解码 / Decode | ffmpeg 零报错解出 **2048×1024 / 25 fps** 真实画面 / ffmpeg decodes 2048×1024 @25 fps with zero errors |
| 参数集复用 / Parameter reuse | SPS 长度恒定，可复用于该预览档 / the SPS is stable and reusable for this profile |
| 控制面 / Control surface | 203 条用例：ISO、快门、曝光模式、变焦、模式切换、各档录像、延时、子弹时间 / 203 built-in sequences |

> 这里只写“怎么做成”，不写失败路线 / Only the working path is documented here, not the dead ends.

### 二·补、2026-11-19 追加突破 / Additional breakthroughs (2026-11-19)

详见 / see [`docs/breakthrough-2026-11-19.md`](docs/breakthrough-2026-11-19.md)。

| 项 / Item | 结果 / Result |
|---|---|
| 条带几何 / Strip geometry | 整帧被切成 **32 条 2048×32 横条**，每条独立 H.264 流；SPS 改 2048×32 后真 IDR **零 concealing** / the frame is 32 strips of 2048×32, each an independent stream; a real IDR decodes with **zero concealing** once the SPS is set to 2048×32 |
| 关键帧通道 / Keyframe channel | **关键帧通道 = 视频通道 + 0x20**（0x3a/0x3b/0x3f 自带 SPS+PPS+IDR）/ keyframe channel = video channel + 0x20 |
| 关键帧命令 / Keyframe command | **DUML cmd_id 0x38** 已破解，造包 CRC 对真机 **3/3 逐字节匹配** / cmd 0x38 cracked, builder byte-exact 3/3 against real packets |
| 连续取流 / Continuous capture | 单通道 **100% 帧覆盖、40 秒不断、589–592 包/秒恒定**（约 950KB/s）/ single channel 100% coverage, 40 s gapless, steady 589–592 pkt/s |
| 实时音频 / Live audio | `regular0` 原生 **4ch s24-32le**，**ch0/ch2 为真实麦克风** / native 4-channel s24-32le; ch0/ch2 are the real mics |

### 二·补2、2026-11-20 工程化落地 / Engineering landed (2026-11-20)

详见 / see [`docs/breakthrough-2026-11-20.md`](docs/breakthrough-2026-11-20.md)。

| 项 / Item | 结果 / Result |
|---|---|
| 两路流厘清 / Two streams separated | **预览小流 = 2048×1024 整帧双鱼眼 + CABAC**（手机画面）；全景分片流 = 2048×32×32 + CAVLC，二者 SPS/PPS 完全不同 / preview small stream = full 2048×1024 dual-fisheye CABAC (the phone picture); panorama strips = 2048×32×32 CAVLC, distinct SPS/PPS |
| 干净解码自证 / Clean decode proven | IDR 起始样本用 SPS0+`68ee06f2c0` 解出**完全干净双鱼眼**（人物/显示器/书架清晰）/ an IDR-started sample decodes to a perfectly clean dual-fisheye image |
| CAVLC PPS / CAVLC PPS | 扫出全景流最佳 PPS `68ce3880`；关键 `deblocking_filter_control_present_flag=0` / best panorama PPS `68ce3880`; deblock flag must be 0 |
| 通用 CRC / Generic CRC | `crc16(0x3692,0x8408)` over `DUML[0:-2]` 匹配所有长度；可把手机配置命令注入本会话 / matches all lengths; phone config commands can be injected into the local session |
| 本地播放器 / Local player | `preview.py` 打开即见预览窗口 + `help` 控制台；`start_preview.bat` 一键启动 / open-and-see preview window + `help` console; one-click launcher |

---

## 三、设备文件与系统结构 / Device layout and system structure

### 3.1 关键可执行与库 / Key executables and libraries

| 路径 / Path | 作用 / Role |
|---|---|
| `/usr/bin/dji_sys` | 主机状态机，**12 秒闸门**就在它的 `sys_host_check_status_ti` 里 / host state machine; the 12-s gate lives here |
| `/usr/bin/dji_cst` | 相机自带用例执行器，203 条序列 / built-in test-case runner (203 sequences) |
| `/usr/bin/dji_camera3` | 持 `/dev/video32`、`/dev/video33`，**编码器实例的创建者** / owns the video nodes, creates the encoder instance |
| `/usr/lib/libduml_frwk.so` | 传输层滑动窗口 ACK / transport sliding-window ACK |
| `/usr/lib/gstreamer-1.0/libgstdjiwlc2venc.so` | 编码器插件，`idr_interval` 的读取者 / encoder plugin reading `idr_interval` |
| `/usr/lib/libdcam_frwk_gst_eng.so` | 采集与编码框架 / capture-encode framework |
| `/usr/include/duml_hal_venc.h` | VENC 属性枚举（`IDR_INTERVAL = 10` 等）/ VENC property enums |

### 3.2 运行时目录 / Runtime directories

| 路径 / Path | 说明 / Notes |
|---|---|
| `/dev/shm` | tmpfs，**抓包写这里**（`/data` 只有 ~94 MB，写满会出事）/ tmpfs; write captures here |
| `/data` | overlay，94 MB，极易满 / overlay, only 94 MB |
| `/blackbox/camera/log/cam.log` | 相机主日志（`CST_RLT`、`print_c2_venc_property` 都在这里）/ main camera log |
| `/blackbox/gui/log/gui.log` | 屏幕 GUI 日志 / screen GUI log |
| `/mnt/media_rw/sd` | SD 卡挂载点（需手动挂载）/ SD mount point (mount manually) |
| `/data/local/tmp` | 脚本默认放置位置 / where the scripts live |

### 3.3 设备节点 / Device nodes

| 节点 / Node | 用途 / Purpose |
|---|---|
| `/dev/video32`、`/dev/video33` | `dji_camera3` 占用的采集口 / capture nodes held by `dji_camera3` |
| `/dev/snd/pcmC0D5c` | 音频采集，被 `pulseaudio` 独占 / audio capture, held by `pulseaudio` |
| `/dev/mmcblk1p1` | SD 卡块设备（exfat）/ SD card block device |

### 3.4 服务分工 / Services

```
dji_system.service   -> dji_sys        主机状态机 / host state machine
dji_camera3          -> 采集 + 编码实例 / capture + encoder instance
dji_sw_uav           -> 0.0.0.0:9004   单客户端传输宿主 / single-client transport host
dji_media_server     -> libmedia_uvc.so
gui                  -> 屏幕 UI / screen UI
pulseaudio           -> 音频 / audio
```

---

## 四、详细使用 / Detailed usage

### 4.1 准备 / Prerequisites

**中文**
1. 设备已解锁（见文末说明）。
2. PC 装好 `adb`，相机接 USB，`adb devices` 能看到序列号；或走网络 adb（`adb connect <ip>:5555`）。
3. 把 `probe/` 下的脚本推到相机：`adb push probe/ /data/local/tmp/`。
4. 备一份真机会话抓包当“开场模板”（本仓库不附带）。

**English**
1. The device is unlocked (see the note at the end).
2. `adb` installed on the PC; camera on USB and visible in `adb devices`, or use network adb
   (`adb connect <ip>:5555`).
3. Push the scripts: `adb push probe/ /data/local/tmp/`.
4. Keep one genuine session capture as the "opening template" (not bundled here).

### 4.2 关掉 12 秒闸门 / Disable the 12-second gate

```bash
adb shell python3 /data/local/tmp/gate_off.py
# 输出 after write=14000016 OK 即成功 / expect "after write=14000016 OK"
```

**中文** — `dji_sys` 的 `sys_host_check_status_ti` 中，文件偏移 `0x5d2f8` 处的 `b.ne`（`54 00 02 c1`）
改成无条件 `b`（`14 00 00 16`），“重试耗尽 → 判死”这条分支永不进入。只改内存，重启服务即恢复原样。

**English** — Inside `sys_host_check_status_ti` of `dji_sys`, the `b.ne` at file offset `0x5d2f8`
(`54 00 02 c1`) is rewritten to an unconditional `b` (`14 00 00 16`), so the
"retries exhausted → session down" branch is never taken. Memory only; a service restart restores it.

### 4.3 拿到关键帧 / Obtain the keyframe

**中文** — 编码器配置为 `idr_interval = 1000000`，即**只在实例创建时吐一个 IDR**。所以顺序很关键：
**先让客户端连上，再中途重启编码器进程**，让“实例创建”发生在有消费者在场时。

**English** — The encoder runs with `idr_interval = 1000000`, i.e. it emits exactly one IDR when the instance
is created. Order matters: **attach the client first, then restart the encoder process mid-session**, so that
instance creation happens while a consumer is already listening.

```bash
adb shell 'systemctl restart dji_sw_uav; sleep 3;
  tcpdump -i lo -n -s 0 -w /dev/shm/sX.pcap udp & sleep 1;
  (python3 -u /data/local/tmp/lv_fix.py /dev/shm/mimo.pcap 32 20 >/dev/shm/fx.log 2>&1 &);
  sleep 6; systemctl restart dji_camera3; sleep 24; kill %1'
adb pull /dev/shm/sX.pcap samples/sX.pcap
```

### 4.4 找通道并解码 / Locate the channel and decode

```bash
python probe/ch_sum.py samples/sX.pcap                     # 每通道字节数 / SPS 偏移 / IDR 偏移
python probe/cut_from.py samples/sX.pcap <SPS偏移>          # 生成 samples/_dec.h264
ffmpeg -y -f h264 -i samples/_dec.h264 -fps_mode passthrough out_%03d.png
```

**中文** — `ch_sum.py` 会打印形如 `ch 0x0d bytes 9130 SPS 17 IDR 48` 的行，那条就是带参数集的预览小流；
把 SPS 偏移交给 `cut_from.py`，即可切出直接可解码的 ES 流。

**English** — `ch_sum.py` prints lines like `ch 0x0d bytes 9130 SPS 17 IDR 48`; that channel carries the
parameter sets. Pass its SPS offset to `cut_from.py` to slice a directly decodable elementary stream.

### 4.5 实时观看 / Live viewing

**中文** — 解码端只要拿到**一次** IDR，之后靠零丢包一直解 P 帧即可，不需要周期性关键帧。

**English** — Once the decoder has a single IDR, the lossless P-frame stream keeps decoding; no periodic
keyframe is needed.

```bash
ffmpeg -f h264 -i - -fps_mode passthrough -f sdl "Osmo Preview" < stream.h264
```

### 4.6 控制面 / Control surface

```bash
cd /usr/lib
LD_PRELOAD=/data/local/tmp/hk/libdcam_hook.so:libqcom_adapter.so \
  dji_cst -d cst -u -f -m <序列名> -l /tmp/x.log
grep -a -E "CST_RLT|CST_Summary" /tmp/x.log     # PASSED / FAILED
```

常用序列 / Useful sequences：

```
duss_liveview_6000x3000_pano_video_p25    预览档 / liveview profile
duss_mode_profile_switch                  模式切换 / mode switch
Binder_SetISO  SetShutterSpeed  SetExpoMode  参数 / parameters
duss_record_6000x3000_video_pano_p25_mp4  全景录像 / panorama record
```

录像前先确认存储 / before recording, check storage：

```bash
mkdir -p /mnt/media_rw/sd
mount -t exfat /dev/mmcblk1p1 /mnt/media_rw/sd
df -h /mnt/media_rw/sd
```

### 4.7 条带几何与关键帧通道 / Strip geometry and keyframe channels

**中文** — 用 `sps_retool.py` 把 SPS 宽高改成 2048×32（`rebuild(sps,128,2)`），保留相机原 PPS；
用 `hi_ch.py` 提取 ≥0x28 的关键帧通道（= 视频通道 + 0x20）；用 `gen38.py` 的 `build38(seq)` 周期发送
0x38 关键帧请求；`lv_full.py` 把“握手 + 0x92 循环 + 0x38 + ACK”四线程合在一起，可拿到 100% 连续单通道流。

**English** — Use `sps_retool.py` to rewrite the SPS to 2048×32 (`rebuild(sps,128,2)`), keeping the camera
PPS; `hi_ch.py` extracts the keyframe channels (≥0x28, = video channel + 0x20); `gen38.py`'s `build38(seq)`
sends periodic 0x38 keyframe requests; `lv_full.py` combines handshake + 0x92 cycle + 0x38 + ACK in four
threads to obtain a 100% continuous single-channel stream.

```bash
python probe/hi_ch.py samples/capture.pcap        # 找关键帧通道 / find keyframe channels
python probe/gen38.py                              # 自验 0x38 造包 3/3 / self-check 3/3
python probe/sps_retool.py                         # 列出各高度 SPS / list SPS per height
```

### 4.8 实时麦克风音频 / Live microphone audio

**中文** — `regular0` 原生 4ch s24-32le，**ch0/ch2 是真实麦克风**。只采集、不播放：

**English** — `regular0` is natively 4-channel s24-32le; **ch0/ch2 are the real mics**. Capture only, never play:

```bash
timeout 4 parec --raw --rate=48000 --channels=4 --format=s32le -d regular0 /dev/shm/aud4.raw
python probe/aud_rms.py aud4.raw 4                 # 逐声道 RMS / per-channel RMS
python probe/aud_extract.py aud4.raw mic.wav       # ch0+ch2 -> 立体声 wav / stereo wav
```

### 4.9 本地预览播放器 + 控制台 / Local preview player + console

**中文** — 最简单的用法：相机端起中继，PC 端开窗口。

```bat
:: 一键（推脚本 + 起中继 + 开预览窗口）/ one-click
start_preview.bat
:: 或手动 / or manually
adb shell sh /data/local/tmp/run_relay.sh
python preview.py
:: 无需相机，回放干净双鱼眼样本 / replay a clean sample without the camera
python preview.py --file samples\live20.h264
```

窗口打开后，在控制台输入 `help`：
`stat`（统计）/ `kick`（重启 camera3 逼 IDR）/ `rehand`（重握手）/ `ackn <n>` / `reqms <s>` / `kf` / `save`（截图）/ `quit`。

**English** — Easiest path: start the relay on the camera, open the window on the PC.
Run `start_preview.bat` (one-click), or `run_relay.sh` then `python preview.py`.
`python preview.py --file samples/live20.h264` replays a clean dual-fisheye capture without the camera.
In the console type `help`: `stat / kick / rehand / ackn <n> / reqms <s> / kf / save / quit`.

---

## 五、协议要点 / Protocol essentials

**中文** — 下行大包 1472 字节，剥壳取 `p[19:]`（当 `p[19:23] == 00 00 00 01`，否则 `p[20:]`）；
`p[16]` 类型、`p[17]` 通道号、`p[18]` 通道内序号。内层是 DUML：
`55 <len> <crc8> <发送方> <接收方> <seq 2B> <flags> <cmd_set> <cmd_id> <payload> <crc16>`。
上行 magic `80 40 34`，下行 magic `80 0e 8a`；`[6]` 为 `0x05` 请求 / `0x04` ACK / `0x00` 保活。
通用 DUML CRC = `crc16(init=0x3692, poly=0x8408)` over `DUML[0:-2]`。

**English** — Downstream large packets are 1472 bytes; strip the wrapper as `p[19:]` when
`p[19:23] == 00 00 00 01`, otherwise `p[20:]`. Field map: `p[16]` type, `p[17]` channel,
`p[18]` sequence within the channel. Inside is DUML:
`55 <len> <crc8> <sender> <receiver> <seq 2B> <flags> <cmd_set> <cmd_id> <payload> <crc16>`.
Uplink magic `80 40 34`, downlink `80 0e 8a`; `[6]` is `0x05` request / `0x04` ACK / `0x00` keep-alive.
Generic DUML CRC = `crc16(init=0x3692, poly=0x8408)` over `DUML[0:-2]`.

---

## 六、仓库结构 / Repository layout

```
preview.py                     PC 预览窗口 + help 控制台 / PC preview window + console
start_preview.bat              一键启动 / one-click launcher
docs/预览总文档.md             中文详细文档 / detailed guide (Chinese)
docs/preview-guide-en.md       English detailed guide
docs/breakthrough-2026-11-19.md  2026-11-19 追加突破（中英）/ additional breakthroughs (bilingual)
docs/breakthrough-2026-11-20.md  2026-11-20 工程化落地（中英）/ engineering landed (bilingual)
probe/lv_relay.py              相机端预览中继（流控+组帧+TCP服务）/ camera relay
probe/run_relay.sh             起中继 / start relay
probe/kill_relay.sh            停中继 / stop relay
probe/gate_off.py              关闸门 / gate patch (idempotent, verifies original bytes)
probe/lv_fix.py                最小客户端 / minimal client
probe/lv_full.py               完整客户端：握手+0x92+0x38+ACK / full client
probe/lv_pipe6.py              原始转发客户端 / raw forwarding client
probe/reasm5.py                全景分片重组器 / panorama strip reassembler
probe/pc_recv7.py              预览小流重组器 / preview small-stream reassembler
probe/pps_find.py              PPS 搜索 / PPS search
probe/build_cfg.py             通用 CRC 造配置包 / config packet builder
probe/extract_cfg.py           提取手机配置命令 / extract phone config commands
probe/ch_sum.py                通道统计 / per-channel SPS & IDR offsets
probe/cut_from.py              切片出 ES 流 / slice the elementary stream
probe/oneshot.sh               一键跑完整套 / one-shot pipeline
probe/sps_retool.py            SPS 宽高字段重写 / rewrite SPS width/height
probe/gen38.py                 0x38 关键帧请求造包 / build 0x38 keyframe request
probe/hi_ch.py                 提取关键帧通道 / extract keyframe channels
probe/cmd_time.py              命令与关键帧因果对齐 / command/keyframe causal alignment
probe/aud_rms.py               音频逐声道 RMS / per-channel audio RMS
probe/aud_extract.py           提取真实麦克风 wav / extract real-mic wav
```

---

## 七、未来研究方向 / Future work

**中文**
- 脱离 USB 线：走网络直接取流与推流，彻底摆脱线缆。
- 用第三方软件完全控制相机（参数、模式、录像），应该不会很难。
- 尝试尽可能提高传感器的上限，例如全景照片的 RAW 格式。

**English**
- Go fully wireless: stream and control over the network, dropping the USB cable entirely.
- Complete third-party camera control (parameters, modes, recording) — should not be difficult.
- Push the sensor's limits, for example RAW capture for panorama photos.

---

## 八、设备解锁说明 / Unlocking note

（本句不是AI写的）
以上都需要你在你的 osmo360 设备解锁的情况下操作，你可以在 bilibili 上搜索 @凛青Linnea 发布的
DJI action5pro 的解锁方式的视频 BV1qr3t6MEGV

(This sentence was not written by AI.)
All of the above requires operating on your Osmo 360 **with the device unlocked**. You can search Bilibili for the
DJI Action 5 Pro unlocking video **BV1qr3t6MEGV** published by **@凛青Linnea**.

---

## 九、再次声明 / Disclaimer, again

> **EN** — Everything here is for learning and research only, on hardware you own and are allowed to modify.
> No warranty, no liability, no affiliation with DJI. Do not redistribute it as a paid service or a cracking tool.
>
> **中文** — 本仓库全部内容仅供学习研究，仅限在本人所有且获准改造的设备上使用。不提供担保、不承担责任、
> 与 DJI 无隶属关系。不得作为付费服务或破解工具再分发。
