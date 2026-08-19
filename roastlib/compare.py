# ============================================================
# Roast Studio
# compare.py : CompareEngine
# ============================================================
"""
複数の RoastAnalysis を並べて比較するためのグラフ・サマリー出力。

注意:
  font.family はこのモジュールでは一切上書きしない。
  日本語表示が必要な場合は、呼び出し側で
  `roastlib.fonts.setup_japanese_font()` を先に実行しておくこと。
  （過去、このモジュール内で 'DejaVu Sans' に強制上書きしており、
    日本語が文字化けするバグがあったため、意図的に触らない設計にしている）
"""

from __future__ import annotations

from typing import List

import matplotlib.pyplot as plt
import numpy as np

from .analyzer import RoastAnalysis


class CompareEngine:
    @staticmethod
    def plot_comparison(analyses: List[RoastAnalysis], title: str = "Profile Comparison"):
        if not analyses:
            return

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 10), sharex=True)
        colors = plt.cm.tab10(np.linspace(0, 1, len(analyses)))

        for i, analysis in enumerate(analyses):
            label = analysis.profile.name[:35]
            color = colors[i]

            # roastPointsの実データそのまま描画する
            # （cooldownPointまでの水平延長線は描かない: 実測の冷却カーブが無いため）
            t = [e.time_sec for e in analysis.timeline]
            temp = [e.temperature for e in analysis.timeline]
            ax1.plot(t, temp, label=label, color=color, linewidth=2.5)

            f_t = analysis.profile.fan.x
            f_v = analysis.profile.fan.y
            if f_t:
                ax2.plot(f_t, f_v, label=label, color=color, linestyle="--")

        ax1.set_ylabel("Temperature (°C)")
        ax1.set_title(title)
        ax1.grid(True, alpha=0.3)
        ax1.legend(fontsize=9, loc="upper left")

        ax2.set_xlabel("Time (seconds)")
        ax2.set_ylabel("Fan (%)")
        ax2.grid(True, alpha=0.3)
        ax2.legend(fontsize=9, loc="upper left")

        # 焙煎終了(=冷却開始)を赤点線、冷却完了予定を橙点線で表示（目安のみ、線は延長しない）
        for analysis in analyses:
            s = analysis.summary
            if s.cooldown_start_sec:
                ax1.axvline(s.cooldown_start_sec, color="red", linestyle=":", alpha=0.5)
                ax2.axvline(s.cooldown_start_sec, color="red", linestyle=":", alpha=0.5)
            if s.cooldown_end_sec:
                ax1.axvline(s.cooldown_end_sec, color="orange", linestyle=":", alpha=0.5)
                ax2.axvline(s.cooldown_end_sec, color="orange", linestyle=":", alpha=0.5)

        plt.tight_layout()
        plt.show()

    @staticmethod
    def compare_summary(analyses: List[RoastAnalysis]) -> None:
        print("=== Comparison Summary ===")
        print(f"{'Profile':<35} {'Roast(s)':<9} {'CDstart':<8} {'CDend':<8} {'MaxT':<6} {'AvgT':<7} {'FanΔ':<5}")
        print("-" * 90)
        for a in analyses:
            s = a.summary
            cd_s = s.cooldown_start_sec if s.cooldown_start_sec is not None else "-"
            cd_e = s.cooldown_end_sec if s.cooldown_end_sec is not None else "-"
            print(
                f"{a.profile.name[:34]:<35} {s.roast_time_sec:<9} {str(cd_s):<8} {str(cd_e):<8} "
                f"{a.temperature.max_temp:<6.0f} {a.temperature.avg_temp:<7.1f} {a.fan.change_count:<5}"
            )
        print("-" * 90)
