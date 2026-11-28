# Prosmo360

Osmo 360（一代）2:1 双鱼眼实时预览与离线分析工具集：在 PC 上取得相机预览流，
不依赖手机 App、不截屏、不拍屏。

> **免责声明**：仅供学习与研究，仅限在本人合法持有且获准改造的设备上使用。
> 手法只修改运行中进程内存，不写磁盘、不刷固件，重启服务即恢复。与 DJI 无隶属关系。

## 快速开始

```bat
git clone https://github.com/happy1zao/osmo360-live-preview.git
cd osmo360-live-preview
py -3 -m pip install -r requirements.txt
py -3 -c "import osmo360; print(osmo360.__version__)"
```

前置：`adb` 与 `ffmpeg` 在 PATH 中（或用环境变量 `ADB` / `FFMPEG` 指定路径）；
相机已 root。设备序列号通过 `ADB_SERIAL` 指定，单设备可留空。

### 中继预览（Windows 一键）

```bat
start_preview.bat
```

等价手动步骤见 [`docs/00_START_HERE.zh.md`](docs/00_START_HERE.zh.md)。

### LRF 边写边读 2:1 预览（相机录制中）

```bat
pano_live.bat
:: 等价于
py -3 tools\pc\r156_lrf_live2.py --file auto --secs 3600 --scale 1280
```

### 设备端探针编译（可选，hook 方案需要）

```sh
sh probe/build.sh
```

产物 `probe/libdcam_hook.so` 用 bind mount 部署到 `/usr/lib/libdcam_hook.so`，
步骤与回滚见 [`probe/README.md`](probe/README.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `osmo360/` | Python 包：`transport`（adb/转发）、`video`（重组/解码）、`audio`（四麦采集）、`control`（相机控制）、`session`（自建会话）、`pipeline`（一键串联）、`live`（实时流） |
| `probe/` | 设备端 C 探针源码、相机端 Python/Shell 脚本、交叉编译脚本、回滚脚本 |
| `tools/pc/` | PC 端分析与预览脚本（清单见 [`tools/pc/README.md`](tools/pc/README.md)） |
| `preview.py` | PC 预览窗口 + 控制控制台 |
| `samples/` | 样本占位说明；抓包/码流/截图等大文件不入库 |
| `docs/` | 主动指南、详细原理、轮次归档 |

## 文档

- [快速上手（中文）](docs/00_START_HERE.zh.md) · [Start Here (English)](docs/00_START_HERE.en.md)
- [状态与里程碑（中文）](docs/01_STATUS.zh.md) · [Status (English)](docs/01_STATUS.en.md)
- [预览详细文档（中文）](docs/preview-guide.zh.md) · [Detailed Guide (English)](docs/preview-guide.en.md)
- [轮次归档](docs/archive/)：早期逆向记录，中英成对（`.zh.md` / `.en.md`）；部分早期脚本未随仓库发布

## 依赖

Python 第三方包见 [`requirements.txt`](requirements.txt)（`numpy`、`opencv-python`）。
`adb`、`ffmpeg` 为外部二进制，不在 pip 依赖内。

## 许可

[MIT](LICENSE) © 2026 happy1zao
