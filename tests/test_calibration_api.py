# -*- coding: utf-8 -*-
"""豆温度モデルの較正API(app/server.py)の検証。

このリポジトリの他のサーバーテストと同じく、HTTPを介さず関数を直接呼ぶ
(TestClientはhttpxを要求するため、依存を増やさない)。
保存先は conftest.py の srv フィクスチャがまとめて差し替える
(実際の calibration.json や焙煎記録を触らないため)。
"""
import asyncio
import json

import pytest

from conftest import sandbox_path


class FakeRequest:
    """server側が使うのは await request.json() だけなので、それだけ用意する。"""

    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def body_of(response):
    """JSONResponse から中身を取り出す。"""
    return json.loads(response.body.decode("utf-8"))


def put(srv, payload):
    return body_of(asyncio.run(srv.set_calibration(FakeRequest(payload))))


def test_校正用プロファイルを取得できる(srv):
    d = body_of(srv.get_calibration_profile())
    assert d["id"] == "calibration"
    assert len(d["roast"]) >= 2 and len(d["fan"]) >= 2
    # 実機で送信確認していないので、確認済みの印は付けない
    assert d["verified"] is False


def test_較正前は既定値で予想だけ返る(srv):
    d = body_of(srv.get_calibration())
    assert d["scale"] == {} and d["overrides"] == {}
    assert d["expected"]["fcStart"] > 0
    assert d["expected"]["scStart"] > d["expected"]["fcStart"]


def test_測定値を入れると較正され保存される(srv, tmp_path):
    # モデルの予想から少しずらした、到達できる範囲の測定値
    exp = body_of(srv.get_calibration())["expected"]
    meas = {"fcStart": exp["fcStart"] - 20, "fcEnd": exp["fcEnd"] - 10,
            "scStart": exp["scStart"] - 25, "greenG": 50,
            "roastedG": exp["roastedG"] - 0.8, "abortAt": 360, "abortG": 46.6}
    d = put(srv, meas)
    assert set(d["scale"]) == {"U0", "CRACK_SPREAD", "H_ENDO", "K_PYRO", "K_DRY"}
    saved = json.loads((sandbox_path(tmp_path, "ROAST_CALIBRATION_PATH")).read_text(encoding="utf-8"))
    assert saved["measurements"]["fcStart"] == meas["fcStart"]
    # 較正後の予想が実測に近づいていること
    after = body_of(srv.get_calibration())
    assert abs(after["fitted"]["fcStart"] - meas["fcStart"]) <= 6
    assert abs(after["fitted"]["roastedG"] - meas["roastedG"]) <= 0.2


def test_一項目だけでも受け付ける(srv):
    assert put(srv, {"fcStart": 420})["used"] == ["fcStart"]


def test_空なら拒否する(srv):
    res = asyncio.run(srv.set_calibration(FakeRequest({})))
    assert res.status_code == 400


def test_数値でない値は捨てる(srv):
    assert put(srv, {"fcStart": "abc", "roastedG": 38.4})["used"] == ["roastedG"]


def test_既定値に戻せる(srv, tmp_path):
    put(srv, {"fcStart": 420})
    assert (sandbox_path(tmp_path, "ROAST_CALIBRATION_PATH")).exists()
    srv.clear_calibration()
    assert not (sandbox_path(tmp_path, "ROAST_CALIBRATION_PATH")).exists()
    assert body_of(srv.get_calibration())["scale"] == {}


def test_較正が推定に効く(srv):
    """較正するとキャッシュも捨てられ、推定値が作り直されること。"""
    prof = srv.beancal.CALIBRATION_PROFILE
    before = srv._profile_estimate(prof["roast"], prof["fan"], 0.10)
    put(srv, {"fcStart": 420, "roastedG": 38.4})
    after = srv._profile_estimate(prof["roast"], prof["fan"], 0.10)
    assert after["roast_index"] != before["roast_index"]
    assert after["end_bean_temp"] != before["end_bean_temp"]


def fitted_only(srv):
    """較正で当てはめた分だけを取り出す。

    チャフ量は較正ではなく設定で決めるので、常に入っている。
    """
    ov = dict(srv._calibration_overrides())
    ov.pop("CHAFF_G", None)
    return ov


def test_壊れた保存ファイルでも既定値で動く(srv, tmp_path):
    (sandbox_path(tmp_path, "ROAST_CALIBRATION_PATH")).write_text("{壊れている", encoding="utf-8")
    assert fitted_only(srv) == {}
    assert body_of(srv.get_calibration())["scale"] == {}


def test_知らない定数名は無視する(srv, tmp_path):
    """保存ファイルを手で編集された場合に、変な値をモデルへ流し込まない。"""
    (sandbox_path(tmp_path, "ROAST_CALIBRATION_PATH")).write_text(
        json.dumps({"overrides": {"U0": 0.004, "SOMETHING_ELSE": 1.0, "K_PYRO": "x"}}),
        encoding="utf-8")
    assert fitted_only(srv) == {"U0": 0.004}


def test_標高の補正は焙煎ログから学ぶまで効かない(srv):
    """設定項目にはしていない。実測が集まるまでは、どの標高でも基準値のまま。"""
    import roastlib.energy as E
    assert srv._altitude_fc_slope() == 0.0
    for b in ("2000m以上", "1000m未満", "", "指定なし"):
        assert srv._fc_bean_temp_for(b) == E.T_FC_BEAN, b


def test_学んだ傾きが1ハゼ豆温度に効く(srv, tmp_path):
    """焙煎ログから学んだ値だけがモデルに入ること。"""
    import roastlib.energy as E
    (sandbox_path(tmp_path, "ROAST_CALIBRATION_PATH")).write_text(
        json.dumps({"learned": {"fcBeanTemp": 199.0, "altitudeSlope": 4.0}}),
        encoding="utf-8")
    assert srv._altitude_fc_slope() == 4.0
    assert srv._fc_bean_temp_for("1500-2000m") == 199.0     # 基準の帯は動かない
    assert srv._fc_bean_temp_for("1000m未満") < 199.0        # 低地は低い
    assert srv._fc_bean_temp_for("2000m以上") > 199.0        # 高地は高い
    assert srv._fc_bean_temp_for("") == 199.0               # 未入力は補正しない

    prof = srv.beancal.CALIBRATION_PROFILE
    low = E.estimate(prof["roast"], prof["fan"],
                     cal={"T_FC_BEAN": srv._fc_bean_temp_for("1000m未満")})
    high = E.estimate(prof["roast"], prof["fan"],
                      cal={"T_FC_BEAN": srv._fc_bean_temp_for("2000m以上")})
    # 爆ぜる温度が高いほど1ハゼは遅い
    assert high["crack_start"] > low["crack_start"]
    # 豆温度のカーブそのものは動かない(いつ爆ぜるかだけが変わる)
    assert high["series"][100]["bean"] == low["series"][100]["bean"]


def test_おかしな傾きは採らない(srv, tmp_path):
    """手で編集された場合に、変な値をモデルへ流し込まない。"""
    for v in (-99, 99, "abc", None):
        (sandbox_path(tmp_path, "ROAST_CALIBRATION_PATH")).write_text(
            json.dumps({"learned": {"altitudeSlope": v}}), encoding="utf-8")
        assert srv._altitude_fc_slope() == 0.0, v


def test_プロファイルの推定に標高は効かない(srv, tmp_path):
    """どの豆を焼くかは焙煎するまで決まらないので、プロファイル側では補正しない。"""
    (sandbox_path(tmp_path, "ROAST_CALIBRATION_PATH")).write_text(
        json.dumps({"learned": {"fcBeanTemp": 199.0, "altitudeSlope": 6.0}}),
        encoding="utf-8")
    import inspect
    # そもそも標高を受け取らない(渡す口が無い)
    assert "altitude" not in inspect.signature(srv._profile_estimate).parameters

    prof = srv.beancal.CALIBRATION_PROFILE
    est = srv._profile_estimate(prof["roast"], prof["fan"], 0.10)
    # 学んだ基準値(標高補正なし)での結果と一致すること
    import roastlib.energy as E
    want = E.estimate(prof["roast"], prof["fan"], moisture=0.10,
                      cal=dict(srv._calibration_overrides(), T_FC_BEAN=199.0))
    assert est["end_bean_temp"] == round(want["end_bean_temp"], 1)
    assert est["roast_index"] == round(want["roast_index"], 3)


def test_焙煎ログの標高は豆情報から取る(srv):
    """プロファイルの産地標高ではなく、実際に焼いた豆の標高を使う。

    産地標高の合わないプロファイルで焼くことがあるため。
    """
    beans = {"b1": {"altitude": "2000m以上"}}
    assert srv._record_altitude_bucket({"bean_purchase_id": "b1"}, beans) == "2000m以上"
    # 豆を紐づけていない記録・標高未入力の豆は、補正しない
    assert srv._record_altitude_bucket({}, beans) == ""
    assert srv._record_altitude_bucket({"bean_purchase_id": "b1"}, {"b1": {}}) == ""


def test_豆情報を保存すると推定のキャッシュを捨てる(srv):
    """後から標高を入力したとき、一覧の値が古いまま残らないこと。"""
    srv._PROFILE_ESTIMATE_CACHE[("dummy",)] = {"x": 1}
    srv._clear_profile_estimate_cache()
    assert srv._PROFILE_ESTIMATE_CACHE == {}


def test_チャフ量の設定がモデルに渡る(srv):
    """焙煎前後の重量差のうち、水分でも揮発性ガスでもない分。"""
    import roastlib.energy as E
    assert srv._calibration_overrides()["CHAFF_G"] == E.CHAFF_G
    asyncio.run(srv.set_app_settings(FakeRequest({"chaffG": 1.2})))
    assert srv._calibration_overrides()["CHAFF_G"] == 1.2
    prof = srv.beancal.CALIBRATION_PROFILE
    base = E.estimate(prof["roast"], prof["fan"], cal={"CHAFF_G": 0.0})
    with_chaff = E.estimate(prof["roast"], prof["fan"], cal={"CHAFF_G": 1.2})
    # チャフが多いほど焙煎後は軽くなる = 焙煎指数は上がる
    assert with_chaff["roasted_g"] < base["roasted_g"]
    assert with_chaff["roast_index"] > base["roast_index"]
    # 熱は使わないので、豆温度への影響はごく小さい
    assert abs(with_chaff["end_bean_temp"] - base["end_bean_temp"]) < 3.0


def test_チャフ量は範囲に収める(srv):
    for sent, want in ((-5, 0.0), (99, 2.0), (0.7, 0.7), ("abc", 0.5)):
        asyncio.run(srv.set_app_settings(FakeRequest({"chaffG": sent})))
        assert srv._chaff_g() == want, f"{sent} → {want}"
