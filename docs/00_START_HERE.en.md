# Start Here

> For study and research only, on a device you legally own and are allowed to modify.
> The techniques described only change the memory of a running process: nothing is written
> to disk, nothing is flashed, and a service restart reverts everything. Not affiliated
> with DJI.

## 1. What this repo does

Obtain the 2:1 dual-fisheye live preview stream of an Osmo 360 (first generation) on a PC —
no phone app, no screen capture, no filming of a screen.

- **Relay preview**: a camera-side relay script assembles H.264 frames and pushes them to the
  PC over TCP; `preview.py` decodes and displays them and exposes a control console.
- **Live-read-from-LRF preview**: while the camera records, follow the growing `.LRF` file and
  pull NAL units from GOF anchors for streaming decode (`tools/pc/r156_lrf_live2.py`, the
  current primary path).
- **Offline analysis**: frame-rate authenticity audit, frame-difference vs. lag curve, proxy
  stream GOF trimming, and other PC-side tools.

## 2. Requirements

| Item | Requirement |
|---|---|
| PC | Python 3.10+ (`python` / `py -3` on Windows; `python3` on Linux/macOS) |
| Python deps | `numpy`, `opencv-python` (see `requirements.txt`) |
| External binaries | `adb` (camera connection), `ffmpeg` (decode; optional `ffplay`), on PATH or set via env vars |
| Camera | rooted Osmo 360; camera-side scripts run on the device's built-in Python 3, no packages installed there |

Optional environment variables:

| Variable | Purpose |
|---|---|
| `ADB` | path to the adb executable; defaults to PATH |
| `ADB_SERIAL` | device serial; leave empty for a single device (no `-s` flag) |
| `FFMPEG` / `FFPLAY` | ffmpeg / ffplay paths; default to PATH |

## 3. Run from scratch

```bat
:: 1) Clone
git clone https://github.com/happy1zao/osmo360-live-preview.git
cd osmo360-live-preview

:: 2) Install dependencies
py -3 -m pip install -r requirements.txt

:: 3) Package import self-check (passes without a connected device)
py -3 -c "import osmo360; print(osmo360.__version__)"
```

### Option A: Relay preview (`start_preview.bat`)

```bat
:: Windows: push camera-side relay scripts → start relay (5010 video / 5011 control) → open PC window
start_preview.bat
```

Manual equivalent on Linux / macOS:

```sh
adb push probe/lv_relay.py probe/run_relay.sh probe/kill_relay.sh \
       probe/gen38.py probe/gate_off.py /data/local/tmp/
adb shell "cd /data/local/tmp && nohup sh run_relay.sh >/dev/null 2>&1 &"
python3 preview.py
```

Type `help` in the preview console for commands (`stat` / `kick` / `rehand` / `ackn` /
`reqms` / `kf` / `save` / `quit`).

### Option B: Live-read-from-LRF 2:1 preview (`pano_live.bat`)

Prerequisite: the camera is recording.

```bat
pano_live.bat
:: equivalent to
py -3 tools/pc/r156_lrf_live2.py --file auto --secs 3600 --scale 1280
```

Headless frame capture: `--no-window --save-every 30`; frames land in `samples/rec/live4/`
(generated locally, not committed).

## 4. Layout

```
osmo360-live-preview/
├─ README.md / README.en.md     # repo overview (ZH / EN)
├─ preview.py                  # PC preview window + control console
├─ start_preview.bat           # Option A one-click launcher (Windows)
├─ pano_live.bat               # Option B one-click launcher (Windows)
├─ osmo360/                    # Python package: transport / video / audio / control / session / pipeline / live
├─ probe/                      # device-side C probe & scripts, build.sh, rollback script, README
├─ tools/pc/                   # PC-side analysis & preview scripts (see tools/pc/README.md)
├─ samples/README.md           # sample placeholder note (sample files are not committed)
└─ docs/                       # docs: active guides + archived round logs
```

## 5. Further reading

- [Status & milestones](01_STATUS.en.md): which links work end to end, which are in progress.
- [Detailed preview guide](preview-guide.en.md): device layout, protocol frame format, the
  two key breakthroughs and why they work.
- [Round archives](archive/): early reverse-engineering logs, paired ZH/EN (`.zh.md` / `.en.md`); some early
  scripts referenced there are not shipped with this repo.

## 6. Known boundaries

- The repo ships **no** real-device captures / streams / screenshots (see `samples/README.md`);
  offline replay commands require a capture taken on your own device first.
- Some early experimental scripts are not shipped; the list and reasons are in `tools/pc/README.md`.
- The camera-side probe must be cross-compiled with `probe/build.sh` per `probe/README.md`,
  then deployed to the device via bind mount.
