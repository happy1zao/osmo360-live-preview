# osmo360-live-preview

Toolkit for the Osmo 360 (first generation) 2:1 dual-fisheye live preview and offline analysis:
get the camera's preview stream on a PC — no phone app, no screen capture, no filming of a screen.

> **Disclaimer**: for study and research only, on a device you legally own and are allowed to
> modify. The techniques described only change the memory of a running process: nothing is
> written to disk, nothing is flashed, and a service restart reverts everything. Not affiliated
> with DJI.

## Quick start

```bat
git clone https://github.com/happy1zao/osmo360-live-preview.git
cd osmo360-live-preview
py -3 -m pip install -r requirements.txt
py -3 -c "import osmo360; print(osmo360.__version__)"
```

Prerequisites: `adb` and `ffmpeg` on PATH (or set `ADB` / `FFMPEG` env vars); camera rooted.
Set the device serial via `ADB_SERIAL`; leave it empty for a single device.

### Relay preview (Windows one-click)

```bat
start_preview.bat
```

Manual equivalent steps are in [`docs/00_START_HERE.en.md`](docs/00_START_HERE.en.md).

### Live-read-from-LRF 2:1 preview (while recording)

```bat
pano_live.bat
:: equivalent to
py -3 tools\pc\r156_lrf_live2.py --file auto --secs 3600 --scale 1280
```

### Build the device-side probe (optional, for the hook approach)

```sh
sh probe/build.sh
```

The resulting `probe/libdcam_hook.so` is deployed to `/usr/lib/libdcam_hook.so` via bind mount;
steps and rollback are in [`probe/README.md`](probe/README.md).

## Layout

| Path | Contents |
|---|---|
| `osmo360/` | Python package: `transport` (adb/forwarding), `video` (reassembly/decode), `audio` (four-mic capture), `control` (camera control), `session` (self-built session), `pipeline` (one-shot chain), `live` (live stream) |
| `probe/` | device-side C probe source, camera-side Python/Shell scripts, cross-compile script, rollback script |
| `tools/pc/` | PC-side analysis & preview scripts (see [`tools/pc/README.md`](tools/pc/README.md)) |
| `preview.py` | PC preview window + control console |
| `samples/` | sample placeholder note; captures/streams/screenshots and other large files are not committed |
| `docs/` | active guides, detailed theory, round archives |

## Documentation

- [Start Here (ZH)](docs/00_START_HERE.zh.md) · [Start Here (English)](docs/00_START_HERE.en.md)
- [Status (ZH)](docs/01_STATUS.zh.md) · [Status (English)](docs/01_STATUS.en.md)
- [Detailed Guide (ZH)](docs/preview-guide.zh.md) · [Detailed Guide (English)](docs/preview-guide.en.md)
- [Round archives](docs/archive/): early reverse-engineering logs, paired ZH/EN (`.zh.md` / `.en.md`); some early scripts referenced there are not shipped with this repo

## Dependencies

Python third-party packages are listed in [`requirements.txt`](requirements.txt)
(`numpy`, `opencv-python`). `adb` and `ffmpeg` are external binaries, not pip packages.

## License

[MIT](LICENSE) © 2026 happy1zao
