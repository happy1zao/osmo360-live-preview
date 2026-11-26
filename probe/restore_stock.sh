#!/bin/sh
# restore_stock.sh —— 一键回滚到出厂态（钩子库 + 相机能力表 + UI 档位表），然后重启相机
#
# 前提：/data/local/tmp 下这些备份必须还在（本轮部署时都留过）
#   libdcam_hook.so        原始钩子库（38,216 B，md5 4e1c7d4592c4c0f61ad56b1df6b685d1）
#   bak_def_cap.json       原始 /system/etc/dji_default_camera_ov68a40_us.json（630,760 B）
#   bak_cur_cap.json       原始 /data/camera/dji_current_camera_ov68a40_us.json（264,314 B）
#   bak_capability.json    原始 /system/etc/capability.json（107,786 B，md5 db81d2d6fa22c250219d34c77869106a）
#
# 用法: sh /data/local/tmp/restore_stock.sh
set -e

echo "== backup check =="
for f in libdcam_hook.so bak_def_cap.json bak_cur_cap.json bak_capability.json; do
  if [ ! -f "/data/local/tmp/$f" ]; then echo "缺少 /data/local/tmp/$f"; exit 1; fi
done
ls -l /data/local/tmp/libdcam_hook.so /data/local/tmp/bak_def_cap.json \
      /data/local/tmp/bak_cur_cap.json /data/local/tmp/bak_capability.json

echo "== restore =="
mount -o remount,rw /
cp -f /data/local/tmp/libdcam_hook.so      /usr/lib/libdcam_hook.so
cp -f /data/local/tmp/bak_def_cap.json     /system/etc/dji_default_camera_ov68a40_us.json
cp -f /data/local/tmp/bak_cur_cap.json     /data/camera/dji_current_camera_ov68a40_us.json
cp -f /data/local/tmp/bak_capability.json  /system/etc/capability.json
mount -o remount,ro /

echo "== verify =="
md5sum /usr/lib/libdcam_hook.so /system/etc/capability.json \
       /system/etc/dji_default_camera_ov68a40_us.json /data/camera/dji_current_camera_ov68a40_us.json

echo "== restart camera3 =="
for p in $(pgrep -f "dji_cs""t"); do kill $p; done
systemctl reset-failed dji_camera3.service
systemctl restart dji_camera3.service
sleep 12
systemctl is-active dji_camera3.service
echo "done. 现在相机是原厂状态：录 4K120 全景仍在（相机侧本来支持），但 UI 里没有 4K120 档。"
