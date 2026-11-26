# 第 22 轮：2:1 全景实时预览打通（零落盘）+ 4K120 全景实证 + UI 档位原地改

> 目标回顾（用户硬约束）
> 1. 双鱼眼 2048×1024 的 2:1 全景，**实时出现在电脑上**；
> 2. **不落盘、不读 SD 卡上的 LRF**；
> 3. 相机 UI 的全景档位里出现 **4K 120 帧**。

---

## 一、结论速览

| 项目 | 状态 | 关键证据 |
|---|---|---|
| 2:1（2048×1024）实时预览到 PC | ✅ 打通 | `ffprobe` 显示抓到的 PNG 是 `2048x1024`；`dcam_rec/proxy_*.bin` 连续增长 |
| 零落盘 | ✅ 实测 | 长录会话进行中，卡上 `..._0098_D.OSV/.LRF` **恒为 0 字节** |
| 4K120 全景真实存在 | ✅ 重新实证 | `0093`：双 hevc `1920×1920@119.88`、1238 帧 / 10.33 s、42.1 + 41.2 Mbps |
| UI 出现 4K120 | ⏳ 已改表、待你在 UI 上确认 | `capability.json` block4 的 `1X1_1920P P100` 原地换成 `P120`，`num` 仍 12，camera3 稳定、cst `PASSED` |

---

## 二、先解决的那个「玄学」：为什么从 02:04 起什么都录不了

现象：`cst_record failed[-1002]`、`storage[0] status update [13]`、`free space 0` / `remain 0`，
随后 `_camera_service_disconnected` + `cs_topmode_util.c:6574 Assertion Failed: 0`（camera3 起崩溃循环）。

真因不在钩子、不在能力表、不在电量：

```
storage-service: do_mount: end fsck /dev/sda12 exfat count 2!     ← 对「已挂载」的分区跑 fsck，失败
storage-service: update_storage_state: id = emulated, old state = 1 new state = 6   ← state 6 = ERROR
→ camera3 的 storage agent 把 emulated 报给 cst 时就是 status 13 / free 0
→ cst 认为存储不可用 → Record 直接 -1002 → topmode 收到「非预期动作结果」→ assert(0) → 崩
```

也就是：**开机时 `/mnt/media_rw/emulated` 已经被 init/systemd 挂上了，
storage-service 自己再挂一次失败 → 给它打上 ERROR(6)**，此后所有录制全被拒。

修复（必须是这个顺序，否则 umount 会 EBUSY，服务再 fsck 一次还是 6）：

```sh
for p in $(pgrep -f 'dji_cs''t'); do kill $p; done
systemctl stop dji_camera3.service
systemctl stop dji_media_server.service
sleep 4; sync
umount /mnt/media_rw/emulated          # 必须真的 UNMOUNTED
systemctl restart storage_service.service
systemctl start dji_media_server.service
systemctl reset-failed dji_camera3.service
systemctl restart dji_camera3.service
```

修好后立刻验证：`duss_record_3840x1920_video_pano_p120_mp4` → `PASSED`，
`storage[0] status 0 / free 74429043 / remain 4110`。
`pano_live97.py` 里已把这个体检 + 自愈做成了自动步骤。

---

## 三、4K120 全景的实证：它一直都在

新录出的 `CAM_20261127023958_0093_D.OSV`（110 MB / 10.33 s）：

```
stream 0: hevc Main10 1920x1920  120000/1001 fps  42165 kb/s  1238 帧
stream 1: hevc Main10 1920x1920  120000/1001 fps  41241 kb/s  1238 帧
stream 2: aac 48k stereo
stream 3-6: bin_data(djmd/dbgi) 每帧一份（IMU/时间戳）
stream 7: mjpeg 688x344(2:1) 缩略图
```

两路 1920×1920 严格同帧数 → 双镜各 120 fps，拼起来就是 4K 全景。
触发它的就是：`SetModeProfile VIDEO_PANO` + `SetVideoFmt RESOLUTION_1X1_1920P / FRAMERATE_P120`
—— **用原始相机能力表就能跑通**，不需要之前那版 `e903` 补丁。

所以你「UI 回放显示全景 4K + 120 帧」的印象是对的：录是真的，UI 没给这个档是真缺项。

---

## 四、2:1 实时流怎么来的（并且为什么不走 LRF）

### 4.1 正确的取流点：proxy 编码器出口

相机内部 gst 管线（PARSE #9）：

```
appsrc name=proxy_yuv_src ! djicamc2venc name=proxy_encoder
  ! capsfilter name=proxy_enc_config ! appsink name=proxy_sink
```

这条就是 2:1 代理流的唯一来源，输出 2048×1024 H.264（编码器给的是 **AVCC 长度前缀**，
不是 AnnexB，所以裸 `ffmpeg -f h264` 读 TCP 会报 `Could not find codec parameters`）。

`probe97.so` 把这条管线改成：

```
... ! tee name=pte
pte. ! queue max-size-buffers=6 leaky=2 ! h264parse config-interval=-1
     ! video/x-h264,stream-format=byte-stream,alignment=au
     ! tcpserversink host=127.0.0.1 port=8888 sync=false     ← 给 PC 的实时流
pte. ! queue ! multifilesink location=/dev/shm/dcam_rec/proxy_%05d.bin max-files=24
pte. ! queue ! appsink name=proxy_sink ...                    ← 原链路保留
```

PC 侧 `adb forward tcp:18888 tcp:8888` 后直接播 `tcp://127.0.0.1:18888`。

### 4.2 零落盘：文件层全部改成假写

同 `probe97.so` 里还拦了 `open/open64/openat/openat64`：凡是 `/DCIM/` 下的
`.OSV/.LRF/.MET/.trinf`，路径全改到 `/data/local/tmp/spool/`，
`write/pwrite` 只累加逻辑大小、`fallocate/fsync` 直接返回 0、
`ftruncate` 只改逻辑大小（10 分钟里连 1 个真实数据块都不落）。

实测：长录会话跑着，`DCIM/CAM_001/CAM_..._0098_D.OSV` 与 `.LRF` 都是 **0 字节**，
同时 `/dev/shm/dcam_rec/proxy_*.bin` 每 ~40 ms 一片、稳定 30 KB+。

### 4.3 踩过的三个坑（写下来免得再走）

| 方案 | 结果 | 结论 |
|---|---|---|
| `probe79` 空壳 | 长录稳定 | 基线正常 |
| `probe80` 纯假写 | 长录稳定 | 假写层本身安全 |
| `probe93` OSV 假写 + **LRF 真写 /dev/shm** | **~1.7 s 被掐** | 录制的 LRF 一旦真写就停 |
| `probe95` OSV/LRF **都放同一个 tmpfs**（st_dev 相同） | **~1.7 s 仍被掐** | 不是「跨设备」校验，就是不能碰 LRF 的真实写入 |
| `probe90` 假写 + LRF 真写 + `gst_app_sink_pull_sample` 钩 | ~1.7 s 被掐 | appsink 钩子也别再加，取流只走管线 tee |

于是最终方案：**LRF 一律假写，2:1 全部从 proxy 管线的 tee 拿**。

---

## 五、UI 里加 4K120：只能原地改，不能追加

事实：
- UI 的帧率档来自 `/system/etc/capability.json`，`camcap_video_format[4]`
  （`pano_mode_type:pano_camera.topmode:video_pano`）共 12 条，
  最后一条是 `PROTO_CAMERA_VIDEO_RESOLUTION_1X1_1920P + DUSS_MSG_V_FRAMERATE_P100`
  —— 这就是你看到的「4K 100」。
- 该文件是**定长数组**语义（`num` 必须等于 `video_formats` 长度）；
  上一轮「追加一条 → num+1」直接把 camera3 打进崩溃循环（每 10 s 一份 core）。
- 相机侧能力表原始版已经支持 `1X1_1920P + P120`（见第三节实测），
  所以**只需要改 UI 表这一条**。

做法（`tools/pc/r185_ui_swap_120.py`）：

```bash
python r185_ui_swap_120.py samples/capability_ui.json samples/capability_ui_4k120_swap.json
adb push samples/capability_ui_4k120_swap.json /data/local/tmp/cap_swap120.json
adb shell 'mount -o remount,rw /; cp -f /data/local/tmp/cap_swap120.json /system/etc/capability.json; mount -o remount,ro /; systemctl reset-failed dji_camera3.service; systemctl restart dji_camera3.service'
```

部署后实测：camera3 `active` 稳定，`cst_camdev init: PASSED`，
`duss_record_3840x1920_video_pano_p120_mp4: PASSED`。
剩下一步需要你在相机 UI 上确认「全景 → 4K → 120 帧」是否出现、选中后是否会被切回。

> 副作用：这个改法是「换掉」而不是「新增」，所以 UI 里 4K 的 100 帧会消失。
> 想同时保留 100/120 就必须动二进制里那条定长数组的容量，风险高，先不动。

---

## 六、怎么用（PC 端一条命令）

```bash
# 实时预览（体检 + 自愈 + 起会话 + ffplay 2048×1024）
python tools/pc/pano_live97.py

# 不起窗口，只落 6 张 PNG 取证
python tools/pc/pano_live97.py --dump 6

# 只停会话
python tools/pc/pano_live97.py --stop
```

本轮实抓的帧在 `samples/rec/live/live_01..06.png`，`ffprobe` 确认 `2048x1024`。
注意：抓帧时房间是关灯的（`0093` 自己的画面 mean≈2.9、p99≈6），
所以 PNG 看起来几乎全黑——这是**场景暗**，不是流坏了。

---

## 七、回滚

```bash
adb push probe/restore_stock.sh /data/local/tmp/
adb shell sh /data/local/tmp/restore_stock.sh
```

会恢复：原版 `libdcam_hook.so`（md5 `4e1c7d45…`）、原始相机两张能力表、
原始 `capability.json`（md5 `db81d2d6…`），然后重启 camera3。
回滚后：录 4K120 全景仍然可用（相机侧本来就支持），但 UI 里没有 4K120 档。

---

## 八、本轮新增文件

```
probe/hook_probe93.c       LRF 真写 /dev/shm 的失败样本（留档）
probe/hook_probe95.c       双 tmpfs 的失败样本（留档）
probe/hook_probe96.c       proxy tee + LRF 真写（仍会被掐，留档）
probe/hook_probe97.c       ✅ 最终版：零写盘 + proxy tee + h264parse → TCP
probe/restore_stock.sh     一键回滚
tools/pc/r184_proxy_trim.py  proxy AVCC 码流的 GOF 截断/解帧
tools/pc/r185_ui_swap_120.py UI 档位原地 P100→P120
tools/pc/pano_live97.py     ✅ PC 端实时预览启动器（含存储自愈）
samples/rec/c93/0093.osv    4K120 全景实证（双 1920×1920@119.88）
samples/rec/live/*.png      TCP 实时抓帧（2048×1024，场景暗）
```
