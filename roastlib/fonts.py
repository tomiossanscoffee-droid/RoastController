# ============================================================
# Roast Studio
# fonts.py : 日本語フォント設定ユーティリティ
# ============================================================
"""
Google Colab / Linux環境では、matplotlibのデフォルトフォント(DejaVu Sans)に
日本語グリフが無いため、グラフのタイトルや凡例が文字化けする。

このモジュールは、
  1) IPA/Noto CJK系フォントを探して matplotlib の FontManager に直接 addfont() する
  2) 見つかったフォント名を rcParams['font.family'] に設定する
処理をまとめたもの。

CompareEngine 等、グラフを描画する側は font.family を勝手に上書きしないこと。
"""

from __future__ import annotations

import glob
from typing import Optional


def setup_japanese_font(verbose: bool = True) -> Optional[str]:
    """システムにインストール済みの日本語フォントを探してmatplotlibに設定する。

    Colabでは事前に以下を実行してフォント自体をインストールしておく必要がある:
        !apt-get update -qq
        !apt-get install -qq -y fonts-ipafont-gothic fonts-noto-cjk
        !fc-cache -f

    戻り値: 見つかったフォントファミリ名。見つからなければ None。
    """
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt

    search_dirs = ["/usr/share/fonts", "/usr/local/share/fonts"]
    candidates = []
    for d in search_dirs:
        candidates += glob.glob(f"{d}/**/*.ttf", recursive=True)
        candidates += glob.glob(f"{d}/**/*.otf", recursive=True)

    keywords = ["ipag", "ipam", "japanese", "notosanscjk", "notosanscjkjp", "notoserifcjk"]

    jp_font_family = None
    for path in candidates:
        if any(k in path.lower() for k in keywords):
            try:
                fm.fontManager.addfont(path)
                prop = fm.FontProperties(fname=path)
                jp_font_family = prop.get_name()
                break
            except Exception:  # noqa: BLE001
                continue

    if jp_font_family:
        plt.rcParams["font.family"] = jp_font_family
        plt.rcParams["axes.unicode_minus"] = False
        if verbose:
            print(f"[OK] 日本語フォント設定: {jp_font_family}")
    else:
        if verbose:
            print("[WARN] 日本語フォントが見つかりませんでした。グラフの日本語が文字化けする可能性があります。")
            print("       Colabの場合、下記を実行してからランタイムを再起動してください:")
            print("       !apt-get update -qq && !apt-get install -qq -y fonts-ipafont-gothic fonts-noto-cjk")

    return jp_font_family
