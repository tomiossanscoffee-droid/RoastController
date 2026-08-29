# -*- coding: utf-8 -*-
"""豆温度モデルの較正API(app/server.py)の検証。

このリポジトリの他のサーバーテストと同じく、HTTPを介さず関数を直接呼ぶ
(TestClientはhttpxを要求するため、依存を増やさない)。
保存先は環境変数で差し替えて、実際の calibration.json を触らないようにする。
"""
import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PATH_KEYS = ("CALIBRATION", "APP_SETTINGS", "FAVORITES", "CUSTOM_PROFILES",
             "ROAST_RECORDS", "BEAN_PURCHASES", "GUIDE_TEMPS",
             "PUSH_SUBSCRIPTIONS", "MOBILE_HOST", "LAST_SENT_PROFILE",
             "ROAST_COUNTS")


class FakeRequest:
    """server側が使うのは await request.json() だけなので、それだけ用意する。"""

    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    for name in PATH_KEYS:
        monkeypatch.setenv(f"ROAST_{name}_PATH", str(tmp_path / f"{name}.json"))
    import app.server as server
    importlib.reload(server)
    return server


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
    saved = json.loads((tmp_path / "CALIBRATION.json").read_text(encoding="utf-8"))
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
    assert (tmp_path / "CALIBRATION.json").exists()
    srv.clear_calibration()
    assert not (tmp_path / "CALIBRATION.json").exists()
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
    (tmp_path / "CALIBRATION.json").write_text("{壊れている", encoding="utf-8")
    assert fitted_only(srv) == {}
    assert body_of(srv.get_calibration())["scale"] == {}


def test_知らない定数名は無視する(srv, tmp_path):
    """保存ファイルを手で編集された場合に、変な値をモデルへ流し込まない。"""
    (tmp_path / "CALIBRATION.json").write_text(
        json.dumps({"overrides": {"U0": 0.004, "SOMETHING_ELSE": 1.0, "K_PYRO": "x"}}),
        encoding="utf-8")
    assert fitted_only(srv) == {"U0": 0.004}


def test_標高の補正が推定に効く(srv):
    """標高帯に応じて1ハゼ豆温度が変わり、1ハゼの時刻が動くこと。"""
    import roastlib.energy as E
    # 既定は傾き0 = どの標高でも基準値のまま
    assert srv._fc_bean_temp_for("2000m以上") == E.T_FC_BEAN
    assert srv._fc_bean_temp_for("1000m未満") == E.T_FC_BEAN

    asyncio.run(srv.set_app_settings(FakeRequest({"altitudeFcSlope": 4.0})))
    assert srv._fc_bean_temp_for("1500-2000m") == E.T_FC_BEAN      # 基準の帯は動かない
    assert srv._fc_bean_temp_for("1000m未満") < E.T_FC_BEAN         # 低地は低い
    assert srv._fc_bean_temp_for("2000m以上") > E.T_FC_BEAN         # 高地は高い
    assert srv._fc_bean_temp_for("") == E.T_FC_BEAN                # 未入力は補正しない
    assert srv._fc_bean_temp_for("指定なし") == E.T_FC_BEAN

    prof = srv.beancal.CALIBRATION_PROFILE
    low = E.estimate(prof["roast"], prof["fan"],
                     cal={"T_FC_BEAN": srv._fc_bean_temp_for("1000m未満")})
    high = E.estimate(prof["roast"], prof["fan"],
                      cal={"T_FC_BEAN": srv._fc_bean_temp_for("2000m以上")})
    # 爆ぜる温度が高いほど1ハゼは遅い
    assert high["crack_start"] > low["crack_start"]
    # 豆温度のカーブそのものは動かない(いつ爆ぜるかだけが変わる)
    assert high["series"][100]["bean"] == low["series"][100]["bean"]


def test_標高が後から入っても推定に反映される(srv):
    """豆情報に標高を後で入力する使い方があるので、キャッシュが残らないこと。"""
    asyncio.run(srv.set_app_settings(FakeRequest({"altitudeFcSlope": 6.0})))
    prof = srv.beancal.CALIBRATION_PROFILE
    before = srv._profile_estimate(prof["roast"], prof["fan"], 0.10, "")
    after = srv._profile_estimate(prof["roast"], prof["fan"], 0.10, "1000m未満")
    # 標高を入れたら別の答えになる(キャッシュのキーに入っている)
    assert after != before or after["end_bean_temp"] == before["end_bean_temp"]
    # 標高を消せば元に戻る
    again = srv._profile_estimate(prof["roast"], prof["fan"], 0.10, "")
    assert again == before


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


def test_標高の補正は範囲に収める(srv):
    for sent, want in ((-99, -10.0), (99, 10.0), (3.5, 3.5), ("abc", 0.0), (0, 0.0)):
        asyncio.run(srv.set_app_settings(FakeRequest({"altitudeFcSlope": sent})))
        assert srv._altitude_fc_slope() == want, f"{sent} → {want}"
