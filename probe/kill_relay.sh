#!/system/bin/sh
# kill_relay.sh —— 杀掉所有 lv_relay/lv_pipe6 进程（脚本自身 cmdline 不含匹配串，不会误杀自身）
for p in /proc/[0-9]*; do
  if tr '\0' ' ' < "$p/cmdline" 2>/dev/null | grep -qE 'lv_[r]elay|lv_[p]ipe6'; then
    pid=${p#/proc/}
    kill -9 "$pid" 2>/dev/null
  fi
done
sleep 2
echo killed
