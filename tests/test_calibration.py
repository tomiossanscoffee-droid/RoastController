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

    深煎り用は深煎りの帯、浅煎り用は浅煎りの帯に収まっていること
    (実在の深煎りプリセットの推定指数は1.193〜1.217、浅煎りは1.099〜1.118)。
    """
    deep = E.estimate(P["roast"], P["fan"])
    assert E.roast_index_level(deep["roast_index"]) == "深煎り", deep["roast_index"]
    assert 1.18 < deep["roast_index"] < 1.26
    light = E.estimate(C.CALIBRATION_PROFILE_LIGHT["roast"],
                       C.CALIBRATION_PROFILE_LIGHT["fan"])
    assert E.roast_index_level(light["roast_index"]) == "浅煎り", light["roast_index"]
    assert 1.08 < light["roast_index"] < 1.14


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


# 発熱の検証用プロファイルを実機で焼いた結果(2026-08-31、ケニア ニエリ・生豆50g)。
#   2ハゼ開始 10:18(618秒) / 2ハゼの音が消えた 11:28(688秒)
# 実際の吸入温度カーブ(機械は落とすのに約10秒遅れた)で計算すると:
#   発熱なし        2ハゼ 621秒(+3) / 音が消える 677秒(-11)
#   約 70 kJ/kg     2ハゼ 614秒(-4) / 音が消える 685秒( -3)
#   約120 kJ/kg     2ハゼ 611秒(-7) / 音が消える 691秒( +3)
#   約300 kJ/kg     2ハゼ 606秒(-12)/ 音が消える 721秒(+33)  ← 明らかに合わない
# 豆ごとのばらつき(1ハゼ開始で10秒)と、音の裾(しきい値を跨いだ後も鳴り続ける)を
# 考えると、この焙煎機の発熱は<b>0〜120 kJ/kg</b>で、それ以上は否定される。
# ドラム焙煎で言われる「2ハゼで火を絞らないと進んでしまう」ほどの発熱は、
# 50gの熱風焙煎機では観測されなかった。豆が空気と強く結びついていて、
# 出た熱がすぐ運び去られるためと考えられる。
# → いまのモデル(分解は常に吸熱)のままで、この焙煎機には十分合う。
EXOTHERM_PROBE_RESULT = {"scStart": 618.0, "scSoundEnd": 688.0}


def test_発熱は小さい():
    """発熱を入れなくても、2ハゼの実測に合うこと。

    検証用プロファイルの実測(上のEXOTHERM_PROBE_RESULT)に対して、発熱を持たない
    いまのモデルが2ハゼの開始時刻を当てられることを確かめる。ここが外れるように
    なったら、発熱を入れるかどうかを検討し直すこと。
    """
    prof = C.EXOTHERM_PROBE_PROFILE
    r = E.estimate(prof["roast"], prof["fan"])
    assert r is not None
    s = r["series"]
    sc = next((p["t"] for p in s if p["bean"] >= C.T_SC_BEAN), None)
    assert sc is not None, "検証用プロファイルが2ハゼに届きません"
    # 設計カーブでの計算なので、実機の追従遅れ(約10秒)ぶんは緩く見る
    assert abs(sc - EXOTHERM_PROBE_RESULT["scStart"]) <= 20, f"2ハゼ {sc:.0f}秒"
    # 落としたあと、音が消えるまでの長さが実測とかけ離れていないこと
    last = max((p["t"] for p in s if p["bean"] >= C.T_SC_BEAN), default=None)
    got = last - sc
    want = EXOTHERM_PROBE_RESULT["scSoundEnd"] - EXOTHERM_PROBE_RESULT["scStart"]
    assert abs(got - want) <= 30, f"2ハゼが続いた時間 モデル{got:.0f}秒 実測{want:.0f}秒"


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
    true = {"U0": 1.15, "CRACK_SPREAD": 1.40, "H_ENDO": 0.85,
            "K_PYRO": 1.30, "K_DRY": 0.75}
    cal = {
        "U0": E.U0 * true["U0"], "CRACK_SPREAD": E.CRACK_SPREAD * true["CRACK_SPREAD"],
        "H_ENDO": E.H_ENDO * true["H_ENDO"], "K_PYRO": E.K_PYRO * true["K_PYRO"],
        "K_SURFACE": E.K_SURFACE * true["K_DRY"], "K_INNER": E.K_INNER * true["K_DRY"],
    }
    got = C.fit(observe(cal))["scale"]
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


# ---- 焙煎ログから学ぶ ----

def _fake_record(fc_t, sc_t=None, inferred=False, curve=None):
    r = E.estimate(P["roast"], P["fan"])
    return {
        "roast_curve": curve if curve is not None else [[p["t"], p["air"]] for p in r["series"]],
        "fan_curve": P["fan"],
        "fc_time": fc_t, "fc_time_inferred": inferred, "sc_time": sc_t,
    }


def test_1ハゼ確認を押した記録だけを使う():
    """ガイド温度からの推定値で較正すると、モデルの入力で自分を較正することになる。"""
    res = C.learn_from_logs([
        _fake_record(400.0),                    # 実測 → 使う
        _fake_record(400.0, inferred=True),     # 推定 → 使わない
        _fake_record(None),                     # 未記録 → 使わない
        _fake_record(400.0, curve=[]),          # カーブなし → 使わない
    ])
    assert res["fc"]["n"] == 1
    assert res["skipped"]["推定値のみ"] == 1
    assert res["skipped"]["1ハゼ未記録"] == 1
    assert res["skipped"]["カーブなし"] == 1


def test_2ハゼも実測から集められる():
    res = C.learn_from_logs([_fake_record(400.0, 560.0), _fake_record(410.0, 570.0)])
    assert res["fc"]["n"] == 2 and res["sc"]["n"] == 2
    # 2ハゼは1ハゼより高い温度になる
    assert res["sc"]["median"] > res["fc"]["median"]


def test_2ハゼが1ハゼより前なら使わない():
    res = C.learn_from_logs([_fake_record(400.0, 300.0)])
    assert res["fc"]["n"] == 1 and res["sc"]["n"] == 0


def test_記録が増えるほどばらつきが出る():
    """1件だけならばらつき0。複数集めて初めて確からしさが見える。"""
    one = C.learn_from_logs([_fake_record(400.0)])
    assert one["fc"]["n"] == 1 and one["fc"]["sd"] == 0.0
    many = C.learn_from_logs([_fake_record(t) for t in (380.0, 400.0, 420.0)])
    assert many["fc"]["n"] == 3 and many["fc"]["sd"] > 0
    assert many["fc"]["min"] < many["fc"]["median"] < many["fc"]["max"]


def test_範囲外の時刻は使わない():
    res = C.learn_from_logs([_fake_record(99999.0), _fake_record(-5.0)])
    assert res["fc"]["n"] == 0
    assert res["skipped"]["時刻が範囲外"] >= 1


def test_記録が無ければ空で返る():
    res = C.learn_from_logs([])
    assert res["fc"]["n"] == 0 and res["fc"]["median"] is None
    assert res["sc"]["n"] == 0
    assert res["altitude"]["n"] == 0 and res["altitude"]["slope"] is None


# ---- 標高による差を学ぶ ----
# 標高は「焙煎した豆」の性質なので、豆情報(購入した豆)の標高だけを見る。
# プロファイル側の標高は使わない(産地の合わないカーブで焼くことがある)。

def _alt_record(fc_t, bucket):
    r = _fake_record(fc_t)
    r["_alt"] = bucket
    return r


def _learn_alt(records):
    return C.learn_from_logs(records, altitude_of=lambda r: r.get("_alt", ""))


def test_標高は豆情報から渡された分だけ数える():
    res = _learn_alt([_alt_record(400.0, "1000m未満"), _alt_record(410.0, "")])
    assert res["altitude"]["n"] == 1          # 標高未入力の豆は数に入らない
    assert res["fc"]["n"] == 2                # 1ハゼの集計そのものには入る


def test_同じ標高帯だけなら傾きは出さない():
    """傾きなのか基準値のずれなのか区別が付かないため。"""
    res = _learn_alt([_alt_record(t, "1500-2000m") for t in (380.0, 400.0, 420.0, 440.0)])
    assert res["altitude"]["n"] == 4 and res["altitude"]["buckets"] == 1
    assert res["altitude"]["slope"] is None


def test_件数が足りなければ傾きは出さない():
    res = _learn_alt([_alt_record(400.0, "1000m未満"), _alt_record(420.0, "2000m以上")])
    assert res["altitude"]["buckets"] == 2 and res["altitude"]["slope"] is None


def test_標高が違う記録がそろうと傾きが出る():
    """高地の豆ほど高い温度で爆ぜていれば、正の傾きになる。"""
    res = _learn_alt([
        _alt_record(390.0, "1000m未満"), _alt_record(395.0, "1000m未満"),
        _alt_record(430.0, "2000m以上"), _alt_record(435.0, "2000m以上"),
    ])
    a = res["altitude"]
    assert a["buckets"] == 2 and a["n"] == 4
    assert a["slope"] > 0
    # 基準標高(1750m)での値は、低地と高地の実測の間に収まる
    assert res["fc"]["min"] < a["base"] < res["fc"]["max"]


def test_逆向きでも学べる():
    res = _learn_alt([
        _alt_record(430.0, "1000m未満"), _alt_record(435.0, "1000m未満"),
        _alt_record(390.0, "2000m以上"), _alt_record(395.0, "2000m以上"),
    ])
    assert res["altitude"]["slope"] < 0


def test_傾きは行き過ぎないよう頭を押さえる():
    """記録が偏っていても、モデルが壊れる値までは動かさない。"""
    res = _learn_alt([
        _alt_record(300.0, "1000m未満"), _alt_record(300.0, "1000m未満"),
        _alt_record(600.0, "1000-1500m"), _alt_record(600.0, "1000-1500m"),
    ])
    assert -10.0 <= res["altitude"]["slope"] <= 10.0


def test_標高を渡さなければ学ばない():
    """豆情報を繋いでいない使い方でも、今までどおり1ハゼだけ学べること。"""
    res = C.learn_from_logs([_fake_record(t) for t in (390.0, 400.0, 410.0, 420.0)])
    assert res["fc"]["n"] == 4
    assert res["altitude"]["n"] == 0 and res["altitude"]["slope"] is None
