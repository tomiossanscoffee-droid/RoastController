# -*- coding: utf-8 -*-
"""温度ガイド線の値の正規化(app/server.py の _clean_guide_temp)。

数値以外や範囲外が保存されると、焙煎度の判定やフェーズ分割の温度比較に
そのまま渡ってTypeErrorになり、味を推測・プロファイル生成が500になる
(2026-08、APIを直接叩いて発見)。読み書きの両方で正規化して防ぐ。
"""
import pytest

from app.server import (
    DEFAULT_GUIDE_TEMPS, GUIDE_TEMP_MAX, GUIDE_TEMP_MIN, _clean_guide_temp,
)


@pytest.mark.parametrize("value,expected", [
    (223, 223), (223.4, 223), (223.6, 224), ("223", 223),
    (GUIDE_TEMP_MIN, GUIDE_TEMP_MIN), (GUIDE_TEMP_MAX, GUIDE_TEMP_MAX),
])
def test_数値として扱える値はそのまま(value, expected):
    assert _clean_guide_temp(value) == expected


@pytest.mark.parametrize("value", [
    None, "", "abc", [1], {"a": 1}, True, float("nan"),
    GUIDE_TEMP_MIN - 1, GUIDE_TEMP_MAX + 1, -10, 9999,
])
def test_扱えない値は未設定になる(value):
    assert _clean_guide_temp(value) is None


def test_既定値そのものが正規化を通る():
    """公開版は初期値を入れてある(184/223/242)ため、Noneとは限らない。

    どちらの版でも、既定値がそのまま使える形(未設定か、正規化しても変わらない値)で
    あることだけを確かめる。
    """
    assert set(DEFAULT_GUIDE_TEMPS) == {"colorChange", "firstCrack", "secondCrack"}
    for key, value in DEFAULT_GUIDE_TEMPS.items():
        assert _clean_guide_temp(value) == value, f"{key} の既定値 {value} が正規化で変わる"


# ------------------------------------------------------------
# 空のレコードを作らせない検証
# ------------------------------------------------------------
from app.server import _BEAN_PURCHASE_FIELDS, _bean_purchase_is_empty  # noqa: E402


def test_全項目が空の豆は空とみなす():
    assert _bean_purchase_is_empty({f: "" for f in _BEAN_PURCHASE_FIELDS})
    assert _bean_purchase_is_empty({})
    # 空白だけの入力も空扱い
    assert _bean_purchase_is_empty({f: "   " for f in _BEAN_PURCHASE_FIELDS})
    # Noneが入っていても落ちない
    assert _bean_purchase_is_empty({f: None for f in _BEAN_PURCHASE_FIELDS})


@pytest.mark.parametrize("field", _BEAN_PURCHASE_FIELDS)
def test_どれか1項目でも入っていれば空ではない(field):
    entry = {f: "" for f in _BEAN_PURCHASE_FIELDS}
    entry[field] = "あ"
    assert not _bean_purchase_is_empty(entry)


# ------------------------------------------------------------
# 配色(theme)
# ------------------------------------------------------------
import re  # noqa: E402
from pathlib import Path  # noqa: E402

from app.server import APP_THEMES, DEFAULT_APP_SETTINGS as _SETTINGS  # noqa: E402

INDEX = Path(__file__).resolve().parent.parent / "app/static/index.html"
MOBILE = Path(__file__).resolve().parent.parent / "app/static/mobile.html"


def test_配色の既定はダーク():
    assert _SETTINGS["theme"] == "dark"
    assert APP_THEMES == ("dark", "light", "contrast")


@pytest.mark.parametrize("page", [INDEX, MOBILE])
def test_JSが読むCSS変数が全配色で定義されている(page):
    """cssVar() で読む変数が :root に無いと、その色だけ既定の灰色になる。

    dark以外は :root を継承するので、:root に全部あることを確かめれば足りる。
    """
    s = page.read_text(encoding="utf-8")
    used = set(re.findall(r"cssVar\('(--[a-z0-9-]+)'", s))
    root = re.search(r":root\s*\{(.*?)\n  \}", s, re.S).group(1)
    defined = set(re.findall(r"(--[a-z0-9-]+):", root))
    missing = sorted(used - defined)
    assert not missing, f"{page.name} の :root に無い変数: {missing}"


@pytest.mark.parametrize("page", [INDEX, MOBILE])
def test_追加した2配色が定義されている(page):
    s = page.read_text(encoding="utf-8")
    for theme in ("light", "contrast"):
        assert f'[data-theme="{theme}"]' in s, f"{page.name} に {theme} が無い"


@pytest.mark.parametrize("page", [INDEX, MOBILE])
def test_グラフの色が直書きされていない(page):
    """JS側に色を直書きすると、配色を切り替えてもそこだけ変わらない。"""
    s = page.read_text(encoding="utf-8")
    tail = s.split("</style>", 1)[1]
    hard = set(re.findall(r"'#[0-9a-fA-F]{6}'|'rgba?\([0-9., ]+\)'", tail))
    hard.discard("'#888888'")   # cssVar() が変数を見つけられなかった時の保険
    assert not hard, f"{page.name} に直書きの色が残っている: {sorted(hard)}"
