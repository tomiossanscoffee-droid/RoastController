# ============================================================
# Roast Studio
# tests/test_calibration.py
# ------------------------------------------------------------
# 豆温度モデルの較正(roastlib/calibration.py)の検証。
#
#   venv/bin/python -m pytest tests/test_calibration.py
# ============================================================
import math
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
        # 途中で止めて量った重量。1点だけだと乾燥の速さがほとんど決まらない
        # (水の大半は1ハゼ前後まで豆の中に残るので、序盤の重量は乾燥の速さに
        #  あまり反応しない)。実際の使い方どおり、複数の時刻で量った形にする。
        "aborts": [{"t": t, "g": round(mass(t) * 1000.0, 1)}
                   for t in (180.0, 300.0, 390.0, 480.0)],
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

    3本とも、実在のプリセットが取る指数の範囲(1.08〜1.26)に収まっていること。

    ■ 浅煎り用が「浅煎り」の帯に入らないことについて(2026-09、未解決)
    浅煎り用の実測は50g→43.1gで、指数にすると1.160。使う人の基準(1ハゼが
    終わって少し先)では浅煎りだが、指数では中深煎りの帯に入る。ゆっくり焼くと
    同じ豆温度でも余分に抜けるためで、指数という1つの数字に速さの情報が
    入っていないことが原因。帯をずらしても直らないので、ここでは帯の判定を
    条件にしない。
    """
    deep = E.estimate(P["roast"], P["fan"])
    assert E.roast_index_level(deep["roast_index"]) == "深煎り", deep["roast_index"]
    assert 1.18 < deep["roast_index"] < 1.26
    for name, prof in (("浅煎り用", C.CALIBRATION_PROFILE_LIGHT),
                       ("展開長め", C.CALIBRATION_PROFILE_LONG)):
        r = E.estimate(prof["roast"], prof["fan"])
        assert 1.08 < r["roast_index"] < 1.26, f"{name} {r['roast_index']:.4f}"
    # 深煎り用がいちばん重く焼ける(3本の並び順は設計どおりであること)
    light = E.estimate(C.CALIBRATION_PROFILE_LIGHT["roast"],
                       C.CALIBRATION_PROFILE_LIGHT["fan"])
    long_ = E.estimate(C.CALIBRATION_PROFILE_LONG["roast"],
                       C.CALIBRATION_PROFILE_LONG["fan"])
    assert light["roasted_g"] > long_["roasted_g"] > deep["roasted_g"]


def test_校正用プロファイルは1ハゼ付近がゆるやか():
    """時刻を±5秒読み違えても、温度換算で±1.2℃に収まること。"""
    s = E.estimate(P["roast"], P["fan"])["series"]
    i = next(i for i, p in enumerate(s) if p["bean"] >= E.T_FC_BEAN)
    ror = (s[i + 15]["bean"] - s[i]["bean"]) / 15 * 60
    assert 0 < ror * 5 / 60 < 1.2, f"1ハゼ付近が急すぎます({ror:.1f}℃/分)"


# ---- 当てはめ ----

def test_途中重量は1点でも受け付ける():
    """古い形(abortAt/abortG の1組)のまま保存されている較正も動くこと。"""
    r = E.estimate(P["roast"], P["fan"])
    g = next(p["mass"] for p in r["series"] if p["t"] >= 360.0) * 1000.0
    res = C.fit({"fcStart": 442.0, "abortAt": 360.0, "abortG": round(g, 1)})
    assert "abort" in res["used"]
    assert "K_DRY" in res["scale"]


# 実機での参照測定(ケニア ニエリ・標高1800m・生豆50g)。
# 深煎り用と浅煎り用を、同じ豆で1本ずつ焼いて測ったもの。440秒までは同じカーブなので、
# 2つの違いは「1ハゼの前後で何が起きたか」だけになる。
# モデルを直したときに、この実測から離れていないかを見るための基準。
REFERENCE = {
    "deep": {
        "fcStart": 442.0, "fcEnd": 508.0, "scStart": 622.0,
        "greenG": 50.0, "roastedG": 40.9,
        "aborts": [{"t": 180.0, "g": 49.2}, {"t": 240.0, "g": 49.0},
                   {"t": 300.0, "g": 49.0}, {"t": 390.0, "g": 47.9}],
    },
    "light": {
        "fcStart": 432.0, "fcEnd": 542.0,
        "greenG": 50.0, "roastedG": 43.1,
    },
}
REFERENCE_COLOR_CHANGE = 240.0   # カラーチェンジの時刻(豆140〜150℃が目安)
# 豆ごとに爆ぜる時刻は違う。同じカーブの440秒までで、2回の1ハゼ開始は442秒と432秒
# (10秒差 = 豆温度で1.4℃)。この程度のばらつきは実測の下限で、モデルの精度ではない。
REFERENCE_SCATTER_S = 12.0


def _reference_fit():
    res = C.fit({"roasts": REFERENCE}, moisture=0.11)
    cal = dict(res["overrides"]); cal["CHAFF_G"] = 0.2
    out = {}
    for kind, prof in (("deep", P), ("light", C.CALIBRATION_PROFILE_LIGHT)):
        out[kind] = E.estimate(prof["roast"], prof["fan"], moisture=0.11, cal=cal)
    return res, out


def test_実機の測定値を再現できる():
    """実際に焼いて測った値に、較正後のモデルが合うこと。

    ハゼの時刻・焙煎後の重量だけでなく、途中で量った重量とカラーチェンジの
    時刻まで同時に合うかを見る。ここが崩れたらモデルを直した意味が無い。
    """
    res, got = _reference_fit()
    assert not res["notes"], f"範囲の端に張り付きました: {res['notes']}"
    for kind, m in REFERENCE.items():
        r = got[kind]
        assert abs(r["crack_start"] - m["fcStart"]) <= REFERENCE_SCATTER_S, kind
        assert abs(r["crack_end"] - m["fcEnd"]) <= 15, kind
        if m.get("scStart"):
            sc = next((p["t"] for p in r["series"] if p["bean"] >= C.T_SC_BEAN), None)
            assert sc is not None and abs(sc - m["scStart"]) <= 10, kind
    s = got["deep"]["series"]
    at = lambda t: s[min(int(t), len(s) - 1)]
    # 途中で量った重量(はかりは0.1g刻み。0.7g以内なら実用上合っている)
    for a in REFERENCE["deep"]["aborts"]:
        g = at(a["t"])["mass"] * 1000.0
        assert abs(g - a["g"]) <= 0.7, f"{a['t']}秒: モデル{g:.2f}g 実測{a['g']}g"
    # カラーチェンジのときの豆温度が、一般に言われる目安の範囲に入ること
    cc = at(REFERENCE_COLOR_CHANGE)["bean"]
    assert 138.0 <= cc <= 152.0, f"カラーチェンジ時の豆温度 {cc:.1f}℃"


def test_2本の焙煎で同じ豆温度で爆ぜる():
    """モデルの一番の主張は「豆は決まった温度で爆ぜる」。

    440秒までは同じカーブで、その後の上がり方が違う2本を焼いた。1ハゼの開始も
    終了も、実測の時刻に対応する豆温度が2本で揃っていれば、その主張が実測で
    裏づけられたことになる(時刻は66秒と110秒でまるで違う)。
    """
    res, got = _reference_fit()
    cal = dict(res["overrides"])
    starts, ends = [], []
    for kind, m in REFERENCE.items():
        s = got[kind]["series"]
        at = lambda t: s[min(int(t), len(s) - 1)]["bean"]
        starts.append(at(m["fcStart"]))
        ends.append(at(m["fcEnd"]))
    assert abs(starts[0] - starts[1]) <= 3.0, f"1ハゼ開始の豆温度が揃わない: {starts}"
    assert abs(ends[0] - ends[1]) <= 3.0, f"1ハゼ終了の豆温度が揃わない: {ends}"
    # 開始はモデルの1ハゼ豆温度そのもの、終了はその上
    t_fc = cal.get("T_FC_BEAN", E.T_FC_BEAN)
    assert all(abs(x - t_fc) <= 3.0 for x in starts), f"{starts} が {t_fc}℃ から離れている"
    assert all(x > t_fc for x in ends)


def test_浅煎りの重量は実測より重く出る():
    """既知の限界。直したら、この期待値を更新すること。

    浅煎り用(豆208℃で停止)の焙煎後の重量を、モデルは実測より重く見積もる。
    実測は50→43.1g(13.8%減)、モデルは12%前後の減りしか出ない。
    深煎り用(豆233℃)では合っているので、焙煎度が浅いほどずれが大きい。

    ■ 分かっていること(2026-08の検証)
    ・足りない分は水ではない。6分30秒から停止までの170秒に豆へ入る熱は8.0kJで、
      残りの水を全部飛ばすには11.0kJ要る。水で説明できるのは0.2g程度。
    ・残りは乾物側だが、乾物を1.3g余分に飛ばすには吸熱が2.1kJ要り、
      その170秒に入る熱の26%にあたる。熱の入りやすさを上げれば賄えるが、
      上げると豆が上昇中の吸入温度を追い越すため1.45倍で頭打ちになる。
    ・試して駄目だった打ち手: 水の抜ける温度域をずらす/狭める(豆温度が止まる)、
      活性化エネルギーを下げる、揮発分の上限を設ける、1ハゼに発熱を置く、
      含水率とチャフの調整(半分程度しか埋まらない)。
    ・足りないのは定数ではなく熱そのもの。焙煎の発熱(2ハゼ以降)がモデルに無い。
      それを測るには、2ハゼで吸入温度を下げて保つプロファイルが要る。
    """
    res, got = _reference_fit()
    d = got["deep"]["roasted_g"] - REFERENCE["deep"]["roastedG"]
    l = got["light"]["roasted_g"] - REFERENCE["light"]["roastedG"]
    assert abs(d) <= 0.5, f"深煎り用は合っているはず: {d:+.2f}g"
    assert 0.3 <= l <= 1.5, f"浅煎り用のずれが想定の範囲を出ました: {l:+.2f}g"
    assert l > d, "浅いほどずれが大きい、という傾向が崩れています"


# ■ 削除した試験用プロファイルの実測(2026-09に profile を削除、記録は残す)
# 発熱の検証(2026-08-31、ケニア ニエリ・生豆50g): 2ハゼ618秒 / 音は688秒まで。
#   発熱を持たないモデルの予測は 2ハゼ621秒・音677秒で、実測とよく合う。
#   発熱を入れた形はAICcでも交差検証でも棄却された。
# 昇温を遅くした検証(2026-09-01、同じ豆): 1ハゼ音は全体で3回のみ
#   (12:20/12:50/14:13)、焙煎後43.1g。9.3分で焼いた浅煎り用と完全に同じ重量で、
#   「重量は時間ではなく到達温度で決まる」ことがはっきりした。この実測で
#   U0と乾燥の速さを当てはめ直してある(roastlib/energy.py の U0 の但し書き)。
SLOW_RAMP_RESULT = {"fcStart": 740.0, "roastedG": 43.1}


def test_遅い昇温の実測を再現できる():
    """削除したプロファイルの実測を、いまの既定値が再現できること。

    このプロファイルはもう送れないが、実測は既定値の根拠になっている。
    モデルを変えてここが合わなくなったら、その変更を疑うこと。
    """
    prof = {"roast": [[0, 185], [60, 95], [300, 150], [480, 180],
                      [720, 206], [900, 209]],
            "fan": [[0, 50], [1, 80], [300, 70], [900, 58]]}
    r = E.estimate(prof["roast"], prof["fan"], moisture=0.11)
    assert r["crack_start"] is not None
    assert abs(r["crack_start"] - SLOW_RAMP_RESULT["fcStart"]) <= 40, \
        f"1ハゼ {r['crack_start']:.0f}秒(実測 {SLOW_RAMP_RESULT['fcStart']:.0f}秒)"
    # 1ハゼ音が3回しか鳴らなかった = 焙煎終了までに1ハゼは終わっていない
    assert r["crack_end"] is None, f"1ハゼが{r['crack_end']:.0f}秒で終わってしまう"
    assert abs(r["roasted_g"] - SLOW_RAMP_RESULT["roastedG"]) <= 1.0, \
        f"焙煎後 {r['roasted_g']:.2f}g(実測 {SLOW_RAMP_RESULT['roastedG']}g)"


def test_途中重量が1点だけだと当てはめが暴れる():
    """1点に合わせ込むと乾燥の速さが極端になり、他の時刻が外れる。

    実際、3分の1点だけで当てはめたら乾燥が6.1倍まで振れ、終盤の吸熱も
    範囲の端に張り付いた。複数点なら常識的な範囲に収まる。
    """
    deep = REFERENCE["deep"]
    single = {k: v for k, v in deep.items() if k != "aborts"}
    single["abortAt"] = 180.0
    single["abortG"] = 49.2
    one = C.fit(single, moisture=0.11)
    many = C.fit({"roasts": {"deep": deep}}, moisture=0.11)
    assert abs(math.log(many["scale"]["K_DRY"])) < abs(math.log(one["scale"]["K_DRY"])), \
        f"複数点 {many['scale']['K_DRY']:.2f} / 1点 {one['scale']['K_DRY']:.2f}"
    assert not many["notes"], f"複数点でも端に張り付きました: {many['notes']}"


def test_ずらした定数を当てはめで戻せる():
    """当てはめる4つの定数を、ずらした状態から元に戻せること。

    H_ENDO は当てはめの対象から外した(2026-09)。2ハゼを乾物の分解量で判定する
    ように直した結果、2ハゼは焙煎後の重量と同じものを測ることになり、H_ENDO を
    決める手がかりが無くなったため。詳しくは roastlib/calibration.py 参照。
    """
    true = {"U0": 1.15, "CRACK_SPREAD": 1.40, "K_PYRO": 1.30, "K_DRY": 0.75}
    cal = {
        "U0": E.U0 * true["U0"], "CRACK_SPREAD": E.CRACK_SPREAD * true["CRACK_SPREAD"],
        "K_PYRO": E.K_PYRO * true["K_PYRO"],
        "K_SURFACE": E.K_SURFACE * true["K_DRY"], "K_INNER": E.K_INNER * true["K_DRY"],
    }
    got = C.fit(observe(cal))["scale"]
    assert "H_ENDO" not in got, "H_ENDOは当てはめないはず"
    for k, want in true.items():
        # 乾燥の速さ(K_DRY)だけは緩めに見る。水の大半が1ハゼ前後まで豆の中に
        # 残るようになったので、途中の重量は乾燥の速さにあまり反応せず、元の値を
        # ぴたりと当てるだけの手がかりが残っていない。
        rel = 0.20 if k == "K_DRY" else 0.10
        assert got[k] == pytest.approx(want, rel=rel), f"{k}: 期待{want} 実際{got[k]}"


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
    for got, want in zip(back["aborts"], meas["aborts"]):
        assert got["t"] == want["t"]
        assert abs(got["g"] - want["g"]) <= 0.3, f"{want['t']}秒の重量"


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


# ------------------------------------------------------------
# 焙煎ログからの学習(roastlib/learning.py)
# ------------------------------------------------------------
def _log(fc=440.0, sc=620.0, green=50.0, roasted=41.0, inferred=False, curve=None):
    """学習に使える形の焙煎記録を作る。"""
    prof = C.CALIBRATION_PROFILE
    if curve is None:
        r = E.estimate(prof["roast"], prof["fan"], moisture=0.11)
        curve = [[p["t"], p["air"]] for p in r["series"]]
    return {"roast_curve": curve, "fan_curve": [list(x) for x in prof["fan"]],
            "fc_time": fc, "fc_time_inferred": inferred, "sc_time": sc,
            "green_g": green, "roasted_g": roasted, "bean_purchase_id": "b1"}


def test_学習に使える記録の条件():
    """カーブがあり、重量か実測の1ハゼのどちらかがあれば使う。

    あるものだけを学び、無いものは学ばない。重量だけの記録は質量の補正に、
    1ハゼだけの記録は1ハゼ豆温度の補正に効く。
    """
    import roastlib.learning as L
    assert L.is_learnable(_log())
    # 重量が無くても、実測の1ハゼがあれば使う
    bad = _log(); bad["roasted_g"] = None
    assert L.is_learnable(bad)
    # 1ハゼが無くても、重量があれば使う
    assert L.is_learnable(_log(inferred=True))
    # どちらも無ければ使わない
    none = _log(inferred=True); none["green_g"] = None; none["roasted_g"] = None
    assert not L.is_learnable(none)
    # 焙煎後が生豆より重いのはあり得ない。1ハゼも自動入力なら使えない
    bad = _log(roasted=51.0, inferred=True)
    assert not L.is_learnable(bad)
    # カーブが無ければ何も測れない
    nocurve = _log(); nocurve["roast_curve"] = []
    assert not L.is_learnable(nocurve)


def test_あるものだけを学ぶ():
    """記録に含まれる項目に応じて、学べるものだけを返すこと。"""
    import roastlib.learning as L
    full = L.observe(_log(), moisture=0.11)
    assert all(full[k] is not None for k in ("fcBeanTemp", "scDryFrac", "indexRatio"))
    only_w = _log(inferred=True)          # 1ハゼは自動入力 = 信用しない
    o = L.observe(only_w, moisture=0.11)
    assert o["indexRatio"] is not None and o["fcBeanTemp"] is None
    only_c = _log(); only_c["green_g"] = None; only_c["roasted_g"] = None
    o = L.observe(only_c, moisture=0.11)
    assert o["fcBeanTemp"] is not None and o["indexRatio"] is None


def test_2ハゼ未記録でも1ハゼと重量は学習に使う():
    import roastlib.learning as L
    o = L.observe(_log(sc=None), moisture=0.11)
    assert o is not None
    assert o["fcBeanTemp"] is not None
    assert o["indexRatio"] is not None
    assert o["scDryFrac"] is None


def test_品種が複数並んでいても1件ずつ数える():
    import roastlib.learning as L
    assert L.split_axis("ブルボン、ティピカ") == ["ブルボン", "ティピカ"]
    assert L.split_axis("SL28 / SL34") == ["SL28", "SL34"]
    assert L.split_axis("ゲイシャ") == ["ゲイシャ"]
    assert L.split_axis("") == ["未分類"]
    assert L.split_axis(None) == ["未分類"]


def test_未知の項目は全体の値から始まり件数で育つ():
    """新しい生産国や品種が増えても壊れず、件数が増えるほどその項目自身へ寄る。"""
    import roastlib.learning as L
    base = 200.0
    assert L._shrunk([], base) == base                    # 0件は全体そのもの
    one = L._shrunk([205.0], base)
    many = L._shrunk([205.0] * 30, base)
    assert base < one < many < 205.0                      # 件数とともに近づく
    assert abs(one - 201.25) < 0.01                       # n/(n+3)の重み


def test_学習は初期データから始まる():
    """ログが1件も無くても、較正5本を初期値として補正が出る。"""
    import roastlib.learning as L
    r = L.learn([], moisture=0.11, axis_of=lambda ax, rec: "")
    assert r["n"] == len(L.SEED_ROASTS) and r["logs"] == 0
    # 初期データから出る値が、モデルの既定値とかけ離れていないこと
    assert abs(r["global"]["fcBeanTemp"] - E.T_FC_BEAN) < 2.0
    assert abs(r["global"]["scDryFrac"] - E.SC_DRY_FRAC) < 0.005


def test_重量が無い記録も構造の見直しに使える():
    """is_learnable が「重量かハゼのどちらか」で通すので、変換も同じでなければ
    実際のログで例外になる(2026-09にそれで落ちた)。"""
    import roastlib.learning as L
    only_crack = _log(); only_crack["green_g"] = None; only_crack["roasted_g"] = None
    r = L.record_to_roast(only_crack, 0.11)
    assert r is not None
    assert "fcStart" in r["meas"] and "roastedG" not in r["meas"]
    only_weight = _log(inferred=True)      # 1ハゼは自動入力 = 使わない
    r = L.record_to_roast(only_weight, 0.11)
    assert r is not None
    assert "roastedG" in r["meas"] and "fcStart" not in r["meas"]
    # どちらも無ければ変換もしない
    none = _log(inferred=True); none["green_g"] = None; none["roasted_g"] = None
    assert L.record_to_roast(none, 0.11) is None


def test_実際の焙煎記録で構造の見直しの前準備が通る():
    """手元の記録をそのまま流して、例外が出ないこと。"""
    import json, pathlib
    import roastlib.learning as L
    p = pathlib.Path(__file__).resolve().parent.parent / "roast_records.json"
    if not p.exists():
        return
    recs = json.loads(p.read_text(encoding="utf-8"))
    n = 0
    for rec in recs.values():
        r = L.record_to_roast(rec, 0.11)
        if r is not None:
            assert r["meas"], "観測が空の変換結果を返しています"
            n += 1
    assert n >= 0


def test_標高も生産国や品種と同じ軸として学ぶ():
    """標高は4つの補正軸の1つ。件数が増えるほどその標高帯自身の値へ寄る。

    以前は energy.py の ALTITUDE_FC_SLOPE(線形の傾き1本)で表していたが、
    傾きを配る経路が無くなって常に0のまま動いていた。いまは生産国・品種・
    精製方法と同じ縮小推定で扱う。
    """
    import roastlib.learning as L
    assert L.AXES == ("altitude", "country", "variety", "process")
    axis_of = lambda ax, rec: rec.get(ax, "")
    recs = [dict(_log(), altitude="2000m以上") for _ in range(6)]
    st = L.learn(recs, 0.11, axis_of=axis_of)
    hi = st["axes"]["altitude"]["2000m以上"]
    assert hi["n"] == 6
    # 初期データ(SEED_ROASTS)は1500-2000m帯なので、別の帯として立つ
    assert "1500-2000m" in st["axes"]["altitude"]
    # 標高を入れていない記録は「未分類」に落ちるだけで、捨てられない
    st2 = L.learn([dict(_log()) for _ in range(3)], 0.11, axis_of=axis_of)
    assert st2["axes"]["altitude"]["未分類"]["n"] == 3
    # 未知の標高帯は「補正なし」ではなく、全体の値から始まって件数で育つ
    assert L._shrunk([], 200.0) == 200.0
    one, many = L._shrunk([205.0], 200.0), L._shrunk([205.0] * 30, 200.0)
    assert 200.0 < one < many < 205.0
