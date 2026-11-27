#!/bin/sh
# build.sh —— 交叉编译设备端 hook 探针（aarch64 Linux / 相机固件环境）
#
# 用法:
#   sh probe/build.sh [source.c] [output.so]
# 默认: probe/hook_probe97.c → probe/libdcam_hook.so
#
# 依赖: zig（内置 aarch64-linux-gnu 交叉 target）；本机不需要 arm gcc。
set -e

HERE=$(cd "$(dirname "$0")" && pwd)
SRC="${1:-$HERE/hook_probe97.c}"
OUT="${2:-$HERE/libdcam_hook.so}"

zig cc -target aarch64-linux-gnu -shared -nostdlib -O0 \
  -fno-sanitize=all -fno-stack-protector -o "$OUT" "$SRC"

echo "built: $OUT"

# ── 部署（bind mount，不 remount 根分区 rw）─────────────────────────────
# 1) 推到设备临时目录:
#     adb push probe/libdcam_hook.so /data/local/tmp/libdcam_hook.so
# 2) 用 bind mount 覆盖相机进程已预载的 /usr/lib/libdcam_hook.so:
#     adb shell 'mount --bind /data/local/tmp/libdcam_hook.so /usr/lib/libdcam_hook.so'
# 3) 局部重启相机进程（不重启整机、不 reboot）:
#     adb shell 'nohup systemctl restart dji_camera3 >/dev/null 2>&1 &'
#
# 回滚: adb shell sh /data/local/tmp/restore_stock.sh
# 注意: bind mount 仅当前会话有效，重启设备后自动复原；不要对 /usr 做 remount rw。
