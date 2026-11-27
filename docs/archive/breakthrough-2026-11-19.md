> **历史归档（中英双语，未另作英文重译）**
> 本文是早期逆向轮次记录。文中引用的部分早期脚本与样本未随仓库发布，
> 清单见 [../../tools/pc/README.md](../../tools/pc/README.md) 与 [../../samples/README.md](../../samples/README.md)；
> 设备序列号已脱敏为 `<device-serial>`。命令请勿直接照跑，以根目录 README 为准。

# 2026-11-19 追加突破 / Additional Breakthroughs (2026-11-19)

> 仅供学习研究 / For study and research only. 见 README 免责声明 / See the disclaimer in README.
> 本文只写已确证的突破结果 / Only confirmed, working results are documented here.

---

## 1. 条带几何真相 / The strip geometry truth

**中文** — 相机把 2048×1024 整帧切成 **32 条 2048×32 的横条**，每条是独立 H.264 流
（`first_mb_in_slice` 恒 0），却用整帧 SPS（2048×1024）来送条带数据。把 SPS 的宽高字段改成
2048×32 后，真 IDR **零 concealing 完全干净解码**，并首次解出手机侧真实画面内容。

**English** — The camera splits the 2048×1024 frame into **32 horizontal strips of 2048×32**, each an
independent H.264 stream (`first_mb_in_slice` always 0), yet sends them with the full-frame SPS
(2048×1024). Rewriting the SPS width/height fields to 2048×32 makes a real IDR decode with **zero
concealing**, revealing actual picture content for the first time.

| 声明几何 / Declared | 已解宏块 / Decoded MB | concealing |
|---|---|---|
| 2048×1024 | 378/8192 | 7814 |
| 2048×48 | 378/384 | 6 |
| **2048×32** | **256/256** | **0** |

- 原 SPS / original: `67 64 00 28 ac b4 01 00 04 0d 35 01 01 01 06 d0 a1 35`
- 2048×32 版 / 32-row version: `67 64 00 28 ac b4 01 00 b4 d4 04 04 04 1b 42 84 d7`
- PPS 保持相机原值 / keep the camera PPS: `68 ee 06 f2 c0`
- 工具 / tool: `probe/sps_retool.py`（只改两个 ue 字段，其余比特原样保留 / rewrites only the two ue fields）。

---

## 2. 关键帧通道与请求命令 / Keyframe channel and request command

**中文** — 通道号 ≥0x28 中存在专门送 SPS+PPS+IDR 的关键帧通道（0x3a/0x3b/0x3f），编号规律：
**关键帧通道 = 视频通道 + 0x20**（0x3a=0x1a+0x20 等）。触发它的上行命令是 **DUML cmd_id 0x38**，
App 每 2~5 秒发一次；上行 0x38 与关键帧通道首包因果对齐（+25~88ms）。

**English** — Channels ≥0x28 include dedicated keyframe channels (0x3a/0x3b/0x3f) carrying SPS+PPS+IDR.
Numbering rule: **keyframe channel = video channel + 0x20**. The uplink command that triggers them is
**DUML cmd_id 0x38**, sent by the app every 2–5 s; uplink 0x38 aligns causally with the keyframe channel's
first packet (+25–88 ms).

0x38 真机 43 字节包 / genuine 43-byte 0x38 packet:
```
2b 80 40 34 70 7f 05 d5 68 7f 70 7f 00 00 00 00 ae 01 00 00
55 17 04 38 02 41 <seq> 92 40 09 a8 00 04 02 00 00 00 00 00 00 00 <crc16 LE>
```
- seq 在整包偏移 26 / seq at packet offset 26; CRC 在 [41:43] / CRC at [41:43].
- CRC = `crc0(DUML[2:21]) ^ 0xB44F`（L=23 残差 / L=23 residual）; 外层 xor8 在字节 7 / outer xor8 at byte 7.
- 工具 / tools: `probe/gen38.py`（`build38(seq)`，对 3 个真机包 DUML 逐字节匹配 3/3 / byte-exact 3/3）,
  `probe/hi_ch.py`（提取 ≥0x28 通道 / extract high channels）, `probe/cmd_time.py`（未发布；因果对齐 / causal alignment）。

---

## 3. 100% 连续取流 / 100% continuous capture

**中文** — 复刻手机 0x92 的固定循环组（约 20ms 一发），配合滚动 ACK 与周期 0x38，单通道达到
**100% 帧覆盖、40 秒不断流、589–592 大包/秒恒定**（约 950KB/s，历史最高最稳）。

**English** — Replicating the phone's fixed 0x92 cycle (~20 ms per send) with rolling ACK and periodic 0x38
yields **100% frame coverage on a single channel, 40 s without a gap, a steady 589–592 large packets/s**
(~950 KB/s, the highest and steadiest yet).

0x92 循环组 / 0x92 cycle:
```
[0x14,0x14,0x09,0x28,0x08,0x15,0x08,0x20,0x29,0x29,0x06,0x0f,0x41,0x0a,0x41,0x18]
```
造包器 ch 参数用 **0x91** / use ch = **0x91** in the builder.
工具 / tool: `probe/lv_full.py`（未发布；四线程：握手回放 + 0x92 循环 + 0x38 关键帧 + ACK / four threads）。

---

## 4. 实时麦克风音频 / Live microphone audio

**中文** — PulseAudio 源 `regular0` 原生为 **s24-32le 4 声道 48kHz**（360 空间音频的 4 麦）。
按原生格式采集后，**ch0 与 ch2 是真实麦克风信号**（rms≈18000、非零 99.5%），ch1/ch3 为空参考声道。
之前“近零”是因为强制 s16le 2ch 下混选到了空声道。

**English** — The PulseAudio source `regular0` is natively **s24-32le, 4 channels, 48 kHz** (the four mics of
360 spatial audio). Captured in native format, **ch0 and ch2 carry real microphone signal** (rms≈18000,
99.5% non-zero); ch1/ch3 are empty reference channels. The earlier "near-zero" reading came from forcing
an s16le 2-channel downmix that landed on the empty channels.

```bash
# 只采集，不播放 / capture only, never play back
timeout 4 parec --raw --rate=48000 --channels=4 --format=s32le -d regular0 /dev/shm/aud4.raw
```
工具 / tools: `probe/aud_rms.py`（逐声道 RMS/峰值 / per-channel RMS & peak）, `probe/aud_extract.py`（取 ch0+ch2、>>8 转 s16 立体声 wav / extract ch0+ch2 to a stereo s16 wav）。

---

## 5. 剩余缺口（如实说明）/ Remaining gap (honest note)

**中文** — 连续 P 流要解出锐利整帧，还需让 IDR 与 P 流来自**同一条带**（条带身份匹配）。
自定义头里的 b1 是全局滚动序号、b8b9 是毫秒时间戳，均不含条带 id。两条候选路线：
(A) 让 0x38 真正触发我们订阅条带的关键帧通道；(B) 内存补丁编码器开启 GDR 自刷新。

**English** — To decode a sharp full frame from the continuous P stream, the IDR and the P stream must come
from the **same strip** (strip-identity matching). The custom header's b1 is a global rolling sequence and
b8b9 a millisecond timestamp; neither carries a strip id. Two candidate routes: (A) make 0x38 actually
trigger the keyframe channel of the strip we subscribe to; (B) memory-patch the encoder to enable GDR
self-refresh.
