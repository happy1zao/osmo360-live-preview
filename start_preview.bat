@echo off
REM ============================================================
REM  Osmo360 实时预览 一键启动
REM    1) 推相机端脚本  2) 起相机端中继(5010视频/5011控制)  3) 开 PC 预览窗口
REM  控制台输入 help 查看命令（stat/kick/rehand/ackn/reqms/kf/save/quit）
REM ============================================================
setlocal
cd /d "%~dp0"

REM ---------- 找 adb ----------
REM 优先环境变量 ADB，其次 PATH 里的 adb
if not "%ADB%"=="" if exist "%ADB%" goto :adb_ok
adb version >nul 2>&1
if not errorlevel 1 (
  set "ADB=adb"
  goto :adb_ok
)
echo [错误] 找不到 adb.exe：请设置环境变量 ADB，或把 adb 加入 PATH。
pause
exit /b 1
:adb_ok

REM ---------- 找 python ----------
set "PY=python"
python --version >nul 2>&1
if not errorlevel 1 goto :py_ok
set "PY=py -3"
py -3 --version >nul 2>&1
if not errorlevel 1 goto :py_ok
echo [错误] 找不到 python：请安装 Python 3 并加入 PATH（Windows 上命令是 python，不是 python3）。
echo        也可手动运行：python preview.py --adb "%ADB%"
pause
exit /b 1
:py_ok

echo [0/3] 检查设备 ...
"%ADB%" devices

REM 握手样本（samples/mimo.pcap）属于真机私有抓包，不随仓库提供；
REM 没有样本不影响中继/预览启动，仅离线回放相关功能不可用。
if not exist samples\mimo.pcap (
  echo [提示] 缺少 samples\mimo.pcap 握手样本（私有抓包，未入库）；继续启动中继。
)

echo [1/3] 推送相机端脚本 ...
"%ADB%" push probe\lv_relay.py probe\run_relay.sh probe\kill_relay.sh probe\gen38.py probe\gate_off.py /data/local/tmp/ >nul

echo [2/3] 启动相机端中继（视频5010 / 控制5011）...
REM 先单独停旧中继（此行不含 lv_relay 字样，不会被 kill_relay.sh 误伤）
"%ADB%" shell "sh /data/local/tmp/kill_relay.sh > /dev/null 2>&1"
REM 再单独后台起中继（Android 没有 setsid，用 nohup + &）
"%ADB%" shell "cd /data/local/tmp && nohup sh run_relay.sh > /dev/null 2>&1 &"
timeout /t 6 /nobreak >nul
"%ADB%" shell "cat /dev/shm/relay.log"

echo [3/3] 打开 PC 预览窗口（控制台输入 help）...
"%PY%" preview.py --adb "%ADB%"

endlocal
