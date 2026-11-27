# tools/pc/ —— PC 端分析与预览脚本

编号 `rNNN_*.py` 为按时间顺序保留的实验脚本；**只增不改**（新结论写新编号，
旧脚本保留原样以维持可复现性）。adb / 设备序列号 / ffmpeg 均通过环境变量或
PATH 解析：`ADB`、`ADB_SERIAL`、`FFMPEG`（缺省走 PATH）。

## 已随仓库发布

| 脚本 | 功能定位 |
|---|---|
| `pano_live97.py` | 2:1 代理流实时预览启动器：零落盘，hook 探针 proxy tee → TCP 8888，体检 + 长录会话 + 回放 |
| `r156_lrf_live2.py` | **LRF 实时预览 v4（当前主推）**：按 GOF 锚点捞 NAL 流式解码，自动对齐 SPS/PPS/IDR |
| `r147_lrf_live.py` | LRF 实时预览 v3（legacy 对照版，需外部 `--sps` 参数集片段） |
| `r148_pano_preview.py` | 一键：起长录会话 → 跟随最新 `.LRF` → 交给 r147 预览 |
| `r149_fps_audit.py` | 帧率真伪审计：总帧数/时长/相邻帧差/重复帧连续段/unique fps |
| `r150_fps_lag.py` | 帧差-间隔（lag）曲线，判定 120fps 是否为真实时间采样 |
| `r184_proxy_trim.py` | 从 proxy H.264 AnnexB 找到 SPS/PPS+IDR 起点后截断 |
| `r185_ui_swap_120.py` | UI 档位表原地把 P100 条目改成 P120（定长数组，不增删条目） |
| `r88_ackmax.py` | 第十轮配置下 ack 限速 / treq 参数 A/B 台，量真实 fps 与自激速率 |
| `r91_cst_live.py` | 用相机自带 `dji_cst` 直接拉起 liveview，对照观察视频通路 |

## 未随仓库发布（历史文档中被引用）

以下脚本属于早期逆向 / 抓包解剖的一次性实验稿，**未包含在本仓库内**；
归档文档（`docs/` 下的轮次记录）提到它们仅为保留实验脉络，不能照其中命令直接运行：

- `r74_e2e_dec.py`、`r87_fragcov.py`、`r90_bootorder.py`、`r110_live_h265.py`
- `r145_lrf_live.py`、`r146_lrf_live.py`
- `r151_lrf_boxes.py`、`r152_lrf_scan.py`、`r153_lrf_walk.py`、
  `r154_lrf_nal_extract.py`、`r155_lrf_gof_extract.py`
- 其余未列出的 `r*.py` 研究草稿同理，不在此发布。

如需复现某条历史结论，请先确认上表对应脚本是否已发布；未发布脚本的逻辑
以归档文档的文字描述为准，不要假设仓库内存在同名文件。
