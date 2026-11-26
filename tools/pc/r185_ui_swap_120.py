#!/usr/bin/env python3
"""r185_ui_swap_120.py —— 把 UI 档位表里「全景 4K100(P100)」原地换成 P120

背景事实（本轮已证）：
  * 相机侧能力表（dji_default/current_camera_ov68a40_us.json）原始版就接受
    VIDEO_PANO + RESOLUTION_1X1_1920P + FRAMERATE_P120，实测能录出
    双 1920×1920@119.88 的 4K 全景（CAM_20261127023958_0093_D.OSV）。
  * UI 只显示 4K100，是因为 /system/etc/capability.json 的
    camcap_video_format[4]（topmode:video_pano）最后一条写死 P100。
  * 该文件是「定长数组」语义（num == len(video_formats) 必须相等），
    追加条目会让 camera3 崩溃循环，所以只能原地改值，不能增删。

做法：只把 block4 第 11 条（1X1_1920P + P100）的 frame_rate 改成 P120，
       num 保持 12，长度保持 12。

用法: python r185_ui_swap_120.py <in.json> <out.json>
"""
import json
import sys

P100 = "DUSS_MSG_V_FRAMERATE_P100"
P120 = "DUSS_MSG_V_FRAMERATE_P120"


def main():
    src, dst = sys.argv[1], sys.argv[2]
    j = json.load(open(src))
    vf = j["camcap_video_format"]
    blk = vf[4]
    formats = blk["version1"]["video_formats"]
    hit = 0
    for f in formats:
        if f["resolution"].endswith("1X1_1920P") and f["frame_rate"] == P100:
            f["frame_rate"] = P120
            hit += 1
    if hit != 1:
        sys.exit(f"expected exactly 1 hit, got {hit}")
    # num 与长度必须一致，严禁增删
    assert blk["version1"]["num"] == len(formats), "num/len mismatch"
    json.dump(j, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent="\t")
    print(f"patched {hit} entry; num={blk['version1']['num']} len={len(formats)} -> {dst}")


if __name__ == "__main__":
    main()
