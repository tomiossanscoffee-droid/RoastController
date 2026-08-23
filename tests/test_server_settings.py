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


def test_設定の3項目が揃っている():
    assert set(DEFAULT_GUIDE_TEMPS) == {"colorChange", "firstCrack", "secondCrack"}
    assert all(v is None for v in DEFAULT_GUIDE_TEMPS.values())
