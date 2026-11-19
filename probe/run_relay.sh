#!/system/bin/sh
# run_relay.sh —— 闸门补丁 + 预览中继（手机握手订阅，GUI 编码器已在出流）
cd /data/local/tmp
python3 /data/local/tmp/gate_off.py > /dev/shm/gate.log 2>&1
nohup python3 -u /data/local/tmp/lv_relay.py /dev/shm/mimo.pcap 4 > /dev/shm/relay.log 2>&1 &
echo $! > /dev/shm/relay.pid
sleep 4
cat /dev/shm/relay.log
