# Round 22 — 2:1 Live Panorama to PC (zero disk writes) + 4K120 proof + UI slot swap

## TL;DR

| Item | Status | Evidence |
|---|---|---|
| 2048×1024 2:1 live preview on PC | ✅ works | `ffprobe` on grabbed PNGs: `2048x1024`; `proxy_*.bin` keeps growing |
| Zero disk writes | ✅ measured | long recording session running, `..._0098_D.OSV/.LRF` stay **0 bytes** |
| 4K120 panorama is real | ✅ re-proven | `0093`: two hevc `1920×1920@119.88`, 1238 frames / 10.33 s, 42.1 + 41.2 Mbps |
| 4K120 in the camera UI | ⏳ table patched, needs your eyes | block4 `1X1_1920P P100` replaced by `P120` in place, `num` still 12, camera3 stable, cst `PASSED` |

## Why nothing could record from 02:04

Not the hook, not the capability tables, not the battery:

```
storage-service: do_mount: end fsck /dev/sda12 exfat count 2!      # fsck run on an already-mounted volume → fails
storage-service: update_storage_state: id = emulated, old state = 1 new state = 6   # 6 = ERROR
```

cst then sees `storage[0] status 13 / free 0 / remain 0` → `Record` rejected with `-1002` →
`topmode_etr_report_action_result` hits `Assertion Failed: 0` at `cs_topmode_util.c:6574` → camera3 crash loop.

Fix, in this exact order (otherwise umount returns EBUSY and the service fsck-fails again):

```sh
kill cst; systemctl stop dji_camera3.service; systemctl stop dji_media_server.service
sleep 4; sync; umount /mnt/media_rw/emulated     # must print UNMOUNTED
systemctl restart storage_service.service
systemctl start dji_media_server.service; systemctl restart dji_camera3.service
```

`pano_live97.py` performs this health check + self-heal automatically.

## 4K120: what it actually looks like

`CAM_20261127023958_0093_D.OSV` (110 MB, 10.33 s): two identical hevc Main10 1920×1920 streams
at `120000/1001` fps, 1238 frames each, plus aac audio, per-frame `bin_data`, and an mjpeg 688×344
(2:1) thumbnail. Triggered purely by `VIDEO_PANO` + `1X1_1920P` + `P120` on the **stock** camera tables.

## Where the 2:1 stream comes from

The proxy branch inside dcam's gst pipeline. probe97 turns it into:

```
... ! tee name=pte
pte. ! queue leaky=2 ! h264parse config-interval=-1
     ! video/x-h264,stream-format=byte-stream,alignment=au
     ! tcpserversink host=127.0.0.1 port=8888 sync=false
pte. ! queue ! multifilesink location=/dev/shm/dcam_rec/proxy_%05d.bin max-files=24
pte. ! queue ! appsink name=proxy_sink        # original consumer kept
```

The encoder emits **AVCC** (length-prefixed), so `h264parse → byte-stream` is mandatory;
without it `ffmpeg` reports `Could not find codec parameters / unspecified size`.

All file writes for `.OSV/.LRF/.MET/.trinf` are redirected to a spool path where
`write/pwrite` only accumulate a logical size, `fallocate/ftruncate/fsync` are faked —
so recording stays alive while nothing real is written.

### Three dead ends (do not retry)

| Variant | Result | Lesson |
|---|---|---|
| probe93: OSV faked, **LRF really written to /dev/shm** | dies ~1.7 s | touching the LRF for real kills the session |
| probe95: OSV+LRF both on the same tmpfs (same `st_dev`) | dies ~1.7 s | not a cross-device check; LRF must stay fake |
| probe90: fake writes + `gst_app_sink_pull_sample` hook | dies ~1.7 s | pull_sample hook is a red herring; tee the pipeline instead |

## UI 4K120 = in-place swap, never append

`/system/etc/capability.json` → `camcap_video_format[4]` has exactly 12 entries and `num == 12`;
the last one is `1X1_1920P + P100`. Appending an entry (num+1) puts camera3 into a core-dump loop.
`r185_ui_swap_120.py` rewrites that single entry to `P120` without changing `num`, then:

```bash
adb push samples/capability_ui_4k120_swap.json /data/local/tmp/cap_swap120.json
adb shell 'mount -o remount,rw /; cp -f /data/local/tmp/cap_swap120.json /system/etc/capability.json; mount -o remount,ro /; systemctl reset-failed dji_camera3.service; systemctl restart dji_camera3.service'
```

Post-deploy: camera3 active, `cst_camdev init PASSED`, `duss_record_3840x1920_video_pano_p120_mp4 PASSED`.

Trade-off: 100 fps disappears from the 4K slot (it is a replacement, not an addition). Keeping both
would require resizing the fixed-size array inside the binary — out of scope for now.

## Usage

```bash
python tools/pc/pano_live97.py            # live preview (self-heal + session + ffplay)
python tools/pc/pano_live97.py --dump 6   # grab 6 PNGs instead
python tools/pc/pano_live97.py --stop     # stop the session
```

Grabbed frames land in `samples/rec/live/live_01..06.png`, confirmed `2048x1024`.
They look black because the room was unlit at 03:1x (the real 4K120 recording `0093` has
mean≈2.9 / p99≈6 too) — scene darkness, not a broken stream.

## Rollback

```bash
adb push probe/restore_stock.sh /data/local/tmp/ && adb shell sh /data/local/tmp/restore_stock.sh
```

Restores the stock `libdcam_hook.so` (`4e1c7d45…`), stock camera capability tables and stock
`capability.json` (`db81d2d6…`), then restarts camera3.
