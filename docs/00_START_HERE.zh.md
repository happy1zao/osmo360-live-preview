# 快速上手 · Start Here

> 仅供学习与研究，仅限在本人合法持有且获准改造的设备上使用。手法只修改运行中进程内存，
> 不写磁盘、不刷固件，重启服务即恢复。与 DJI 无隶属关系。

## 1. 仓库能做什么

在 PC 上取得 Osmo 360（一代）的 2:1 双鱼眼实时预览流，不依赖手机 App、不截屏、不拍屏。

- **中继预览**：相机端中继脚本把组好的 H.264 帧经 TCP 推到 PC，`preview.py` 解码显示并提供控制控制台。
- **LRF 边写边读预览**：相机录制过程中，跟随正在写入的 `.LRF` 文件，按 GOF 锚点捞 NAL 流式解码
  （`tools/pc/r156_lrf_live2.py`，当前主推路径）。
- **离线分析**：帧率真伪审计、帧差-间隔曲线、proxy 码流 GOF 截断等 PC 端工具。

## 2. 环境要求

| 项 | 要求 |
|---|---|
| PC | Python 3.10+（Windows 用 `python` / `py -3`；Linux/macOS 用 `python3`） |
| Python 依赖 | `numpy`、`opencv-python`（见 `requirements.txt`） |
| 外部二进制 | `adb`（连接相机）、`ffmpeg`（解码；可选 `ffplay`），均需在 PATH 或通过环境变量指定 |
| 相机 | 已 root 的 Osmo 360；相机端脚本用机内 Python 3 运行，无需在相机上装包 |

环境变量（均可选）：

| 变量 | 作用 |
|---|---|
| `ADB` | adb 可执行文件路径；缺省走 PATH |
| `ADB_SERIAL` | 设备序列号；单设备时留空即可（不带 `-s`） |
| `FFMPEG` / `FFPLAY` | ffmpeg / ffplay 路径；缺省走 PATH |

## 3. 从零跑起来

```bat
:: 1) 克隆
git clone https://github.com/happy1zao/osmo360-live-preview.git
cd osmo360-live-preview

:: 2) 装依赖
py -3 -m pip install -r requirements.txt

:: 3) 包导入自检（不连设备也应通过）
py -3 -c "import osmo360; print(osmo360.__version__)"
```

### 方式 A：中继预览（`start_preview.bat`）

```bat
:: Windows：推相机端中继脚本 → 起中继（5010 视频 / 5011 控制）→ 打开 PC 预览窗口
start_preview.bat
```

Linux / macOS 手动等价步骤：

```sh
adb push probe/lv_relay.py probe/run_relay.sh probe/kill_relay.sh \
       probe/gen38.py probe/gate_off.py /data/local/tmp/
adb shell "cd /data/local/tmp && nohup sh run_relay.sh >/dev/null 2>&1 &"
python3 preview.py
```

预览窗口控制台输入 `help` 查看命令（`stat` / `kick` / `rehand` / `ackn` / `reqms` / `kf` / `save` / `quit`）。

### 方式 B：LRF 边写边读 2:1 预览（`pano_live.bat`）

前提：相机正在录制。

```bat
pano_live.bat
:: 等价于
py -3 tools/pc/r156_lrf_live2.py --file auto --secs 3600 --scale 1280
```

无窗口抓帧自检：`--no-window --save-every 30`，帧落到 `samples/rec/live4/`（本地生成，不入库）。

## 4. 目录结构

```
osmo360-live-preview/
├─ README.md / README.en.md     # 仓库总览（中英）
├─ preview.py                  # PC 预览窗口 + 控制控制台
├─ start_preview.bat           # 方式 A 一键启动（Windows）
├─ pano_live.bat               # 方式 B 一键启动（Windows）
├─ osmo360/                    # Python 包：transport / video / audio / control / session / pipeline / live
├─ probe/                      # 设备端 C 探针与脚本、build.sh、回滚脚本、说明
├─ tools/pc/                   # PC 端分析与预览脚本（清单见 tools/pc/README.md）
├─ samples/README.md            # 样本占位说明（样本文件不入库）
└─ docs/                       # 文档：主动指南 + 归档轮次记录
```

## 5. 进一步阅读

- [状态与里程碑](01_STATUS.zh.md)：哪些链路已打通、哪些仍在进行。
- [预览详细文档](preview-guide.zh.md)：设备结构、协议帧格式、两个关键突破的原理。
- [轮次归档](archive/)：早期逆向过程记录，中英成对（`.zh.md` / `.en.md`）；其中部分早期脚本未随仓库发布。

## 6. 已知边界

- 仓库**不含**真机抓包 / 码流 / 截图等样本文件（见 `samples/README.md`），离线回放类命令需要先用自己的设备采集。
- 部分早期实验脚本未随仓库发布，清单与原因见 `tools/pc/README.md`。
- 相机端探针需要先按 `probe/README.md` 用 `probe/build.sh` 交叉编译，再 bind mount 部署到设备。
