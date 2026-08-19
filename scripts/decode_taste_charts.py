#!/usr/bin/env python3
# ============================================================
# Roast Studio
# scripts/decode_taste_charts.py
# ------------------------------------------------------------
# THE_ROAST_Extract/profile_charts/ 内のレーダーチャート画像
# (香り・酸味・苦味・後味・ボディの5軸、Panasonic純正アプリ由来)を
# 画像解析でデコードし、taste_charts.json を作成する。
#
# 背景(2026-07、実データで検証済み):
#   焙煎カーブの形状(ABCフェーズ時間・RoR等)は、焙煎度を除くと実測の
#   味データとほぼ無相関だった(相関係数±0.11以内)。一方「産地国」は
#   同一焙煎度内のばらつきの30〜40%、「産地国+品種」の組み合わせでは
#   54〜62%を説明した。つまり味は主にカーブではなく豆自体の資質に
#   由来する。そのためこのツールは:
#     1. 実際のチャート画像がある豆は、画像を直接デコードした実測値を使う
#        (誤差ゼロ)
#     2. 画像が無い豆(全体の1割弱)だけ、産地国+品種が一致する他の豆の
#        平均値で補完する(「estimated」として区別する)
#
# チャート画像の構造(実データで座標を特定済み):
#   360x295px、中心(182,158)、外側半径132px、5軸は真上から時計回りに
#   72度間隔(香り→酸味→苦味→後味→ボディ)。緑の塗りつぶし領域が
#   中心からどこまで届いているかで値(0-100%)を測る。
#
# 使い方:
#   python3 scripts/decode_taste_charts.py
# ============================================================

from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
CHARTS_DIR = REPO_ROOT / "THE_ROAST_Extract" / "profile_charts"
BEANINFO_DIR = REPO_ROOT / "THE_ROAST_Extract" / "beaninfo"
OUTPUT_PATH = REPO_ROOT / "THE_ROAST_Extract" / "taste_charts.json"

CENTER = (182, 158)
OUTER_R = 132
AXES = ["香り", "酸味", "苦味", "後味", "ボディ"]
ANGLES = [-90, -90 + 72, -90 + 144, -90 + 216, -90 + 288]

_CHART_NAME_RE = re.compile(r"^GB(\d{4})\d{4}\d{6}_profilechart([123])\.png$")
_BEANINFO_NAME_RE = re.compile(r"^GB(\d{4})\d{4}\d{6}_beaninfo\.csv$")


def _is_greenish(c: tuple[int, int, int]) -> bool:
    r, g, b = c
    return g > r + 15 and g > b + 30 and g > 90


def decode_chart(path: Path) -> dict[str, int]:
    """レーダーチャート画像1枚から、5軸それぞれの値(0-100)を測る。"""
    img = Image.open(path).convert("RGB")
    w, h = img.size
    px = img.load()
    cx, cy = CENTER
    values = {}
    for axis, angle in zip(AXES, ANGLES):
        rad = math.radians(angle)
        dx, dy = math.cos(rad), math.sin(rad)
        last_green = 0
        for r in range(1, OUTER_R + 5):
            x, y = int(cx + dx * r), int(cy + dy * r)
            if not (0 <= x < w and 0 <= y < h):
                break
            if _is_greenish(px[x, y]):
                last_green = r
        values[axis] = round(last_green / OUTER_R * 100)
    return values


def load_beaninfo_country_variety() -> dict[str, tuple[str, str]]:
    """bean_code -> (国, 品種の代表1つ) の対応表(補完の際のグループ化キーに使う)。"""
    result = {}
    if not BEANINFO_DIR.is_dir():
        return result
    for path in BEANINFO_DIR.glob("*.csv"):
        m = _BEANINFO_NAME_RE.match(path.name)
        if not m:
            continue
        bean_code = m.group(1)
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                row = next(csv.reader(fh))
        except Exception:
            continue
        if len(row) < 28:
            continue
        country = row[1].strip() or row[6].strip()
        variety_raw = row[15].strip()
        variety = re.split(r"[、,\s　]+", variety_raw)[0] if variety_raw else "不明"
        result[bean_code] = (country or "不明", variety or "不明")
    return result


def main() -> int:
    if not CHARTS_DIR.is_dir():
        print(f"エラー: {CHARTS_DIR} がありません。先に scripts/extract_from_ios_backup.py を実行してください。")
        return 1

    # 1. 実際の画像をすべてデコードする(bean_code -> {chart1: {...}, chart2: {...}, chart3: {...}})
    real: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
    for path in sorted(CHARTS_DIR.glob("*.png")):
        m = _CHART_NAME_RE.match(path.name)
        if not m:
            continue
        bean_code, chart_no = m.groups()
        real[bean_code][f"chart{chart_no}"] = decode_chart(path)

    print(f"実測チャートをデコード: {len(real)}豆 x 最大3焙煎度")

    # 2. 産地国+品種でグループ化した平均値を計算(画像が無い豆の補完用)
    country_variety = load_beaninfo_country_variety()
    group_values: dict[tuple[str, str, str], list[int]] = defaultdict(list)  # (country,variety,chart_no) -> [values...]
    for bean_code, charts in real.items():
        cv = country_variety.get(bean_code)
        if not cv:
            continue
        for chart_no, values in charts.items():
            for axis in AXES:
                group_values[(cv[0], cv[1], chart_no, axis)].append(values[axis])

    def group_average(country: str, variety: str, chart_no: str) -> Optional[dict[str, int]]:
        result = {}
        for axis in AXES:
            vals = group_values.get((country, variety, chart_no, axis))
            if not vals:
                return None
            result[axis] = round(sum(vals) / len(vals))
        return result

    # 3. 焙煎度(chart1/2/3)ごとに、実データが無ければ産地国+品種の平均で補完する。
    # (同じ豆でも「浅煎りの画像はあるが深煎りは無い」等、チャート番号単位で
    #  欠けているケースがあるため、豆単位ではなくチャート番号単位で判定する)
    output: dict[str, dict] = {}
    real_count = 0
    estimated_count = 0
    for bean_code, cv in country_variety.items():
        country, variety = cv if cv else ("不明", "不明")
        existing = real.get(bean_code, {})
        entry: dict = {}
        sources: dict[str, str] = {}
        for chart_no in ["chart1", "chart2", "chart3"]:
            if chart_no in existing:
                entry[chart_no] = existing[chart_no]
                sources[chart_no] = "real"
                real_count += 1
            else:
                avg = group_average(country, variety, chart_no)
                if avg:
                    entry[chart_no] = avg
                    sources[chart_no] = f"推定値({country}・{variety}の実測平均)"
                    estimated_count += 1
        if entry:
            entry["chart_source"] = sources
            output[bean_code] = entry

    print(f"実測値をそのまま使用: {real_count}件(豆x焙煎度)")
    print(f"産地国+品種の平均で補完: {estimated_count}件(豆x焙煎度)")

    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"完了: {len(output)}豆分を {OUTPUT_PATH} に保存しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
