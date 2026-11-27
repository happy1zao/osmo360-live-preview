> **历史归档（中文）** / Archived round log (Chinese).
> 本文是早期逆向轮次记录。文中引用的部分早期脚本与样本未随仓库发布，
> 清单见 [../../tools/pc/README.md](../../tools/pc/README.md) 与 [../../samples/README.md](../../samples/README.md)；
> 设备序列号已脱敏为 `<device-serial>`。命令请勿直接照跑，以根目录 README 为准。

# 第十一轮补 · 0x0100 是 App host：冷启动后无法重建“App 已连接”

> 2026-11-23，接 doc97。本文三条结论把上一份文档里的判断更正了一半，
> 并记录相机自带测试工具 `dji_cst` / `duss_shell` 的真实可用性。

## 1. 更正：`subscribe_module(0x0100) failed, ret:0xe0` 不是“视频模块坏了”

`cam.log` 里 SysMode 的日志给了铁证：

```
SysModeManagerServiceImpl::doReleaseWakelock, 0x0100, 2 APP
sysmode_resource_lock_release: type->2, host_id->0x0100, name->APP
```

**host_id 0x0100 = App（手机端）这个 host。** 所以 `dji_camera3` 每秒一次的
`subscribe_module(0x0100) failed`，真实含义是：

> 相机认为**现在没有任何客户端是 App**，于是每秒重试订阅 App 的模块。

它不是视频源缺失，也不是我们的包格式错。**这是“没人被认成 App”的直接症状。**

## 2. 媒体服务：这台机的 liveview 通道本来就是关的

`/system/etc/dji_media_service.json`：

```json
"modes": {
  "liveview":        {"supportable": false},
  "wifi_broadcast":  {"supportable": true},
  "uvc_only":        {"supportable": true},
  "default_workmode": "uvc_only"
}
```

配合 cam.log 里每秒一条 `[UvcMgr] Read gadget state as 0x8002`，
说明媒体服务当前正跑在 **uvc_only**（USB 摄像头）模式。
`capability_strategy` 里什么功能都没禁，但媒体服务的 liveview 模式本身没开。

## 3. 相机自带测试工具确实能用（往后都靠它）

| 工具 | 用法 | 实测结果 |
|---|---|---|
| `dji_cst` | `LD_LIBRARY_PATH=/usr/lib LD_PRELOAD=libdcam_hook.so dji_cst -m <序列名> -L 60` | ✅ 能跑：`CST_RLT:duss_rec_mode_switch: PASSED`，CamX 真的重配了流 |
| `dji_cst` DUSS 发送 | 内部 `duss_event_send_sync` | ❌ `failed:-1003`（目标 host 不在线） |
| `duss_shell` | `duss_shell [config_id] dump/ping/msg_send ...` | ❌ 初始化就失败：`duss_mb_create_route_table error: -1001`（`shell` 服务没在 `/system/etc/dji.json` 里登记） |

`/system/etc/cst_cases.json` 是宝库：**203 条序列**，其中 24 条是 `duss_liveview_*`，
覆盖 6000x3000 / 7680x3840 全景、3840x1920 半全景，帧率从 p24 到 p240（例如 `..._pano_video_p25`）。
`duss_rec_mode_switch` 跑通说明**相机侧没有坏**，只是“App 通道”没人。

## 4. 关键负面结论：冷启动后回放无法把相机认成 App

`no_app_host` 计数（`subscribe_module(0x0100) failed`）在三种起服顺序下都稳定 1/s：

| 顺序 | frames | no_app_host / 40s |
|---|---|---|
| server_first（先回放，1s 后 kick camera3） | 0 | 65 |
| camera_first（先 kick camera3，等 10s，再回放） | 0 | 65 |

⇒ 磁带里那套“app 打开预览”的上行包，**在相机内存里没有历史会话时是无效的**：
它复现不了“App 已连接”这个状态。能出流的那几次，是相机当时**确实还挂着一个 App 会话**
（很可能来自更早的真实 Mimo 连接），我们的回放只是搭在它上面。
一旦相机重启，这个会话被清掉，磁带就再也“点不着”了。

## 5. 两条后续路

1. **重新挂一次真实 App 会话**（一次性）：手机 Mimo 连一次相机，之后再继续用我们的回放搭在上面。
   这是历史上已验证有效的状态；代价是要开相机 Wi-Fi（用户决策项）。
2. **自己实现 App host 注册**：需要真实的 DUML `connect/handshake` 报文（含会话 id / 校验值）。
   `samples/mimo_cold_handshake.pcap` 只有上行，缺相机→App 的响应，所以离线拼不出来。
   可行做法：抓一份**双向**握手（手机连相机的全过程），再把握手做成可重放的独立模块。

## 6. 复现命令

```
# 相机侧跑一条录像模式切换序列（安全，不动 USB/adb）
LD_LIBRARY_PATH=/usr/lib LD_PRELOAD=libdcam_hook.so dji_cst -m duss_rec_mode_switch -L 20

# 观察“有没有 App”（1/s 增长 = 没有）
adb -s <device-serial> shell 'grep -c "subscribe_module(0x0100) failed" /blackbox/camera/log/cam.log'

# PC 侧两种起服顺序对照（含 no_app_host 计数）
SECS=40 ORDERS="camera_first;server_first" python tools/pc/r90_bootorder.py

# 用 cst 拉起 liveview 并同时看我们收到多少帧
SECS=45 HOLD=60 python tools/pc/r91_cst_live.py
```
