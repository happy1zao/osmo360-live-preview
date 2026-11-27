> **Archived round log (English)** / 历史归档（英文译本）.
> Early reverse-engineering round log. Some scripts and samples referenced here were not
> published in this repo; see [../../tools/pc/README.md](../../tools/pc/README.md) and
> [../../samples/README.md](../../samples/README.md); the device serial is redacted to
> `<device-serial>`. Do not run the commands verbatim; follow the root README.

# 89 — This round's fixes: scripts would not run, the real gray-screen cause, IDR must be sent / cached

Date: 2026-11-20

## 1. The real reason the user reported "python3 .py errors"

This Windows machine has no `python3` command (`where python3` → not found); only
`python` (3.13.18) exists. So the command must be written as:

```
python preview.py
```

It was not a script syntax error. `python -m py_compile preview.py` passes, and
`python preview.py --help` works normally.

## 2. Four real bugs found and fixed

| # | Defect | Symptom | Fix |
|---|---|---|---|
| 1 | `ffmpeg -stream_loop -1` applied to a raw H264 stream | ffmpeg exits immediately; log shows `Operation not permitted` / `Terminating thread with error`; no picture in the window | Playback now feeds stdin from Python in ~2 MB/s chunks; ffmpeg is no longer asked to seek |
| 2 | Crash stack when adb is not on PATH | `FileNotFoundError: [WinError 2]` with a full traceback | Fall back to a known adb path; if truly absent, print a Chinese hint and `SystemExit` |
| 3 | Under high DPI the OpenCV window could open off-screen | Process and window title exist, but the window is invisible on screen | After the first `imshow`, `moveWindow(60,60)` + `WND_PROP_TOPMOST` |
| 4 | Three defects in the bat file | ① `where` on a missing PATH entry falsely reports "python not found" ② LF line breaks → cmd splits lines and reports `'"' is not an internal command` ③ `setsid` does not exist on Android | ① probe directly with `python --version` ② unify CRLF / no BOM ③ use `nohup sh ... &`; kill and start run as two separate adb shell calls |

## 3. Another subtle trap: kill_relay.sh killed itself

`adb shell "sh kill_relay.sh; ... python3 lv_relay.py ..."` — the whole command line
contains `lv_relay.py`. The old script matched by cmdline grep and would `kill -9` the very
shell running it (exit code 137).

The new script treats the relay as its own process only when the **first token of the
cmdline is python3/python**; the wrapping shell is not touched.

## 4. The real gray-screen cause (important)

The standing conclusion was "the IDR appears only at instance creation; a late attach
cannot get it". This round located the **other half**:

> **The IDR actually arrives often, but the reassembler drops it under the "drop whole
> frame on missing packets" rule.**

An IDR frame is usually the longest frame (most packets) and the most prone to gaps.
The old `flush()`:

```python
if c["bad"]:
    st["drop"] += 1        # drop the whole frame — including the only IDR
```

So:
1. IDR arrives → judged bad → dropped
2. All following P-frames have no reference frame → ffmpeg can only output gray
3. It looks like "the IDR can never be captured"

**Fix**: keyframes (NAL 0x65 or SPS 0x67) are **sent even with gaps**. The first frame may
be locally corrupted; later P-frames recover immediately. Duplicate packets (`i == prev`)
are no longer counted as a gap.

## 5. IDR cache resend (new mechanism)

`lv_relay.py` adds `idr_cache[2]`: it keeps the full body of the most recent keyframe for
each eye. Any **newly attached client** first receives `SPS+PPS+cached_IDR` after `accept`,
then enters the live stream.

Meaning: there is no need to "happen to be connected at the exact moment the encoder is
created"; a reference frame exists as soon as you connect. The console `stat` now shows
cache size `cache=x/y`, and an `idr` command clears the cache.

## 6. ACK parameters measured

| Setting | Result |
|---|---|
| ACKN=4, reqms=52ms | ~22 fps, drop ≈5%/frame |
| **ACKN=1, reqms=30ms** | **~25 fps** (ok increment ≈25/s), drop clearly lower |

## 7. Cautionary tale: the dji_cst pano sequence cannot be used to force an IDR

After running `duss_liveview_6000x3000_pano_video_p25`, the stream became a
**2048×32×32 strip stream** (CAVLC); decoding with SPS0 (2048×1024) yields
**horizontal striped gray bands** (`samples/idr_snap3.png`), not the phone's 2048×1024
preview stream.

Conclusion:
- The phone/screen preview stream (2048×1024 CABAC) is created by the **GUI preview**, not
  this cst sequence.
- The correct order to force an IDR: `restart dji_camera3` → **the GUI re-enters live
  view** (instance created + IDR emitted).
- Restarting without letting the GUI restore the preview **stops the stream** (measured:
  big stopped growing from 90320 to 90374).

## 8. Current blocker (needs a person at the camera)

After the last kick, the camera sat on the **USB connection screen** (gui.log:
`usb busy true`, `DeviceRunning(false)`); the GUI did not return to live view, the encoder
instance was not rebuilt, and the stream had stopped.

**Recovery action**: on the camera screen, exit USB file transfer / return to the shooting
screen so live view reappears. After that, once the relay attaches it first receives the
cached IDR (if the cache is empty, wait for the next instance creation); the picture
should be clean immediately.

## 9. File changes

- `preview.py`: ffmpeg playback fix, adb fallback and hints, window topmost/move, new
  `--snap/--snap-frames` unattended frame grabbing
- `start_preview.bat`: rewritten probe logic, CRLF, removed setsid
- `probe/lv_relay.py`: keyframe always sent, duplicate packets ignored, IDR cache resend,
  `idr` clear-cache command
- `probe/kill_relay.sh`: kills only the real python3 relay process

The backup directory was not published with the repo: `backup/20261120_fix_bat_preview/`
