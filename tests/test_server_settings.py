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
