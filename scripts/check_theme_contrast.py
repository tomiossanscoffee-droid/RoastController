#!/usr/bin/env python3
# ============================================================
# Roast Studio
# scripts/check_theme_contrast.py
# ------------------------------------------------------------
# 配色(app/static/index.html の :root と [data-theme=...])のコントラスト検算。
#
# 焙煎中は機器のそばから画面を見るので、文字が背景に埋もれると実害がある
# (以前、エラー表示の赤が 3.3〜3.6 しかなく気づきにくかった)。
# 文字色は背景に対して WCAG AA の 4.5:1 以上、グラフの線は背景から見分けが
# つく 3:1 以上を目安にする。
#
#   venv/bin/python scripts/check_theme_contrast.py
# ============================================================
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HTML = REPO / "app/static/index.html"

# 文字として読む色(背景に対して4.5:1以上)
TEXT_VARS = ["--text", "--text-muted", "--amber", "--slate", "--teal", "--sage", "--danger"]
# グラフの線(背景に対して3:1以上あれば追える)
LINE_VARS = ["--line-ref", "--line-edit", "--line-fan", "--line-live", "--line-compare",
             "--line-log", "--line-ror", "--line-ror-profile", "--line-ror-log",
             "--line-bean", "--line-bean-log", "--line-energy", "--line-error",
             "--line-cooldown", "--guide-cc", "--guide-fc", "--guide-sc", "--axis"]
BACKGROUNDS = ["--bg", "--panel"]

TEXT_MIN, LINE_MIN = 4.5, 3.0


def parse_themes(text: str) -> dict:
    """:root と [data-theme="..."] のブロックから、変数名→色 を取り出す。"""
    themes = {}
    root = re.search(r":root\s*\{(.*?)\n  \}", text, re.S)
    if root:
        themes["dark(既定)"] = dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", root.group(1)))
    for m in re.finditer(r'\[data-theme="([a-z]+)"\]\s*\{(.*?)\n  \}', text, re.S):
        themes[m.group(1)] = dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", m.group(2)))
    return themes


def to_rgb(value: str):
    v = value.strip()
    if v.startswith("#") and len(v) == 7:
        return tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", v)
    if m:
        return tuple(int(m.group(i)) for i in (1, 2, 3))
    return None


def luminance(rgb):
    def ch(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def main():
    themes = parse_themes(HTML.read_text(encoding="utf-8"))
    if not themes:
        print("配色を読み取れませんでした")
        return 1
    ng = 0
    for name, vars_ in themes.items():
        print(f"\n■ {name}")
        bgs = [(b, to_rgb(vars_.get(b, ""))) for b in BACKGROUNDS]
        bgs = [(b, c) for b, c in bgs if c]
        for group, names, minimum in (("文字", TEXT_VARS, TEXT_MIN), ("線", LINE_VARS, LINE_MIN)):
            worst = []
            for v in names:
                rgb = to_rgb(vars_.get(v, ""))
                if not rgb:
                    continue
                ratios = [(bn, contrast(rgb, bc)) for bn, bc in bgs]
                low = min(ratios, key=lambda x: x[1])
                if low[1] < minimum:
                    worst.append((v, low[0], low[1]))
            if worst:
                ng += len(worst)
                print(f"  {group}(基準{minimum}:1) 未達 {len(worst)}件")
                for v, bn, r in sorted(worst, key=lambda x: x[2]):
                    print(f"     {v:20} vs {bn:8} {r:.2f}")
            else:
                print(f"  {group}(基準{minimum}:1) すべて満たす")
        # 線どうしが似すぎていないか(同系色を並べると見分けられない)
        close = []
        cols = [(v, to_rgb(vars_.get(v, ""))) for v in LINE_VARS if to_rgb(vars_.get(v, ""))]
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                d = sum(abs(a - b) for a, b in zip(cols[i][1], cols[j][1]))
                if d < 40:
                    close.append((cols[i][0], cols[j][0], d))
        if close:
            print(f"  線どうしが近い組み合わせ {len(close)}件(RGB差の合計<40)")
            for a, b, d in close:
                print(f"     {a} と {b}: {d}")
        else:
            print("  線どうしは十分に離れている")
    print("\n結果:", "問題なし" if ng == 0 else f"★基準未達 {ng}件")
    return 0 if ng == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
