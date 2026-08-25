# ============================================================
# Roast Studio
# tests/test_calibration.py
# ------------------------------------------------------------
# 豆温度モデルの較正(roastlib/calibration.py)の検証。
#
#   venv/bin/python -m pytest tests/test_calibration.py
# ============================================================
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import roastlib.energy as E  # noqa: E402
from roastlib import calibration as C  # noqa: E402
from roastlib.profile_generator import (  # noqa: E402
    MAX_TEMPERATURE, MAX_ROAST_SECONDS,
)

P = C.CALIBRATION_PROFILE


def observe(cal):
    """その定数で校正用プロファイルを焼いたとき、人が測れる値がどうなるか。"""
    r = E.estimate(P["roast"], P["fan"], cal=cal)
    s = r["series"]
    spread = cal.get("CRACK_SPREAD", E.CRACK_SPREAD)

    def at(v):
        return next((p["t"] for p in s if p["bean"] >= v), None)

    def mass(t):
        return next((p["mass"] for p in s if p["t"] >= t), s[-1]["mass"])

    return {
        "fcStart": at(E.T_FC_BEAN),
        "fcEnd": at(E.T_FC_BEAN + 6.0 * spread),
        "scStart": at(C.T_SC_BEAN),
        "greenG": 50.0,
        "roastedG": round(r["roasted_g"], 1),
        "abortAt": 360.0,
        "abortG": round(mass(360.0) * 1000.0, 1),
    }


# ---- 校正用プロファイルそのもの ----

def test_校正用プロファイルは焙煎機の仕様に収まる():
    roast, fan = P["roast"], P["fan"]
    assert len(roast) <= 20 and len(fan) <= 20
    assert all(0 <= t <= MAX_ROAST_SECONDS for t, _ in roast)
    assert all(0 < v <= MAX_TEMPERATURE for _, v in roast)
    assert all(50 <= v <= 100 for _, v in fan)
    # 時刻は昇順で、同じ時刻が並ばない
    assert all(b[0] > a[0] for a, b in zip(roast, roast[1:]))
    assert all(b[0] > a[0] for a, b in zip(fan, fan[1:]))


def test_校正用プロファイルは2ハゼまで届く():
    """届かないと、測れる5項目のうち1つが取れなくなる。"""
    m = observe({})
    assert m["scStart"] is not None
    assert m["fcStart"] is not None and m["fcEnd"] is not None


def test_校正用プロファイルは普段焼く範囲に収まる():
    """焼き飛ばした条件で定数を当てはめても、普段の焙煎には役立たない。

    実在の深煎りプリセットは1.250〜1.258。
    """
    r = E.estimate(P["roast"], P["fan"])
    assert 1.22 < r["roast_index"] < 1.30


def test_校正用プロファイルは1ハゼ付近がゆるやか():
    """時刻を±5秒読み違えても、温度換算で±1.2℃に収まること。"""
    s = E.estimate(P["roast"], P["fan"])["series"]
    i = next(i for i, p in enumerate(s) if p["bean"] >= E.T_FC_BEAN)
    ror = (s[i + 15]["bean"] - s[i]["bean"]) / 15 * 60
    assert 0 < ror * 5 / 60 < 1.2, f"1ハゼ付近が急すぎます({ror:.1f}℃/分)"


# ---- 当てはめ ----

def test_ずらした定数を当てはめで戻せる():
    true = {"U0": 1.15, "CRACK_SPREAD": 1.40, "H_ENDO": 0.85,
            "K_PYRO": 1.30, "K_DRY": 0.75}
    cal = {
        "U0": E.U0 * true["U0"], "CRACK_SPREAD": E.CRACK_SPREAD * true["CRACK_SPREAD"],
        "H_ENDO": E.H_ENDO * true["H_ENDO"], "K_PYRO": E.K_PYRO * true["K_PYRO"],
        "K_SURFACE": E.K_SURFACE * true["K_DRY"], "K_INNER": E.K_INNER * true["K_DRY"],
    }
    got = C.fit(observe(cal))["scale"]
    for k, want in true.items():
        assert got[k] == pytest.approx(want, rel=0.10), f"{k}: 期待{want} 実際{got[k]}"


def test_当てはめた定数で焼き直すと測定値を再現する():
    """実機がモデルと少し違っていた場合を作り、その測定値を再現できること。

    数字を手で置くと較正の範囲外を要求してしまうことがあるので、モデル自身を
    ずらして作った(=必ず到達できる)測定値を使う。
    """
    meas = observe({"U0": E.U0 * 1.15, "H_ENDO": E.H_ENDO * 0.9,
                    "K_PYRO": E.K_PYRO * 1.2, "CRACK_SPREAD": E.CRACK_SPREAD * 1.3,
                    "K_SURFACE": E.K_SURFACE * 0.8, "K_INNER": E.K_INNER * 0.8})
    res = C.fit(meas)
    assert not res["notes"], f"範囲の端に張り付きました: {res['notes']}"
    back = observe(res["overrides"])
    assert abs(back["fcStart"] - meas["fcStart"]) <= 5
    assert abs(back["fcEnd"] - meas["fcEnd"]) <= 8
    assert abs(back["scStart"] - meas["scStart"]) <= 8
    assert abs(back["roastedG"] - meas["roastedG"]) <= 0.2
    assert abs(back["abortG"] - meas["abortG"]) <= 0.3


def test_一項目だけでも較正できる():
    """全部測るのは大変なので、測れたものだけで動くこと。"""
    res = C.fit({"fcStart": 400.0})
    assert res["used"] == ["fcStart"]
    assert set(res["scale"]) == {"U0"}
    # 触っていない定数は上書きされない
    assert "K_PYRO" not in res["overrides"]


def test_測定値が無ければ何も変えない():
    res = C.fit({})
    assert res["scale"] == {} and res["overrides"] == {}


def test_おかしな測定値は端に張り付いて警告が出る():
    """1ハゼが10秒で来るはずがない。黙って極端な定数を採らないこと。"""
    res = C.fit({"fcStart": 10.0})
    assert res["notes"], "振り切ったのに警告が出ていません"
    # 定数名ではなく、何が起きているのかが分かる言葉で伝える
    assert "熱の入りやすさ" in res["notes"][0]


def test_較正した定数が推定に効く():
    base = E.estimate(P["roast"], P["fan"])
    cal = C.fit({"fcStart": 400.0, "roastedG": 38.4})["overrides"]
    tuned = E.estimate(P["roast"], P["fan"], cal=cal)
    assert tuned["end_bean_temp"] != base["end_bean_temp"]
    assert tuned["roast_index"] != base["roast_index"]


def test_上書きしなければ既定値と同じ():
    a = E.estimate(P["roast"], P["fan"])
    b = E.estimate(P["roast"], P["fan"], cal={})
    assert a["end_bean_temp"] == b["end_bean_temp"]
    assert a["roast_index"] == b["roast_index"]


def test_上書きできる定数は宣言と一致する():
    """energy側の受け口と、calibration側が作る上書き値がずれていないこと。"""
    ov = C.fit({"fcStart": 400.0, "fcEnd": 455.0, "scStart": 590.0,
                "roastedG": 38.4, "abortAt": 360.0, "abortG": 46.0})["overrides"]
    assert set(ov) <= set(E.CALIBRATABLE)


def test_較正の許容範囲では豆が空気を追い越さない():
    """較正で振れる範囲の端まで動かしても、熱風焙煎で有り得ない結果にならないこと。

    上昇中の空気より豆が熱くなるのは起こり得ない(下降中はその限りではない。
    冷めていく空気より豆のほうが熱いのは当たり前)。
    """
    for name, (lo, hi) in C.CAL_BOUNDS.items():
        for mul in (lo, hi):
            for moisture in (0.05, 0.10, 0.15):
                s = E.estimate(P["roast"], P["fan"], moisture=moisture,
                               cal=C._overrides({name: mul}))["series"]
                over = [b["bean"] - b["air"] for a, b in zip(s, s[1:])
                        if b["air"] >= a["air"] and b["bean"] > b["air"] + 0.5]
                assert not over, f"{name}×{mul} 含水率{moisture}: 最大{max(over):.1f}℃ 追い越した"


def test_仕様外の入力でも数字が壊れない():
    """カーブの温度が有り得ない値でも、NaNや無限大を返さないこと。

    NaNが出ると、豆温度・積算入熱・焙煎指数が画面上すべて壊れて、どこが原因か
    分からなくなる。読める数字で返れば「変だ」と気づける。
    """
    import math
    for roast in ([[0, 185], [600, 5000]], [[0, -50], [600, 240]],
                  [[0, 255], [600, 50]], [[0, 20], [600, 20]]):
        r = E.estimate(roast, P["fan"])
        assert r is not None
        for key in ("total_kcal", "roasted_g", "roast_index", "end_bean_temp", "end_gap"):
            assert math.isfinite(r[key]), f"{roast}: {key} が {r[key]}"
        assert all(math.isfinite(p["bean"]) for p in r["series"])


def test_seriesに残存重量が入っている():
    """途中で止めて量った重量と突き合わせるために必要。"""
    s = E.estimate(P["roast"], P["fan"])["series"]
    assert all("mass" in p for p in s)
    assert s[0]["mass"] == pytest.approx(E.BEAN_G / 1000.0)
    assert s[-1]["mass"] < s[0]["mass"]
    # 焙煎中に増えることはない
    assert all(b["mass"] <= a["mass"] + 1e-12 for a, b in zip(s, s[1:]))
