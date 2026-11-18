# Osmo 360 Live Preview RE

Reverse-engineering notes and tooling for obtaining a **live preview stream directly from a DJI Osmo 360**
(Qualcomm QCS8550 / DJI customized Linux) on a PC — no phone app, no screen recording, no camera pointed at a screen.

> **DISCLAIMER — FOR STUDY AND RESEARCH ONLY / 免责声明 — 仅供学习研究**
>
> This repository documents personal research performed on a **device owned by the author**.
> It is published **strictly for learning and research purposes**: to understand how a consumer camera
> implements its internal live-preview pipeline and transport protocol.
>
> - The memory-patching technique described here **modifies only a running process's memory**. Nothing is
>   written to disk, nothing is flashed, and a service restart fully reverts it.
> - Do **not** apply any of this to a device you do not own, or without the owner's explicit permission.
>   Doing so may violate local law, the manufacturer's terms of service, or telecom regulations.
> - The author provides **no warranty** and accepts **no liability** for device damage, data loss,
>   warranty voiding, bricking, or any legal consequence arising from use of this material.
> - Do **not** use this for commercial circumvention, paid unlocking services, or any infringing activity.
> - All product names, trademarks and firmware belong to their respective owners. This project is
>   not affiliated with, endorsed by, or sponsored by DJI.
>
> If you are a rights holder and want content removed, open an issue and it will be taken down promptly.

---

## What this is

1. Defeat the camera's **12-second live-view session kill** by flipping one conditional branch in the
   `dji_sys` process memory (no disk change, revert by restarting the service).
2. Explain the **vendor transport protocol** (UDP header + inner DUML envelope) well enough to reassemble
   per-channel H.264 elementary streams out of a loopback packet capture.
3. Explain *why* the stream looks undecodable — the encoder runs with `idr_interval = 1000000`, i.e. it emits
   a single IDR at instance creation and never again — and provide the **trick that actually obtains a
   decodable keyframe**: attach a client first, then restart the encoder process mid-session.
4. Document the camera's built-in `dji_cst` test-case surface: 203 sequences covering ISO, shutter, EV,
   zoom, mode switching, every recording profile, timelapse, bullet-time and liveview profiles.

## Results so far

| Item | Status |
|---|---|
| 12-second session kill | defeated (multi-run, zero gaps, up to 78 s continuous) |
| Packet rate | 600-666 large packets/s ≈ 26 fps, duplicate packets = 0 |
| Keyframe (SPS/PPS/IDR) acquisition | works via the mid-session encoder restart trick |
| Single-frame decode | 2048x1024 decoded by ffmpeg with zero errors |
| Continuous PC preview | in progress (kick once for the I-frame, then rely on lossless P-frames) |
| Full 6000x3000 panorama decode | pending (six slices, no keyframe captured yet) |
| Camera control (ISO / shutter / modes / record) | case surface mapped, partially exercised |
| Audio | PCM path reached, level ~0, cause undetermined |

## Layout

```
docs/预览总文档.md        # the full write-up (Chinese): protocol, walls, patches, pitfalls
probe/gate_off.py         # apply the 12-s gate memory patch (idempotent, verifies original bytes)
probe/lv_fix.py           # minimal client: replay the app's opening sequence, keep the session alive
probe/ch_sum.py           # per-channel byte counts + SPS/IDR offsets for a pcap
probe/cut_from.py         # slice the per-channel elementary stream from a pcap starting at a target offset
probe/force_idr.py        # attempt to raise idr_interval in memory (does NOT work, kept as negative result)
```

## Quick start

```bash
# on the camera (root shell)
python3 gate_off.py                            # patch the gate in memory
# expect 14000016 at offset 0x5d2e8 of dji_sys

# attach a client, then restart the encoder mid-session to receive the only IDR
systemctl restart dji_sw_uav; sleep 3
tcpdump -i lo -n -s 0 -w /dev/shm/x.pcap udp &
python3 lv_fix.py /dev/shm/mimo.pcap 32 20 &
sleep 6
systemctl restart dji_camera3

# on the PC
python probe/ch_sum.py samples/sX.pcap        # find the channel carrying SPS/IDR
python probe/cut_from.py samples/sX.pcap <sps-offset>
ffmpeg -f h264 -i samples/_dec.h264 -fps_mode passthrough out.png
```

## Safety notes

- Never write captures to `/data` — the overlay is ~94 MB and fills up. Use `/dev/shm`.
- Do **not** restart `dji_media`: it takes down the whole userspace (adbd and HTTP die, power cycle needed).
- Stopping `gui` kills USB adb.
- The 12-s gate patch lives only in process memory: re-apply after every reboot.
