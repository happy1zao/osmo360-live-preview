# probe/ —— 设备端探针与相机侧脚本

本目录分三类：C 探针源码、相机端 Python/Shell 脚本、构建与回滚入口。
所有设备端路径（/data/local/tmp/...、/dev/shm/...、/usr/lib/...）均为相机固件内路径，与 PC 主机无关。

## 目录内容

| 文件 | 作用 |
|---|---|
| `hook_probe97.c` | 最终版 LD_PRELOAD 探针：零写盘 + proxy tee + h264parse → TCP 输出 2:1 代理流 |
| `build.sh` | 交叉编译脚本（zig，aarch64-linux-gnu），见下方“编译与部署” |
| `restore_stock.sh` | 一键回滚：卸载探针、恢复 stock 状态 |
| `lrf_tail.py` | 设备端 O(1) seek 跟随正在写入的 `.LRF` 文件，新字节吐到 stdout |
| `h265_push.py` | 相机端把 `/dev/shm/lv` 滚动 H.265 分片按 mtime 排序推到 stdout |
| `lv_relay.py` | 相机端中继：组双鱼眼 H264 帧 → TCP 5010（视频）/ 5011（控制） |
| `live_server.py` | 相机端自建会话服务（握手回放 / 周期请求 / ACK 调度） |
| `lv_fix.py` | 握手补丁与关键帧缓存补发 |
| `gate_off.py` | 12 秒流控闸门内存补丁（每个新 boot 重打一次，只改内存） |
| `gen38.py` | 对真机包 DUML 逐字节匹配校验 |
| `aud_extract.py` / `aud_rms.py` | 音频声道抽取 / 逐声道 RMS 与峰值 |
| `ch_sum.py` / `cut_from.py` | pcap 逐通道字节/SPS/IDR 统计；按 SPS 偏移截取码流 |
| `force_idr.py` | 强制请求关键帧 |
| `hi_ch.py` | 提取 ≥0x28 高通道 |
| `kill_relay.sh` / `run_relay.sh` | 停掉旧中继 / 后台起中继 |
| `sps_retool.py` | 只改 SPS 两个 ue 字段，其余比特原样保留 |

## 编译与部署

编译（在 PC 上交叉编译，不需要 arm gcc）：

```sh
sh probe/build.sh
# 等价于：
# zig cc -target aarch64-linux-gnu -shared -nostdlib -O0 \
#   -fno-sanitize=all -fno-stack-protector -o probe/libdcam_hook.so probe/hook_probe97.c
```

部署到相机（**bind mount 覆盖，不 remount 根分区 rw**）：

```sh
adb push probe/libdcam_hook.so /data/local/tmp/libdcam_hook.so
adb shell 'mount --bind /data/local/tmp/libdcam_hook.so /usr/lib/libdcam_hook.so'
adb shell 'nohup systemctl restart dji_camera3 >/dev/null 2>&1 &'
```

回滚：`adb shell sh /data/local/tmp/restore_stock.sh`。
bind mount 仅当前会话有效，重启设备后自动复原。

## 未随仓库发布的设备端脚本

`osmo360/control.py` 引用的设备端 `camtool.py`，以及 `osmo360/session.py`
引用的 `exp_idr.sh`、`replay_up.py`、`expI_final.sh`，属于设备侧实验脚本，
**未包含在本仓库内**；相关封装仅在对应方法被调用时才会用到，不影响包的导入。

历史轮次记录中提到的 `hook_probe93.c` / `hook_probe95.c` / `hook_probe96.c`
等失败实验变体也未随仓库发布；仓库内仅保留最终版 `hook_probe97.c`。
编译产物 `probe/libdcam_hook.so` 由 `build.sh` 本地生成，不入库。
