# -*- coding: utf-8 -*-
"""`TinyGladeShotWallTexture.py` 那两趟 PNG 的逐像素判据。

**在编辑器外跑**（普通 Python + numpy + PIL），不是 UE 脚本 —— 出图脚本只负责产出
`walltex_<tag>_{peel,flat}_<机位>.png`，判据在这里算。分开的理由：`read_render_target_pixel`
逐像素读 144 万次太慢，而本判据要的正是**全分辨率**的差异率（采样格会把细碎的凸出砖漏掉）。

三条判据（阈值与读法见 `TinyGladeShotWallTexture.py` 文件头）：

  ① 两趟差异像素率 > 0  ⇒ 剥落 / 凸出真的走到了像素，而不只是"材质挂上去了"。
    另外它**自带一条高度判据**：`base`（墙脚）应当显著高于 `mid`（半墙高）。
    2026-09-04 改前 66.0% / 43.3%（几乎没落差 ⇒ 高度项被噪声淹掉），
    改后 50.6% / 6.5%（渐变读得出来）。
  ② 差异像素在 peel 那趟的均色不是中性灰 —— 引擎把不支持的母材质静默换成默认材质时
    R≈G≈B，砖色卡不是。这一条把"材质没编译成功"从"确实是砖"里分出来。
  ③ flat 那趟自身的标准差 > 0 ⇒ 灰泥贴图确实采到了；纯色说明贴图根本没生效。

用法::

    python TinyGladeAnalyzeWallShots.py v3
"""
import os
import sys

import numpy as np
from PIL import Image

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "..", "..", "Saved", "TinyGladeShots")
DIR = os.path.normpath(os.environ.get("TG_SHOT_DIR", DIR))
CAMS = ["wide", "face", "base", "mid", "corner"]
THRESH = 6.0 / 255.0     # 每通道 6/255 以上才算"变了"，压掉 LDR 抖动


def load(path):
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0


def main(tag):
    print("shots dir: %s" % DIR)
    print("%-8s %8s %9s   %-24s %-24s %8s"
          % ("cam", "diff%", "px", "peel 差异像素均色", "flat 全图均色", "flat std"))
    for cam in CAMS:
        pa = os.path.join(DIR, "walltex_%s_peel_%s.png" % (tag, cam))
        pb = os.path.join(DIR, "walltex_%s_flat_%s.png" % (tag, cam))
        if not (os.path.exists(pa) and os.path.exists(pb)):
            print("%-8s  MISSING" % cam)
            continue
        a, b = load(pa), load(pb)
        m = np.abs(a - b).max(-1) > THRESH
        n = int(m.sum())
        col = a[m].mean(0) if n else np.zeros(3)
        bm = b.mean((0, 1))
        print("%-8s %7.2f%% %9d   (%.3f %.3f %.3f)        (%.3f %.3f %.3f)        %6.4f"
              % (cam, 100.0 * n / m.size, n, col[0], col[1], col[2], bm[0], bm[1], bm[2], float(b.std())))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "v1")
