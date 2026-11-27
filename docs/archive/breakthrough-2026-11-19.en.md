> **Archived round log (English)** / 历史归档（英文译本）.
> Early reverse-engineering round log. Some scripts and samples referenced here were not
> published in this repo; see [../../tools/pc/README.md](../../tools/pc/README.md) and
> [../../samples/README.md](../../samples/README.md); the device serial is redacted to
> `<device-serial>`. Do not run the commands verbatim; follow the root README.

# Additional Breakthroughs (2026-11-19)

> For study and research only; see the disclaimer in README. Only confirmed, working results
> are documented here.

---

## 1. The strip geometry truth

The camera splits the 2048×1024 frame into **32 horizontal strips of 2048×32**, each an
independent H.264 stream (`first_mb_in_slice` always 0), yet sends them with the
full-frame SPS (2048×1024). Rewriting the SPS width/height fields to 2048×32 makes a real
IDR decode with **zero concealing**, revealing actual picture content for the first time.

| Declared geometry | Decoded MB | concealing |
|---|---|---|
| 2048×1024 | 378/8192 | 7814 |
| 2048×48 | 378/384 | 6 |
| **2048×32** | **256/256** | **0** |

- Original SPS: `67 64 00 28 ac b4 01 00 04 0d 35 01 01 01 06 d0 a1 35`
- 2048×32 version: `67 64 00 28 ac b4 01 00 b4 d4 04 04 04 1b 42 84 d7`
- Keep the camera PPS: `68 ee 06 f2 c0`
- Tool: `probe/sps_retool.py` (rewrites only the two ue fields, all other bits preserved).

---

## 2. Keyframe channel and request command

Channels ≥0x28 include dedicated keyframe channels (0x3a/0x3b/0x3f) carrying
SPS+PPS+IDR. Numbering rule: **keyframe channel = video channel + 0x20** (0x3a = 0x1a +
0x20, etc.). The uplink command that triggers them is **DUML cmd_id 0x38**, sent by the app
every 2–5 s; uplink 0x38 aligns causally with the keyframe channel's first packet
(+25–88 ms).

Genuine 43-byte 0x38 packet:
```
2b 80 40 34 70 7f 05 d5 68 7f 70 7f 00 00 00 00 ae 01 00 00
55 17 04 38 02 41 <seq> 92 40 09 a8 00 04 02 00 00 00 00 00 00 00 <crc16 LE>
```
- seq at packet offset 26; CRC at [41:43].
- CRC = `crc0(DUML[2:21]) ^ 0xB44F` (L=23 residual); outer xor8 at byte 7.
- Tools: `probe/gen38.py` (`build38(seq)`, byte-exact 3/3 against three genuine packets),
  `probe/hi_ch.py` (extract high channels), `probe/cmd_time.py` (unpublished; causal
  alignment).

---

## 3. 100% continuous capture

Replicating the phone's fixed 0x92 cycle (~20 ms per send) with rolling ACK and periodic
0x38 yields **100% frame coverage on a single channel, 40 s without a gap, a steady
589–592 large packets/s** (~950 KB/s, the highest and steadiest yet).

0x92 cycle:
```
[0x14,0x14,0x09,0x28,0x08,0x15,0x08,0x20,0x29,0x29,0x06,0x0f,0x41,0x0a,0x41,0x18]
```
Use ch = **0x91** in the builder.
Tool: `probe/lv_full.py` (unpublished; four threads: handshake replay + 0x92 cycle +
0x38 keyframe + ACK).

---

## 4. Live microphone audio

The PulseAudio source `regular0` is natively **s24-32le, 4 channels, 48 kHz** (the four
mics of 360 spatial audio). Captured in native format, **ch0 and ch2 carry real microphone
signal** (rms≈18000, 99.5% non-zero); ch1/ch3 are empty reference channels. The earlier
"near-zero" reading came from forcing an s16le 2-channel downmix that landed on the empty
channels.

```bash
# capture only, never play back
timeout 4 parec --raw --rate=48000 --channels=4 --format=s32le -d regular0 /dev/shm/aud4.raw
```
Tools: `probe/aud_rms.py` (per-channel RMS & peak), `probe/aud_extract.py` (extract
ch0+ch2, >>8 to an s16 stereo wav).

---

## 5. Remaining gap (honest note)

To decode a sharp full frame from the continuous P stream, the IDR and the P stream must
come from the **same strip** (strip-identity matching). The custom header's b1 is a global
rolling sequence and b8b9 a millisecond timestamp; neither carries a strip id. Two
candidate routes: (A) make 0x38 actually trigger the keyframe channel of the strip we
subscribe to; (B) memory-patch the encoder to enable GDR self-refresh.
