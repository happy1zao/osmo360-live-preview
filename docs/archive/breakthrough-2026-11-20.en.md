> **Archived round log (English)** / 历史归档（英文译本）.
> Early reverse-engineering round log. Some scripts and samples referenced here were not
> published in this repo; see [../../tools/pc/README.md](../../tools/pc/README.md) and
> [../../samples/README.md](../../samples/README.md); the device serial is redacted to
> `<device-serial>`. Do not run the commands verbatim; follow the root README.

# Preview engineering landed (2026-11-20)

> Working results only, no failed routes. Continues `breakthrough-2026-11-19.en.md`.

## 1. Two streams finally separated

The root cause of past gray/mosaic frames was conflating two H264 streams with different
geometry and parameter sets:

| Stream | Geometry | Entropy coding | SPS | PPS |
|---|---|---|---|---|
| **Preview small stream (what the phone shows)** | **full 2048×1024 frame** (dual-fisheye 2:1) | CABAC | `67640028acb40100040d3501010106d0a135` | `68ee06f2c0` |
| Panorama strip stream | 2048×32 strip ×32 | CAVLC | `67640028acb40100b4d40404041b4284d7` | `68ce3880` (deblock=0) |

An IDR-started sample of the preview small stream, decoded directly with SPS0 +
`68ee06f2c0`, yields a **perfectly clean dual-fisheye image** (person, monitor, bookshelf,
lamp all clear) — the target "phone-like" quality, self-verified.

## 2. Correct reassemblers

19-byte downstream header: `byte[16]=fid`, `byte[17]=channel`, `byte[18]=fragment`.
- Preview small stream: `k=byte[17]>>7` (left/right fisheye), `i=byte[18]` (intra-frame
  packet order, 0-based); a new frame starts at `i==0`; a missing packet drops the whole
  frame.
- Panorama strip stream: `frag=byte[18]&0x1F` (high 3 bits are slice-type flags); packets
  resume aligned by fid, with fewer dropped fragments.

Two reassemblers: `pc_recv7.py` and `reasm5.py` (both unpublished).

## 3. CAVLC PPS search

`pps_find.py` (unpublished) scans entropy × deblock × qp × chroma, using rawvideo frame
output as the criterion. Key finding: for the CAVLC stream the
`deblocking_filter_control_present_flag` must be 0, otherwise frames are rejected with
`deblocking_filter_idc 13 out of range`. Best panorama PPS = `68ce3880`.

## 4. Generic DUML CRC + config injection

**Generic CRC = `crc16(init=0x3692, poly=0x8408)` over `DUML[0:-2]`** (including the
0x55 and length bytes), matching all sampled lengths. `extract_cfg.py`, `build_cfg.py`
(both unpublished) extract 12 phone uplink commands (including ASCII "APP" registration,
extended start, topic subscribe); `build_cfg.py` re-targets them to the local session shell;
after injection the slice size varies with content, proving the config takes effect.

## 5. Local preview player + console

New `preview.py` (PC): opens a preview window with a `help` console
(`stat / kick / rehand / ackn / reqms / kf / save / quit`). Camera-side `lv_relay.py`
combines phone-handshake subscribe + 52 ms periodic request + ACKN=4 ACK + periodic
handshake replay + dual-fisheye framing, served over TCP 5010 (video) / 5011 (control).
`start_preview.bat` is one-click. `python preview.py --file samples/live20.h264` replays a
clean dual-fisheye capture without the camera.

## 6. Flow sustain and IDR

Both ACK schemes sustain 530–600 pkt/s. The encoder emits IDR only at instance creation; a
late-attached session gets P-frames only (gray). The relay catches IDR via `kick` (restart
camera3 + re-handshake) and periodic handshake replay; once an IDR is present the picture
is clean (the player path is self-verified).
