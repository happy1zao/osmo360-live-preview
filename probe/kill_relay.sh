#!/system/bin/sh
# kill_relay.sh —— 只杀“真正的 python3 中继进程”。
# 为什么不再直接 grep cmdline：adb shell 传进来的命令形如
#   sh -c "sh kill_relay.sh; ... python3 lv_relay.py ..."
# 整条命令行都含 lv_relay.py 字样，粗暴匹配会把执行脚本的 shell 自己杀掉（退出码 137）。
# 这里要求 cmdline 的第一个词就是 python3/python，才认为是中继本体。
for p in /proc/[0-9]*; do
  cmd=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
  set -- $cmd
  case "$1" in
    python3|python|*/python3|*/python)
      case "$cmd" in
        *lv_relay.py*|*lv_pipe6.py*|*lv_full.py*|*lv_fix.py*)
          pid=${p#/proc/}
          kill -9 "$pid" 2>/dev/null
          ;;
      esac
      ;;
  esac
done
sleep 2
grep ':1388 ' /proc/net/tcp >/dev/null 2>&1 || echo PORT5000_FREE
grep ':1389 ' /proc/net/tcp >/dev/null 2>&1 || echo PORT5001_FREE
