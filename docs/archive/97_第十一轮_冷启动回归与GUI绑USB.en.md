> **Archived round log (English)** / 历史归档（英文译本）.
> Early reverse-engineering round log. Some scripts and samples referenced here were not
> published in this repo; see [../../tools/pc/README.md](../../tools/pc/README.md) and
> [../../samples/README.md](../../samples/README.md); the device serial is redacted to
> `<device-serial>`. Do not run the commands verbatim; follow the root README.

# Round 11 — Cold-boot regression: the liveview subscription slot and the GUI/USB coupling

> 2026-11-23. The camera restarted on its own while unattended (uptime 1 min). After the
> restart the entire tape-replay flow **completely failed**: not one video frame came out.
> This log records the evidence chain, the ruled-out hypotheses, and a new finding that
> broke adb (the GUI process owns the USB mode).

## 1. One-line conclusion

1. After cold boot the camera's **own** preview is fine (cam.log: waylandsink `frame cnt`
   moves 270 frames in 10 s ≈ **27 fps**); internal encoding / ISP / EIS are all running,
   and `capability_strategy` disables nothing (capture/record/liveview all 0);
2. But **we cannot get video**: `dji_camera3` reports `subscribe_module(0x0100) failed,
   ret:0xe0` once per second; meanwhile the client only receives **audio** (audio_MB keeps
   growing) and 35B checks, `big=0 / vclients=0 / idr=0`;
3. In other words: **module 0x0100 (the liveview video source) cannot be subscribed to** —
   not fragment loss, not ack, not reassembly;
4. **The GUI process `dji_gui_on_disp0` also owns the USB peripheral mode.** After killing
   it, Windows still sees the `USB\VID_2717&PID_5055` composite device, but the **ADB
   interface is gone and `adb devices` is empty** — this is the direct cause of adb dropping
   this round; physical replug/power cycle is needed to recover (see §6).

## 2. The field after cold boot (evidence)

| Observation | Value | Meaning |
|---|---|---|
| `uptime` | 1 min (booted 03:33) | The camera restarted by itself, not by us |
| `dji_camera3` in `ps` | only **1** process (3422, since 03:33:18) | The earlier "multiple encoder instances" hypothesis does not hold after cold boot |
| waylandsink `frame cnt` | 2671 → 2941 / 10 s = **27 fps** | Camera internal preview is full-rate; hardware is fine |
| `capability_strategy` | capture/record/transcode/liveview/playback all 0 | Nothing disabled |
| `EIS_MOTION_FILTER` | `liveview_fs:4`, vsync advancing | The preview picture path is alive |
| `subscribe_module(0x0100)` | **1 failure per second, ret:0xe0**, starting the second our replay began | Video source subscription rejected |
| Camera error counts | `Recv_Stream_Check_Seq err1` 3353, `Send_Stream_Check_Ack err2` 42220 | Ack semantics still questioned, but §3 rules it out as the main cause |
| Our side stat | `big=0 frames=0 idr=0 vclients=0`, but `audio_MB` 45→79 MB | Audio subscribed, video subscription failed |

Key timing: the first `subscribe_module(0x0100) failed` appears at **03:35:28**, exactly
one second after our replay started — the "app opens preview / subscribes" packet sequence
in our replay triggers camera3 to subscribe, and it fails.

## 3. A/B tests done (none recovered it, but many hypotheses ruled out)

`tools/pc/r90_bootorder.py` (unpublished; newly written, two service-start orders, 40 s
each):

| Order | Frames | err1 increment | err2 increment | Conclusion |
|---|---|---|---|---|
| `camera_first` (kick camera3 first, wait 10 s, then start replay) | **0** | 27 | 10450 | Timing aligned; ack sequence barely errors, but still no video |
| `server_first` (old order: replay first, kick camera3 after 1 s) | **0** | 3599 | 18110 | The old order scrambles the sequence completely after cold boot |

`tools/pc/r88_ackmax.py` four ack semantics tiers (echo / dup / dup_hi / table, 30 s
each): **all 0 frames**.

⇒ **The ack clock/sequence semantics are no longer the current bottleneck.** Even if ack
were perfectly "real-device-like", without a video-source subscription there is nothing.
Conversely, this explains the earlier `ACK_MODE` jitter between 16 fps and 11 fps: those
differences occurred inside sessions where subscription had already succeeded.

## 4. New hypotheses (to test next)

* **0x0100 is a single-subscriber resource**: the local camera GUI is previewing (27 fps)
  and may already hold the liveview subscription slot; when camera3 subscribes again as
  "the second client (us)" it returns `0xe0`. The few earlier times a stream came out may
  have been when the GUI was on a non-preview page / asleep.
* It may also be that the provider on the `dji_media` / `dji_amt` side was not registered
  (`ret 0xe0` may also mean "module offline"). To distinguish: switch the GUI to the
  playback/menu page (release live view) and run the replay again; or check the
  `agent_pub_sub` registry (`/blackbox/system/agent_pub_sub.json` is currently **0 bytes**,
  nothing to inspect).

## 5. How adb was broken this round (important, do not repeat)

`r90_bootorder.py` added a `gui_first` tier: `pkill -f dji_gui_on_disp0`, to experiment on
the "free up the subscription slot" route. Result: the script threw on a log-count parse
bug, and then **adb dropped offline immediately** (`adb devices` empty).

Recheck:

* Windows side: the `USB\VID_2717&PID_5055` **composite device is still present, status OK**;
* but **there is no ADB interface sub-device**; re-enumerating with `pnputil
  /restart-device` still shows none;
* no new RNDIS/network adapter appeared either (`Get-NetAdapter` shows nothing new), so
  there is no bypass.

Conclusion: **the GUI process (dji_gui_on_disp0) is the manager of the USB peripheral
mode** (the boot log line `[GUI_I]: [USBMODE]: Try to set USB WorkMode[4 WIFI]` is emitted
by it). Killing it equals switching USB to a mode without adb. **This is a hard red line**:
do not kill this PID again.

## 6. Recovery (needs physical action)

By priority:

1. **Unplug and replug the USB cable** (wait 3 s before reinserting) — let the camera
   re-enumerate its gadget; adb usually comes back;
2. If that fails, **short-press the power key to wake / long-press to restart the camera**;
3. If still failing, **power-cycle** (battery 21%, charging; can continue after restart).

After recovery, verify first: `adb devices` shows the serial → `adb -s <device-serial>
shell uptime` returns output.

## 7. Reproduction commands

```
python tools/pc/r90_bootorder.py                       # two service-start orders (script unpublished)
ORDERS=gui_first python tools/pc/r90_bootorder.py      # ⚠ dangerous: switches USB mode and breaks adb, do not rerun
SECS=30 ARMS="echo=LIVE_ACK_MODE=echo;dup=LIVE_ACK_MODE=dup" python tools/pc/r88_ackmax.py
```

Read-only, safe observation commands:

```
adb -s <device-serial> shell 'grep -c "subscribe_module(0x0100) failed" /blackbox/camera/log/cam.log'
adb -s <device-serial> shell 'grep "frame cnt" /blackbox/camera/log/cam.log | tail -n1'
adb -s <device-serial> shell 'grep -iE "subscribe_module|capability_strategy|liveview_fs" /blackbox/camera/log/cam.log | tail -n 5'
```
