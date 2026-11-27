# Status & Milestones

> Legend: ✅ code for this link is retained in the repo; 🟡 verified offline / single segment,
> end-to-end in progress; ⛔ attempted and blocked. This document references only scripts
> shipped in the repo; unpublished early scripts are listed in `tools/pc/README.md`.

## Link status

| Stage | Status | Notes | Code |
|---|---|---|---|
| adb / root / file transfer | ✅ | device adb root works; scripts pushed to `/data/local/tmp` | `osmo360/transport.py` |
| Process & library inventory, 9004 protocol draft | ✅ | downstream packet header parsing, channel model, fragment reassembly | `osmo360/video.py` |
| Offline dual-fisheye frame reassembly | ✅ | frame-by-frame decode from a capture, conceal=0 | `osmo360/pipeline.py`, `osmo360/video.py` |
| 12-second flow-control gate patch | ✅ | memory-only; revert = restart `dji_system` | `probe/gate_off.py` |
| Camera-side relay push | ✅ | frame assembly → TCP 5010 (video) / 5011 (control) | `probe/lv_relay.py`, `probe/run_relay.sh` |
| PC relay preview window | ✅ | ffmpeg decode + console commands | `preview.py` |
| Live-read-from-LRF 2:1 preview | 🟡 | v4 pulls NALs from GOF anchors; 2048×1024 while recording; long-haul P-frame stability still under observation | `tools/pc/r156_lrf_live2.py` |
| Offline frame-rate audit | ✅ | frames/duration/duplicates/frame-diff-vs-lag analysis on a self-supplied gray raw | `tools/pc/r149_fps_audit.py`, `tools/pc/r150_fps_lag.py` |
| Zero-write proxy stream tee | ✅ | hook tees one H.264 branch off the proxy pipeline; `.OSV/.LRF/.MET` faked writes, nothing on disk | `probe/hook_probe97.c`, `tools/pc/pano_live97.py` |
| In-place UI P100→P120 rewrite | ✅ | fixed-length array, no entry added/removed | `tools/pc/r185_ui_swap_120.py` |
| Self-built session without the phone | 🟡 | full cold-start replay triggers sustained push; continuous P-frame decode still pending | `osmo360/session.py`, `osmo360/live.py` |
| Device-side GPU/EGL/Vulkan transcode | ⛔ | no `libvulkan_adreno.so`, all three EGL backends fail; weston is pixman software compositing | — |

## Known blockers

- **GPU transcode**: no usable Vulkan/EGL backend on the camera; device-side hard decode/transcode
  is not feasible. Decoding happens on the PC.
- **Long-haul P-frame stability**: panorama slice P-frames after the first IDR are a different
  stream from the preview small stream; continuous decode stability is still being iterated on.
- **Samples are not committed**: captures, streams and screenshots cited by historical milestones
  are private acquisitions and are not in the repo (see `samples/README.md`).

## Index

- Getting started: [`00_START_HERE.en.md`](00_START_HERE.en.md)
- Detailed theory: [`preview-guide.en.md`](preview-guide.en.md)
- Round archives: [`archive/`](archive/)
