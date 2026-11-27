> **历史归档（中文，未提供英文译本）**
> 本文是早期逆向轮次记录。文中引用的部分早期脚本与样本未随仓库发布，
> 清单见 [../../tools/pc/README.md](../../tools/pc/README.md) 与 [../../samples/README.md](../../samples/README.md)；
> 设备序列号已脱敏为 `<device-serial>`。命令请勿直接照跑，以根目录 README 为准。

# 第十一轮 · 冷启动回归：liveview 订阅位与 GUI/USB 耦合

> 2026-11-23。相机在无人值守期间自己重启过一次（uptime 1min）。重启后**回放磁带的整套流程完全失效**：
> 一帧视频都出不来。本文记录证据链、已排除的假设，以及一个把 adb 搞断的新发现（GUI 进程管着 USB 模式）。

## 1. 一句话结论

1. 冷启动后相机**自己**的预览是好的（cam.log 里 waylandsink `frame cnt` 10s 走 270 帧 ≈ **27fps**），
   内部编码/ISP/EIS 都在跑，`capability_strategy` 也没禁用任何功能（capture/record/liveview 全 0）；
2. 但我们**拿不到视频**：`dji_camera3` 每秒一次 `subscribe_module(0x0100) failed, ret:0xe0`，
   同时客户端只收到**音频**（audio_MB 一直在涨）和 35B check，`big=0 / vclients=0 / idr=0`；
3. 也就是说：**“模块 0x0100（liveview 视频源）订阅不上”**，不是丢片、不是 ack、不是拼帧；
4. **`dji_gui_on_disp0` 这个 GUI 进程同时管着 USB 外设模式**。把它 kill 掉之后，
   Windows 侧仍看得到 `USB\VID_2717&PID_5055` 复合设备，但 **ADB 接口消失、adb devices 空**——
   这就是本轮把 adb 搞断的直接原因，需要物理重新插拔/上电才能恢复（见 §6）。

## 2. 冷启动后的现场（证据）

| 观测 | 值 | 含义 |
|---|---|---|
| `uptime` | 1 min（03:33 开机） | 相机自己重启过，不是我们重启的 |
| `ps` 里 `dji_camera3` | 只有 **1 个** 进程（3422，03:33:18 起） | 之前“多路编码器实例”的假设在冷启动下不成立 |
| waylandsink `frame cnt` | 2671 → 2941 / 10s = **27fps** | 相机内部预览满帧，硬件没问题 |
| `capability_strategy` | capture/record/transcode/liveview/playback 全 0 | 没有任何功能被禁用 |
| `EIS_MOTION_FILTER` | `liveview_fs:4`，vsync 持续推进 | 预览画面通路活着 |
| `subscribe_module(0x0100)` | **每秒 1 次 failed ret:0xe0**，从我们起回放那秒开始 | 视频源订阅被拒 |
| 相机报错计数 | `Recv_Stream_Check_Seq err1` 3353、`Send_Stream_Check_Ack err2` 42220 | ack 语义仍在被挑，但见 §3 已排除它是主因 |
| 我们侧 stat | `big=0 frames=0 idr=0 vclients=0`，但 `audio_MB` 45→79MB | 音频订阅成功、视频订阅失败 |

关键时序：`subscribe_module(0x0100) failed` 的第一条出现在 **03:35:28**，正是我们回放启动的下一秒，
说明是**我们回放里的“app 打开预览/订阅”那一串包**触发了 camera3 去订阅，而它订不上。

## 3. 已做的 A/B（都没救回来，但排除了大量假设）

`tools/pc/r90_bootorder.py`（未发布；新写，两种起服顺序，各测 40s）：

| 顺序 | frames | err1 增量 | err2 增量 | 结论 |
|---|---|---|---|---|
| `camera_first`（先 kick camera3，等 10s 再起回放） | **0** | 27 | 10450 | 时序对了，ack 序号几乎不报错了，但视频照样没有 |
| `server_first`（老顺序：先起回放，1s 后 kick camera3） | **0** | 3599 | 18110 | 老顺序在冷启动下会把序号彻底打乱 |

`tools/pc/r88_ackmax.py` 四档 ack 语义（echo / dup / dup_hi / table，各 30s）：**全部 0 帧**。

⇒ **ack 的时钟/序号语义已经不是当前瓶颈**。即使 ack 完全“像真机”，订阅不上视频源就什么都没有。
反过来说：这也解释了此前 `ACK_MODE` 各档时而 16fps 时而 11fps 的抖动 —— 那些差异发生在**已经订阅成功**的会话里。

## 4. 新假设（下一步要验的）

* **0x0100 是单订阅者资源**：相机本地 GUI 正在预览（27fps），可能已经占住 liveview 订阅位，
  camera3 为“第二个客户端（我们）”再订阅就返回 `0xe0`。历史能出流的那几次，可能是 GUI 处于非预览页/休眠态。
* 也可能是 `dji_media` / `dji_amt` 侧的 provider 没注册好（`ret 0xe0` 也可能是“模块不在线”）。
  区分办法：把 GUI 切到**播放/菜单页**（释放 liveview）再跑回放；或查 `agent_pub_sub` 的注册表（
  `/blackbox/system/agent_pub_sub.json` 目前是 **0 字节**，没有内容可查）。

## 5. 本轮搞断 adb 的经过（重要，勿重犯）

`r90_bootorder.py` 里加过一档 `gui_first`：`pkill -f dji_gui_on_disp0` 想在“空出订阅位”这条路上做实验。
结果：脚本因日志计数解析 bug 抛异常，随后 **adb 直接掉线**（`adb devices` 空）。

复查：

* Windows 侧 `USB\VID_2717&PID_5055` **复合设备仍在、状态 OK**；
* 但**没有 ADB 接口子设备**，`pnputil /restart-device` 重新枚举后依然没有；
* 也没有出现新的 RNDIS/网卡（`Get-NetAdapter` 无新增），所以没有旁路可走。

结论：**GUI 进程（dji_gui_on_disp0）是 USB 外设模式的管家**（开机日志里
`[GUI_I]: [USBMODE]: Try to set USB WorkMode[4 WIFI]` 就是它打的）。杀掉它等于把 USB 切成
不含 adb 的模式，**这是硬红线**，以后不许再 kill 这个 PID。

## 6. 恢复手段（需要物理动作）

按优先级：

1. **拔插 USB 线**（等 3s 再插回）——让相机重新枚举 gadget，通常 adb 会回来；
2. 不行就**短按电源键唤醒/长按重启相机**；
3. 再不行就**断电重开**（电池 21%，正在充电，重启后可继续跑）。

恢复后先验：`adb devices` 出现设备序列号 → `adb -s <device-serial> shell uptime` 有回显。

## 7. 复现命令

```
python tools/pc/r90_bootorder.py                       # 两种起服顺序对照（脚本未发布）
ORDERS=gui_first python tools/pc/r90_bootorder.py      # ⚠ 危险：会切 USB 模式搞断 adb，勿再跑
SECS=30 ARMS="echo=LIVE_ACK_MODE=echo;dup=LIVE_ACK_MODE=dup" python tools/pc/r88_ackmax.py
```

观测命令（只读，安全）：

```
adb -s <device-serial> shell 'grep -c "subscribe_module(0x0100) failed" /blackbox/camera/log/cam.log'
adb -s <device-serial> shell 'grep "frame cnt" /blackbox/camera/log/cam.log | tail -n1'
adb -s <device-serial> shell 'grep -iE "subscribe_module|capability_strategy|liveview_fs" /blackbox/camera/log/cam.log | tail -n 5'
```
