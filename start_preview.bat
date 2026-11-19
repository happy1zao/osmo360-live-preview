@echo off
REM Osmo360 实时预览一键启动（相机端中继 + PC 预览窗口+控制台）
setlocal
set ADB=E:\123pan\Downloads\DJIAc5P_UI_Debian-master\DJIAc5P_UI_Debian-master\adb\adb.exe
cd /d "%~dp0"

echo [1/3] 推送相机端脚本 ...
"%ADB%" push probe\lv_relay.py probe\run_relay.sh probe\kill_relay.sh probe\gen38.py probe\gate_off.py /data/local/tmp/ >nul
if not exist samples\mimo.pcap (
  echo 缺少 samples\mimo.pcap 握手样本，请先抓包。
  pause & exit /b 1
)
"%ADB%" push samples\mimo.pcap /dev/shm/mimo.pcap >nul

echo [2/3] 启动相机端中继（视频5010/控制5011）...
"%ADB%" shell "sh /data/local/tmp/kill_relay.sh > /dev/null; setsid sh /data/local/tmp/run_relay.sh < /dev/null"

echo [3/3] 打开 PC 预览窗口（控制台输入 help）...
python preview.py --adb "%ADB%"
endlocal
