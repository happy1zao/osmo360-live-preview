> **Archived round log (English)** / 历史归档（英文译本）.
> Early reverse-engineering round log. Some scripts and samples referenced here were not
> published in this repo; see [../../tools/pc/README.md](../../tools/pc/README.md) and
> [../../samples/README.md](../../samples/README.md); the device serial is redacted to
> `<device-serial>`. Do not run the commands verbatim; follow the root README.

# Round 11 addendum — 0x0100 is the App host: after cold boot "App connected" cannot be rebuilt

> 2026-11-23, follows doc97. The three conclusions here correct half the judgment in the
> previous document, and record the real availability of the camera's own test tools
> `dji_cst` / `duss_shell`.

## 1. Correction: `subscribe_module(0x0100) failed, ret:0xe0` is not "the video module is broken"

The cam.log SysMode entry is the hard evidence:

```
SysModeManagerServiceImpl::doReleaseWakelock, 0x0100, 2 APP
sysmode_resource_lock_release: type->2, host_id->0x0100, name->APP
```

**host_id 0x0100 = the App (phone-side) host.** So `dji_camera3` retrying
`subscribe_module(0x0100)` once per second really means:

> The camera believes **no client is recognized as the App right now**, so it retries
> subscribing to the App's module every second.

It is not a missing video source, and our packet format is not wrong. **This is the direct
symptom of "nobody is recognized as the App".**

## 2. Media service: this machine's liveview channel is off by default

`/system/etc/dji_media_service.json`:

```json
"modes": {
  "liveview":        {"supportable": false},
  "wifi_broadcast":  {"supportable": true},
  "uvc_only":        {"supportable": true},
  "default_workmode": "uvc_only"
}
```

Together with the per-second `[UvcMgr] Read gadget state as 0x8002` in cam.log, the media
service is currently running in **uvc_only** (USB camera) mode. Nothing is disabled in
`capability_strategy`, but the media service's own liveview mode is off.

## 3. The camera's own test tools do work (we rely on them hereafter)

| Tool | Usage | Measured result |
|---|---|---|
| `dji_cst` | `LD_LIBRARY_PATH=/usr/lib LD_PRELOAD=libdcam_hook.so dji_cst -m <sequence-name> -L 60` | ✅ runs: `CST_RLT:duss_rec_mode_switch: PASSED`, CamX really reconfigured the stream |
| `dji_cst` DUSS send | internal `duss_event_send_sync` | ❌ `failed:-1003` (target host offline) |
| `duss_shell` | `duss_shell [config_id] dump/ping/msg_send ...` | ❌ fails at init: `duss_mb_create_route_table error: -1001` (the `shell` service is not registered in `/system/etc/dji.json`) |

`/system/etc/cst_cases.json` is a goldmine: **203 sequences**, 24 of them
`duss_liveview_*`, covering 6000×3000 / 7680×3840 panorama and 3840×1920 half-panorama,
frame rates from p24 to p240 (e.g. `..._pano_video_p25`). `duss_rec_mode_switch` passing
proves **the camera side is not broken** — only the "App channel" has nobody on it.

## 4. Key negative conclusion: after cold boot replay cannot make the camera recognize us as App

The `no_app_host` count (`subscribe_module(0x0100) failed`) stays steady at 1/s under all
three service-start orders:

| Order | frames | no_app_host / 40 s |
|---|---|---|
| server_first (replay first, kick camera3 after 1 s) | 0 | 65 |
| camera_first (kick camera3 first, wait 10 s, then replay) | 0 | 65 |

⇒ The "app opens preview" uplink packets in the tape are **invalid without a historical
session in camera memory**: they cannot reproduce the "App connected" state. The times a
stream did come out were when the camera **still held a real App session** (likely from an
earlier genuine Mimo connection); our replay only rode on top of it. Once the camera
restarts and that session is cleared, the tape cannot wake it up again.

## 5. Two follow-up routes

1. **Re-establish a real App session once** (one-off): connect the camera to the phone
   Mimo once, then continue riding our replay on top of it. This route was historically
   verified effective; the cost is turning on camera Wi-Fi (user decision).
2. **Implement App host registration ourselves**: the real DUML `connect/handshake`
   packets are required (session id / checksum included). `samples/mimo_cold_handshake.pcap`
   only has the uplink, missing the camera→App response, so it cannot be assembled
   offline. Workable approach: capture a **bidirectional** handshake (the whole process of
   the phone connecting to the camera), then turn the handshake into an independent
   replayable module.

## 6. Reproduction commands

```
# Run one recording-mode switch sequence on the camera (safe, does not touch USB/adb)
LD_LIBRARY_PATH=/usr/lib LD_PRELOAD=libdcam_hook.so dji_cst -m duss_rec_mode_switch -L 20

# Check "is there an App" (1/s growth = none)
adb -s <device-serial> shell 'grep -c "subscribe_module(0x0100) failed" /blackbox/camera/log/cam.log'

# PC side: two service-start orders compared (with no_app_host count)
SECS=40 ORDERS="camera_first;server_first" python tools/pc/r90_bootorder.py

# Use cst to raise liveview and see how many frames we receive at the same time
SECS=45 HOLD=60 python tools/pc/r91_cst_live.py
```
