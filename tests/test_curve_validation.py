# -*- coding: utf-8 -*-
"""カーブ(roast/fan)の検証(app/server.py の _clean_curve)。

数値でない値やnullが混ざったカーブを渡すと、味を推測とプロファイル健全性が
500になっていた(比較の途中で int と str を比べて TypeError)。画面から来る
カーブは常に数値だが、保存ファイルを手で編集した場合やAPIを直接叩かれた場合には
混ざりうる。ガイド温度で同じ直し方をしたのと同種。
"""
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.server import _clean_curve  # noqa: E402


@pytest.mark.parametrize("points,expected", [
    ([[0, 185], [60, 95]], [[0.0, 185.0], [60.0, 95.0]]),
    ([[0, 185.5], ["60", "95"]], [[0.0, 185.5], [60.0, 95.0]]),
    ([(0, 185), (60, 95)], [[0.0, 185.0], [60.0, 95.0]]),
    ([[0, 185, "余分"], [60, 95]], [[0.0, 185.0], [60.0, 95.0]]),
    ([], []),
])
def test_数値として扱える形はそのまま通す(points, expected):
    out, err = _clean_curve(points)
    assert err is None
    assert out == expected


@pytest.mark.parametrize("points", [
    [[0, "abc"], [100, 200]],
    [[0, 185], [60, None], [300, 240]],
    [[0, 185], "x"],
    [[0, 185], [60]],
    [[0, 185], 60],
    [[0, 185], [60, True]],
    [[True, 185], [60, 95]],
    [[0, 185], [60, float("nan")]],
    [[0, 185], [60, float("inf")]],
    "カーブではない",
    None,
    {"a": 1},
])
def test_扱えない値は理由付きで弾く(points):
    out, err = _clean_curve(points)
    assert out is None
    assert err and isinstance(err, str)


def test_理由に何番目かが入る():
    _, err = _clean_curve([[0, 185], [60, 95], [120, "x"]])
    assert "3番目" in err


def test_名前が理由に入る():
    _, err = _clean_curve([[0, "x"]], "fan")
    assert err.startswith("fan")


def test_通したあとは全部floatになる():
    out, _ = _clean_curve([[0, 185], ["60", "95"]])
    assert all(isinstance(v, float) for p in out for v in p)
    assert all(math.isfinite(v) for p in out for v in p)
