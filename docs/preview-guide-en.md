# Osmo 360 Live Preview · Detailed Guide

> **Disclaimer** — This document is for study and research only, on a device you legally own and are allowed to
> modify. The technique described only changes the memory of a running process: nothing is written to disk,
> nothing is flashed, and a service restart reverts it. No warranty. No liability for damage or legal
> consequences. Not affiliated with DJI.

---

## 1. Goal and acceptance criteria

Obtain the Osmo 360's live preview stream directly on a PC — no phone app, no screen capture, no filming a screen.
Acceptance: smooth, no packet loss, no corrupted frames, no gaps.

## 2. Device and system structure

| Item | Value |
|---|---|
| Platform | Qualcomm QCS8550, DJI customized Linux (Debian-based) |
| Product code | `qcs8550_oq101` |
| Firmware | `01.06.01.04` |
| Access | root available; built-in Python 3.10.13; **no compiler** |

**Key files**

| Path | Role |
|---|---|
| `/usr/bin/dji_sys` | host state machine; the 12-second gate lives in `sys_host_check_status_ti` |
| `/usr/bin/dji_cst` | built-in test-case runner (203 sequences) |
| `/usr/bin/dji_camera3` | holds `/dev/video32`, `/dev/video33`; creates the encoder instance |
| `/usr/lib/libduml_frwk.so` | transport sliding-window ACK |
| `/usr/lib/gstreamer-1.0/libgstdjiwlc2venc.so` | encoder plugin; reads `idr_interval` |
| `/usr/include/duml_hal_venc.h` | VENC property enums |

**Key directories**

| Path | Notes |
|---|---|
| `/dev/shm` | tmpfs (1.2 GB) — write captures here |
| `/data` | overlay, only 94 MB, fills quickly |
| `/blackbox/camera/log/cam.log` | main camera log |
| `/blackbox/gui/log/gui.log` | screen GUI log |
| `/mnt/media_rw/sd` | SD card mount point (mount manually) |
| `/data/local/tmp` | where the scripts live |

**Services**: `dji_system` (state machine) / `dji_camera3` (capture + encode) / `dji_sw_uav`
(`0.0.0.0:9004`, single-client transport host) / `gui` (screen UI) / `pulseaudio` (audio).

## 3. Protocol

Downstream large packets are 1472 bytes:

```
[16] type   [17] channel   [18] sequence within channel   [19:23] == 00 00 00 01   [23:] payload
```

Strip as `p[19:]` when `p[19:23] == 00000001`, otherwise `p[20:]`.

Inner DUML:

```
55 <len> <crc8> <sender> <receiver> <seq 2B> <flags> <cmd_set> <cmd_id> <payload> <crc16>
```

Uplink magic `80 40 34`, downlink magic `80 0e 8a`; `[6]`: `0x05` request / `0x04` ACK / `0x00` keep-alive.

## 4. Breakthrough #1 — the session is no longer killed after 12 seconds

On the camera, `sys_host_check_status_ti` calls `sys_host_task_sm_down` once retries are exhausted, exactly
12 seconds in. Patch: file offset `0x5d2f8`, `b.ne` (`54 00 02 c1`) → unconditional `b` (`14 00 00 16`),
applied to process memory only.

```bash
python3 /data/local/tmp/gate_off.py     # expect "after write=14000016 OK"
```

Measured: 600–666 large packets/s (≈26 fps), 78 seconds continuous with zero gaps, zero duplicates.
Revert with `systemctl restart dji_system`.

## 5. Breakthrough #2 — obtaining SPS / PPS / IDR

Log evidence:

```
[ djiwlc2venc ] print_c2_venc_property: [liveview] wl_plugin config idr_interval : 1000000.
```

`idr_interval = 1000000` means the encoder emits **exactly one IDR at instance creation**, then P-frames only.
So instance creation must happen while we are already attached:

> **Attach the client first, then restart the encoder process mid-session.**

```bash
systemctl restart dji_sw_uav; sleep 3
tcpdump -i lo -n -s 0 -w /dev/shm/sX.pcap udp &
python3 /data/local/tmp/lv_fix.py /dev/shm/mimo.pcap 32 20 &
sleep 6
systemctl restart dji_camera3
```

Observed (21.4 MB / 22 s capture):

```
SPS @ 6,210,886   00000001 67 64 00 28 ac b4 01 00 04 0d 35 …
PPS @ 6,210,908   00000001 68 ee 06 f2 c0
IDR @ 6,210,917   00000001 65 …
```

## 6. Decoding

```bash
python probe/ch_sum.py samples/sX.pcap                  # per channel: bytes / SPS / IDR
python probe/cut_from.py samples/sX.pcap <SPS offset>   # -> samples/_dec.h264
ffmpeg -y -f h264 -i samples/_dec.h264 -fps_mode passthrough out_%03d.png
```

Result: `Stream #0:0: Video: h264 … 2048x1024, 25 fps`, zero errors.
The 31-byte parameter set is constant and reusable:

```
00000001 67640028 acb40100 040d3501 010106d0 a135
00000001 68 ee06f2c0
```

## 7. Channel map

| Channel | Bytes | SPS | IDR | Meaning |
|---|---|---|---|---|
| 0x20 / 0xa0 / 0x22 / 0xa2 / 0x21 / 0xa1 | 1.8–1.97 MB each | none | none | 6000×3000 panorama, six slices |
| 0x92 | 1.30 MB | none | present | channel carrying an I-frame |
| 0x0d | 9,130 B | present | present | 2048×1024 preview stream |

## 8. Live viewing

After one IDR, the lossless P-frame stream keeps decoding — no periodic keyframe required:

```bash
ffmpeg -f h264 -i - -fps_mode passthrough -f sdl "Osmo Preview" < stream.h264
```

## 9. Control surface

```bash
cd /usr/lib
LD_PRELOAD=/data/local/tmp/hk/libdcam_hook.so:libqcom_adapter.so \
  dji_cst -d cst -u -f -m <sequence> -l /tmp/x.log
grep -a -E "CST_RLT|CST_Summary" /tmp/x.log
```

| Category | Sequences |
|---|---|
| Liveview | `duss_liveview_6000x3000_pano_video_p25` |
| Modes | `duss_mode_profile_switch`, `duss_cap_mode_switch`, `duss_rec_mode_switch`, `duss_pano_rec_mode_switch` |
| Parameters | `Binder_SetISO`, `SetShutterSpeed`, `SetExpoMode`, `test_3a_ev_setting`, `test_dzoom_*` |
| Recording | `duss_record_*` (2688×1512 up to 7680×3840 panorama p60) |
| Photo | `osmo_shot`, `duss_single_capture_*` (including 4 s / 8 s long exposure) |

Mount the card before recording: `mkdir -p /mnt/media_rw/sd && mount -t exfat /dev/mmcblk1p1 /mnt/media_rw/sd`

## 10. Future work

- Go fully wireless: stream and control over the network
- Complete third-party camera control (parameters, modes, recording)
- Push the sensor's limits, e.g. RAW capture for panorama photos

## 11. Unlocking note

(This sentence was not written by AI.)
All of the above requires operating on your Osmo 360 **with the device unlocked**. You can search Bilibili for the
DJI Action 5 Pro unlocking video **BV1qr3t6MEGV** published by **@凛青Linnea**.

## 12. Disclaimer, again

Everything in this repository is for study and research only, on a device you own and are allowed to modify.
No warranty, no liability, no affiliation with DJI. Do not redistribute it as a paid service or cracking tool.
