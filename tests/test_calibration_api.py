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


def test_較正前はアプリの初期値が入っている(srv):
    """空の状態からは始めない。実機で測った較正を初期値として持つ。"""
    import roastlib.calibration as beancal
    d = body_of(srv.get_calibration())
    assert d["overrides"]["U0"] == beancal.DEFAULT_CALIBRATION["overrides"]["U0"]
    assert d["scale"]["U0"] == beancal.DEFAULT_CALIBRATION["scale"]["U0"]
    # 予想は較正用プロファイルごとに返る(深煎り用・浅煎り用・展開長め)
    assert set(d["expected"]) == {"deep", "light", "long"}
    # 展開長めは2ハゼまで届く(3本目の狙いは1ハゼを遅らせつつ最後まで焼くこと)
    assert d["expected"]["long"]["scStart"] > d["expected"]["long"]["fcStart"]
    # 1ハゼは既存2本より遅い
    assert d["expected"]["long"]["fcStart"] > d["expected"]["deep"]["fcStart"]
    assert d["expected"]["deep"]["fcStart"] > 0
    assert d["expected"]["deep"]["scStart"] > d["expected"]["deep"]["fcStart"]
    # 浅煎り用は2ハゼまで届かない
    assert d["expected"]["light"]["scStart"] is None
    # 浅煎り用のほうが軽く焼ける = 焙煎後は重い
    assert d["expected"]["light"]["roastedG"] > d["expected"]["deep"]["roastedG"]


def test_測定値を入れると較正され保存される(srv, tmp_path):
    # モデルの予想から少しずらした、到達できる範囲の測定値
    exp = body_of(srv.get_calibration())["expected"]["deep"]
    meas = {"fcStart": exp["fcStart"] - 20, "fcEnd": exp["fcEnd"] - 10,
            "scStart": exp["scStart"] - 25, "greenG": 50,
            "roastedG": exp["roastedG"] - 0.8, "abortAt": 360, "abortG": 46.6}
    d = put(srv, meas)
    # H_ENDOは当てはめない(2ハゼが焙煎後の重量と同じものを測るため。
    # roastlib/calibration.py 参照)
    assert set(d["scale"]) == {"U0", "CRACK_SPREAD", "K_PYRO", "K_DRY"}
    saved = json.loads((sandbox_path(tmp_path, "ROAST_CALIBRATION_PATH")).read_text(encoding="utf-8"))
    assert saved["measurements"]["fcStart"] == meas["fcStart"]
    # 較正後の予想が実測に近づいていること
    after = body_of(srv.get_calibration())["fitted"]["deep"]
    assert abs(after["fcStart"] - meas["fcStart"]) <= 6
    assert abs(after["roastedG"] - meas["roastedG"]) <= 0.2


def test_プロファイルごとに測定値を受け取れる(srv, tmp_path):
    """浅煎り用と深煎り用の両方を焼いた場合。"""
    exp = body_of(srv.get_calibration())["expected"]
    body = {"roasts": {
        "deep": {"fcStart": exp["deep"]["fcStart"], "greenG": 50,
                 "roastedG": exp["deep"]["roastedG"] - 0.5},
        "light": {"fcStart": exp["light"]["fcStart"], "greenG": 50,
                  "roastedG": exp["light"]["roastedG"] - 0.5},
    }}
    d = body_of(asyncio.run(srv.set_calibration(FakeRequest(body))))
    saved = json.loads((sandbox_path(tmp_path, "ROAST_CALIBRATION_PATH")).read_text(encoding="utf-8"))
    assert set(saved["measurements"]["roasts"]) == {"deep", "light"}
    # 両方の焙煎後の重量に近づいていること
    after = body_of(srv.get_calibration())["fitted"]
    for kind in ("deep", "light"):
        want = body["roasts"][kind]["roastedG"]
        assert abs(after[kind]["roastedG"] - want) <= 0.6, kind


def test_展開長めの校正用プロファイルを送れる(srv):
    """1ハゼを遅らせて展開を長くとる形。較正の当てはめにも使う3本目。"""
    d = body_of(srv.get_calibration_profile("long"))
    assert d["kind"] == "long"
    assert len(d["uuid"]) == 16 and d["uuid"].isdigit()
    roast = d["roast"]
    # 最後まで上げ続ける形(2ハゼまで届かせる)
    assert roast[-1][1] == max(p[1] for p in roast)
    # THE ROAST EXPERT の制限内
    assert max(p[1] for p in roast) <= 255
    assert len(roast) <= 20 and len(d["fan"]) <= 10
    assert roast[-1][0] <= 900


def test_浅煎り用は深煎り用と途中まで同じ形(srv):
    """2つの焙煎後の重量の差が、そのまま1ハゼ前後で抜けた水になるようにしてある。"""
    deep = body_of(srv.get_calibration_profile("deep"))
    light = body_of(srv.get_calibration_profile("light"))
    assert deep["roast"][:4] == light["roast"][:4]
    assert light["roast"][-1][0] < deep["roast"][-1][0]
    # どちらも焙煎機に送れる形であること
    for p in (deep, light):
        assert len(p["uuid"]) == 16 and p["uuid"].isdigit()
        assert p["cooldown"][0] > p["roast"][-1][0]


def test_一項目だけでも受け付ける(srv):
    assert put(srv, {"fcStart": 420})["used"] == ["fcStart"]


def test_空なら拒否する(srv):
    res = asyncio.run(srv.set_calibration(FakeRequest({})))
    assert res.status_code == 400


def test_数値でない値は捨てる(srv):
    assert put(srv, {"fcStart": "abc", "roastedG": 38.4})["used"] == ["roastedG"]


def test_初期値に戻せる(srv, tmp_path):
    """自分で測り直した値を捨てると、空ではなくアプリの初期値へ戻る。"""
    import roastlib.calibration as beancal
    put(srv, {"fcStart": 420})
    assert (sandbox_path(tmp_path, "ROAST_CALIBRATION_PATH")).exists()
    assert body_of(srv.get_calibration())["scale"]["U0"] != \
        beancal.DEFAULT_CALIBRATION["scale"]["U0"]
    srv.clear_calibration()
    assert not (sandbox_path(tmp_path, "ROAST_CALIBRATION_PATH")).exists()
    assert body_of(srv.get_calibration())["scale"]["U0"] == \
        beancal.DEFAULT_CALIBRATION["scale"]["U0"]


def test_較正が推定に効く(srv):
    """較正するとキャッシュも捨てられ、推定値が作り直されること。

    初期値の較正がすでに効いているので、そこから動く測定値を入れて確かめる。
    """
    prof = srv.beancal.CALIBRATION_PROFILE
    before = srv._profile_estimate(prof["roast"], prof["fan"], 0.10)
    put(srv, {"fcStart": 390, "roastedG": 37.2})
    after = srv._profile_estimate(prof["roast"], prof["fan"], 0.10)
    assert after["roast_index"] != before["roast_index"]
    assert after["end_bean_temp"] != before["end_bean_temp"]


def fitted_only(srv):
    """保存ファイルから読んだ較正値だけを取り出す。

    チャフ量は較正ではなく設定で決めるので、常に入っている。
    焙煎ログから学んだ補正(T_FC_BEANなど)はここには含めない。あれは保存
    ファイルとは別の出どころで、_calibration_overrides()のほうで重なる。
    """
    ov = dict(srv._calibration_overrides_raw())
    ov.pop("CHAFF_G", None)
    return ov


def test_壊れた保存ファイルでも初期値で動く(srv, tmp_path):
    import roastlib.calibration as beancal
    (sandbox_path(tmp_path, "ROAST_CALIBRATION_PATH")).write_text("{壊れている", encoding="utf-8")
    assert fitted_only(srv) == beancal.DEFAULT_CALIBRATION["overrides"]
    assert body_of(srv.get_calibration())["scale"] == beancal.DEFAULT_CALIBRATION["scale"]


def test_知らない定数名は無視する(srv, tmp_path):
    """保存ファイルを手で編集された場合に、変な値をモデルへ流し込まない。"""
    (sandbox_path(tmp_path, "ROAST_CALIBRATION_PATH")).write_text(
        json.dumps({"overrides": {"U0": 0.004, "SOMETHING_ELSE": 1.0, "K_PYRO": "x"}}),
        encoding="utf-8")
    assert fitted_only(srv) == {"U0": 0.004}


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


def test_学んだ補正がモデルに渡る(srv):
    """焙煎ログから学んだ1ハゼ豆温度が、推定に使う定数に重なること。

    保存してある較正値(_calibration_overrides_raw)とは別の出どころ。
    ログが1件も無くても、初期データ(較正5本)から補正が出る。
    """
    raw = srv._calibration_overrides_raw()
    applied = srv._calibration_overrides()
    assert "T_FC_BEAN" in applied, "学んだ1ハゼ豆温度が渡っていません"
    assert "T_FC_BEAN" not in raw or applied["T_FC_BEAN"] != raw.get("T_FC_BEAN")
    # 初期データから出る値なので、モデルの既定値から大きくは離れない
    import roastlib.energy as E
    assert abs(applied["T_FC_BEAN"] - E.T_FC_BEAN) < 3.0


# ------------------------------------------------------------
# 焙煎ログからの学習(roastlib/learning.py に一本化した。2026-09)
# ------------------------------------------------------------
def test_プロファイルの推定に標高は効かない(srv):
    """プロファイルを選んだ時点では「どの豆を焼くか」が決まっていない。

    産地標高の合わないカーブで焼くこともあるので、一覧の推定に豆固有の補正は
    掛けない。掛かるのは、実際に焼いた豆が分かっている焙煎ログの側だけ。
    """
    import inspect
    assert "altitude" not in inspect.signature(srv._profile_estimate).parameters
    import roastlib.energy as E
    prof = srv.beancal.CALIBRATION_PROFILE
    est = srv._profile_estimate(prof["roast"], prof["fan"], 0.10)
    want = E.estimate(prof["roast"], prof["fan"], moisture=0.10,
                      cal=srv._calibration_overrides())
    assert est["end_bean_temp"] == round(want["end_bean_temp"], 1)
    assert est["roast_index"] == round(want["roast_index"], 3)


def test_学習は軸ごとに補正を持つ(srv):
    """標高・生産国・品種・精製方法の4軸。未知の項目でも壊れない。"""
    import roastlib.learning as L
    body = body_of(srv.get_bean_temp_learning())
    assert set(body["axes"]) <= set(L.AXES)
    assert body["seed"] == len(L.SEED_ROASTS)
    # 初期データだけでも全体の補正は出る
    assert body["global"]["fcBeanTemp"] is not None


def test_旧の学習APIは無くなっている(srv):
    """同じ定数を2つの経路が別々に決める状態を解消した。"""
    for name in ("calibration_from_logs", "apply_calibration_from_logs",
                 "clear_calibration_from_logs", "_learned_fc_bean_temp",
                 "_learned_sc_bean_temp", "_fc_bean_temp_for",
                 "_altitude_fc_slope"):
        assert not hasattr(srv, name), f"{name} が残っています"


def test_学習データを初期化できる(srv):
    """学習が変な方向に振れたときの出口。記録そのものは消さない。"""
    before = len(body_of(srv.list_roast_records()))
    d = body_of(srv.reset_bean_temp_learning())
    assert d["ok"] and d["seed"] > 0
    assert len(body_of(srv.list_roast_records())) == before


def test_節目を越えたら一度だけ知らせる(srv, tmp_path, monkeypatch):
    """焼くたびに知らせては煩いので、節目ごとに1回だけ。

    節目に届いているかの判定(_structure_due_state)は別に検証してあるので、
    ここでは「越えたときに1回だけ送る」ところだけを見る。
    """
    sent = []

    async def fake_push(title, body, force=False):
        sent.append((title, body))
        return 1

    monkeypatch.setattr(srv, "_send_push_to_all", fake_push)
    saved = {"n": 30, "roasts": [], "lastN": 18, "nextThreshold": 25,
             "due": True, "saved": {}}
    monkeypatch.setattr(srv, "_structure_due_state", lambda: dict(saved))
    srv._notify_structure_due()
    assert len(sent) == 1, "節目を越えたのに知らせていません"
    assert "見直せます" in sent[0][0] and "30点" in sent[0][1]

    # 2回目は出ない(同じ節目)。保存した印を読み直す。
    st2 = json.loads(sandbox_path(tmp_path, "ROAST_MODEL_STRUCTURE_PATH")
                     .read_text(encoding="utf-8"))
    assert st2["notifiedThreshold"] == 25
    monkeypatch.setattr(srv, "_structure_due_state",
                        lambda: dict(saved, saved=st2))
    srv._notify_structure_due()
    assert len(sent) == 1, "同じ節目で2回知らせています"

    # 次の節目まで進めば、また知らせる
    monkeypatch.setattr(srv, "_structure_due_state",
                        lambda: dict(saved, nextThreshold=40, n=45, saved=st2))
    srv._notify_structure_due()
    assert len(sent) == 2, "次の節目で知らせていません"


def test_節目に届いていなければ知らせない(srv, monkeypatch):
    sent = []

    async def fake_push(title, body, force=False):
        sent.append(title)
        return 1

    monkeypatch.setattr(srv, "_send_push_to_all", fake_push)
    monkeypatch.setattr(srv, "_structure_due_state",
                        lambda: {"n": 18, "roasts": [], "lastN": 18,
                                 "nextThreshold": 25, "due": False, "saved": {}})
    srv._notify_structure_due()
    assert sent == []


# ------------------------------------------------------------
# 覚えている値が、もとを書き換えたときに必ず作り直されること
# ------------------------------------------------------------
def _rec(**kw):
    r = {"profile_source": "custom", "profile_id": "p1", "profile_name": "A",
         "roasted_at": "2026-09-06T10:00:00",
         "roast_curve": [[0, 185], [60, 95], [240, 180], [500, 210], [760, 240]],
         "fan_curve": [[0, 70]], "duration": 760,
         "green_g": 50.0, "roasted_g": 42.0,
         "fc_time": 442.0, "fc_time_inferred": False, "sc_time": 622.0}
    r.update(kw)
    return r


def test_ログを保存したら学習値は作り直す(srv):
    """呼び出し側で消し忘れると、古い学習値のまま推定が続く。
    保存そのものが引き金になるようにしてある。"""
    srv._save_roast_records({"r1": _rec()})
    first = srv._learned_now()
    srv._save_roast_records({"r1": _rec(), "r2": _rec(green_g=48.0, roasted_g=40.0)})
    assert srv._LEARNED_CACHE == {}, "保存しても覚えたままだった"
    second = srv._learned_now()
    assert second["logs"] == first["logs"] + 1, "増えたログが学習に入っていない"


def test_較正を書き換えたら学習値も推定値も作り直す(srv):
    """較正は推定にも学習にも効くので、片方だけ残ると食い違う。"""
    srv._save_roast_records({"r1": _rec()})
    srv._learned_now()
    srv._profile_estimate([[0, 185], [760, 240]], None, 0.11)
    assert srv._LEARNED_CACHE and srv._PROFILE_ESTIMATE_CACHE
    srv._save_calibration({"measurements": {}, "overrides": {"U0": 0.0035}})
    assert srv._LEARNED_CACHE == {}, "較正を変えても学習値が残っていた"
    assert not srv._PROFILE_ESTIMATE_CACHE, "較正を変えても推定値が残っていた"


def test_推定値の控えは前提が変わると使わない(srv):
    """較正やモデルの版が変われば、前に計算した推定値は当てにならない。"""
    srv._profile_estimate([[0, 185], [760, 240]], None, 0.11)
    srv._save_estimate_cache()
    fp = srv._estimate_cache_fingerprint()
    assert srv.ESTIMATE_CACHE_PATH.exists()
    # 読み直せる
    srv._PROFILE_ESTIMATE_CACHE.clear()
    srv._FILE_CACHE.clear()
    srv._load_estimate_cache()
    assert srv._PROFILE_ESTIMATE_CACHE, "控えから戻らない"
    # 較正が変われば指紋が変わり、控えは使われない
    srv._save_calibration({"measurements": {}, "overrides": {"U0": 0.0040}})
    assert srv._estimate_cache_fingerprint() != fp
    srv._FILE_CACHE.clear()
    srv._load_estimate_cache()
    assert not srv._PROFILE_ESTIMATE_CACHE, "前提が変わったのに古い推定値を使った"


def test_較正の当てはめは覚えている値を巻き込まない(srv):
    """較正を測り直したあと、推定も学習も新しい定数で計算されること。"""
    import roastlib.energy as E
    srv._save_roast_records({"r1": _rec()})
    before_est = srv._profile_estimate([[0, 185], [760, 240]], None, 0.11)
    put(srv, {"fcStart": 400, "roastedG": 39.5})
    after_est = srv._profile_estimate([[0, 185], [760, 240]], None, 0.11)
    assert after_est != before_est, "較正したのに推定値が変わらない"
    assert srv._calibration_overrides_raw()["U0"] != E.U0


def test_計算中に較正が変わったら学習値を書き戻さない(srv, monkeypatch):
    """学習は2.6秒かかる。裏で計算している最中に較正を変えると、
    終わった側が変更前の答えを書き戻し、以降ずっとそれが返っていた
    (メモリに当たった時点では指紋を見ないため)。"""
    srv._save_roast_records({"r1": _rec()})
    calls = []

    def fake_learn(recs, **kw):
        calls.append(kw.get("cal", {}).get("U0"))
        if len(calls) == 1:
            # 1回目の計算中に較正が変わった、という状況を作る
            srv._save_calibration({"measurements": {}, "overrides": {"U0": 0.0041}})
        return {"logs": len(recs), "u0": kw.get("cal", {}).get("U0")}

    monkeypatch.setattr(srv.beanlearn, "learn", fake_learn)
    res = srv._learned_now()
    # 呼ばれた回数は数えない(他のテストが残した先読みスレッドが
    # 同じ関数を呼ぶことがあるため)。見るのは、返る値と覚える値。
    assert calls[0] != 0.0041, "前提が変わる前の計算になっていない"
    assert len(calls) >= 2, "前提が変わったのに計算し直していない"
    assert res["u0"] == 0.0041, "変更前の答えをそのまま返した"
    assert srv._LEARNED_CACHE.get("value", {}).get("u0") == 0.0041, \
        "変更前の答えを覚えたままになっている"


def test_計算中に較正が変わったら推定値の控えを残さない(srv):
    """先読みの途中で較正が変わると、新しい指紋のまま古い前提の値が
    ファイルに残ってしまう。世代を見て書かないようにしてある。"""
    srv._profile_estimate([[0, 185], [760, 240]], None, 0.11)
    gen = srv._estimate_gen()
    srv._save_calibration({"measurements": {}, "overrides": {"U0": 0.0042}})
    srv._save_estimate_cache(expect_gen=gen)
    assert not srv.ESTIMATE_CACHE_PATH.exists(), "変更前の推定値が控えに残った"


def test_較正を変えたら推定値の控えファイルも消える(srv):
    """メモリだけ消しても、次の起動でファイルから戻ってきては意味がない。"""
    srv._profile_estimate([[0, 185], [760, 240]], None, 0.11)
    srv._save_estimate_cache()
    assert srv.ESTIMATE_CACHE_PATH.exists()
    srv._save_calibration({"measurements": {}, "overrides": {"U0": 0.0043}})
    assert not srv.ESTIMATE_CACHE_PATH.exists(), "古い控えが残っている"
