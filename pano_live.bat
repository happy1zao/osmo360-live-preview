@echo off
chcp 65001 >nul
setlocal
set PY=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe
set HERE=%~dp0

rem 2:1 双鱼眼实时预览（LRF 边写边读，按 GOF 锚点捞 NAL）
rem 前提：相机正在录制（否则先跑 r148 里的起录命令）
"%PY%" "%HERE%tools\pc\r156_lrf_live2.py" --file auto --secs 3600 --scale 1280 %*

echo.
echo 结束。若看不到窗口，加 --no-window 并在 --save-every 30 下看 samples\rec\live4
pause
