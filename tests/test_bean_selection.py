# -*- coding: utf-8 -*-
"""「焙煎する豆」の選択と、その豆で前に焼いたプロファイルの候補。

豆を選んでからプロファイルを選ぶ流れのための部分。他のサーバーテストと同じく
HTTPを介さず関数を直接呼び、保存先は環境変数で差し替える。
"""
import asyncio
import json

from conftest import sandbox_path


class FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def body_of(response):
    return json.loads(response.body.decode("utf-8"))


def _curve():
    """1ハゼまで届く、それらしい実測カーブ。焙煎度の推定に使う。"""
    import roastlib.calibration as beancal
    import roastlib.energy as E
    p = beancal.CALIBRATION_PROFILE
    r = E.estimate(p["roast"], p["fan"])
    return [[s["t"], round(s["air"], 1)] for s in r["series"]], p["fan"]


def _setup(srv, tmp_path):
    """同じ豆(生産年違い)2件と、その豆での焙煎記録を用意する。"""
    beans = {
        "b1": {"country": "タンザニア", "region": "Itumpi", "process": "ウォッシュド",
               "variety": "N39", "source": "OC", "crop_year": "2025",
               "altitude": "1500-2000m"},
        # group_key(国・産地・品種・精製・購入先)が同じ = 同じ豆の買い直し
        "b2": {"country": "タンザニア", "region": "Itumpi", "process": "ウォッシュド",
               "variety": "N39", "source": "OC", "crop_year": "2024"},
        "other": {"country": "グアテマラ", "region": "レタナ"},
    }
    (sandbox_path(tmp_path, "ROAST_BEAN_PURCHASES_PATH")).write_text(
        json.dumps(beans, ensure_ascii=False), encoding="utf-8")
    curve, fan = _curve()
    records = {
        "r1": {"bean_purchase_id": "b1", "profile_source": "preset", "profile_id": 12,
               "profile_name": "タンザニア 深煎り", "roasted_at": "2026-08-06T00:00:00Z",
               "rating": 4, "roast_curve": curve, "fan_curve": fan},
        "r2": {"bean_purchase_id": "b1", "profile_source": "preset", "profile_id": 12,
               "profile_name": "タンザニア 深煎り(浅め)", "roasted_at": "2026-08-07T00:00:00Z",
               "rating": 2, "roast_curve": curve, "fan_curve": fan},
        # 生産年違いの購入分も、同じ豆としてまとめて見えること
        "r3": {"bean_purchase_id": "b2", "profile_source": "custom", "profile_id": "c1",
               "profile_name": "自作 中煎り", "roasted_at": "2026-07-01T00:00:00Z",
               "roast_curve": curve, "fan_curve": fan},
        # 別の豆の記録は混ざらないこと
        "r4": {"bean_purchase_id": "other", "profile_source": "preset", "profile_id": 99,
               "profile_name": "グアテマラ", "roasted_at": "2026-07-02T00:00:00Z",
               "roast_curve": curve, "fan_curve": fan},
    }
    (sandbox_path(tmp_path, "ROAST_RECORDS_PATH")).write_text(
        json.dumps(records, ensure_ascii=False), encoding="utf-8")
    (sandbox_path(tmp_path, "ROAST_CUSTOM_PATH")).write_text(
        json.dumps({"c1": {"id": "c1", "name": "自作 中煎り", "uuid": "0001700000000001",
                           "roast": [[0, 180], [300, 220]], "fan": [[0, 60], [300, 70]]}},
                   ensure_ascii=False), encoding="utf-8")
    return beans, records


# ---- 焙煎する豆の選択 ----

def test_はじめは選んでいない(srv):
    d = body_of(srv.get_selected_bean())
    assert d["bean_purchase_id"] is None and d["label"] == ""


def test_選ぶと保存され読み直せる(srv, tmp_path):
    _setup(srv, tmp_path)
    d = asyncio.run(srv.set_selected_bean(FakeRequest({"bean_purchase_id": "b1"})))
    assert body_of(d)["bean_purchase_id"] == "b1"
    # 標高も一緒に返す(豆温度モデルの学習に使う)
    assert body_of(srv.get_selected_bean())["altitude"] == "1500-2000m"


def test_選択を外せる(srv, tmp_path):
    _setup(srv, tmp_path)
    asyncio.run(srv.set_selected_bean(FakeRequest({"bean_purchase_id": "b1"})))
    asyncio.run(srv.set_selected_bean(FakeRequest({"bean_purchase_id": None})))
    assert srv._load_selected_bean_id() is None


def test_無い豆は選べない(srv, tmp_path):
    _setup(srv, tmp_path)
    res = asyncio.run(srv.set_selected_bean(FakeRequest({"bean_purchase_id": "存在しない"})))
    assert res.status_code == 404


def test_豆が消されたら選択も無効になる(srv, tmp_path):
    """豆を削除したあと、消えたidのまま焙煎記録に紐づけない。"""
    _setup(srv, tmp_path)
    asyncio.run(srv.set_selected_bean(FakeRequest({"bean_purchase_id": "b1"})))
    (sandbox_path(tmp_path, "ROAST_BEAN_PURCHASES_PATH")).write_text("{}", encoding="utf-8")
    assert srv._load_selected_bean_id() is None
    assert body_of(srv.get_selected_bean())["bean_purchase_id"] is None


def test_壊れた保存ファイルでも落ちない(srv, tmp_path):
    (sandbox_path(tmp_path, "ROAST_SELECTED_BEAN_PATH")).write_text("{壊れている", encoding="utf-8")
    assert srv._load_selected_bean_id() is None


def test_選んだ豆は焙煎記録に紐づく(srv, tmp_path):
    """焙煎前に選んでおけば、記録を作るときに自動で入る。"""
    _setup(srv, tmp_path)
    res = asyncio.run(srv.create_roast_record(FakeRequest({
        "profile_source": "preset", "profile_id": 12, "profile_name": "テスト",
        "bean_purchase_id": "b1", "roast_curve": [[0, 180], [300, 220]],
    })))
    rid = body_of(res)["id"]
    assert srv._load_roast_records()[rid]["bean_purchase_id"] == "b1"


# ---- その豆で前に焼いたプロファイルの候補 ----

def test_候補はプロファイルごとにまとまる(srv, tmp_path):
    _setup(srv, tmp_path)
    d = body_of(srv.bean_roast_candidates("b1"))
    names = {c["profile_name"] for c in d["candidates"]}
    # 同じプロファイルで2回焼いた分は1件にまとまる
    assert len(d["candidates"]) == 2
    # 名前は一番新しい記録のもの(浅め/深めに調整して送ると名前が変わるため)
    assert "タンザニア 深煎り(浅め)" in names
    assert "自作 中煎り" in names


def test_生産年違いも同じ豆として集める(srv, tmp_path):
    """同じ豆を買い直した分の焙煎記録も、選ぶときの手がかりになる。"""
    _setup(srv, tmp_path)
    d = body_of(srv.bean_roast_candidates("b1"))
    years = {r["bean_crop_year"] for c in d["candidates"] for r in c["roasts"]}
    assert years == {"2025", "2024"}


def test_別の豆の記録は混ざらない(srv, tmp_path):
    _setup(srv, tmp_path)
    d = body_of(srv.bean_roast_candidates("other"))
    assert [c["profile_name"] for c in d["candidates"]] == ["グアテマラ"]


def test_焙煎度と評価が出る(srv, tmp_path):
    """「その時どう焼けたか」が分かるように、実測カーブから推定した焙煎度を出す。"""
    _setup(srv, tmp_path)
    d = body_of(srv.bean_roast_candidates("b1"))
    c = next(c for c in d["candidates"] if c["profile_source"] == "preset")
    assert c["count"] == 2
    # 新しい順
    assert c["roasts"][0]["roasted_at"] > c["roasts"][1]["roasted_at"]
    assert c["roasts"][0]["rating"] == 2 and c["roasts"][1]["rating"] == 4
    assert c["best_rating"] == 4
    for r in c["roasts"]:
        assert r["roast_index"] > 1.0
        assert r["roast_index_level"] in ("浅煎り", "中煎り", "中深煎り", "深煎り")


def test_消えたプロファイルは選べない印を付ける(srv, tmp_path):
    """保存プロファイルを消したあとも記録は残る。候補には出すが選ばせない。"""
    _setup(srv, tmp_path)
    (sandbox_path(tmp_path, "ROAST_CUSTOM_PATH")).write_text("{}", encoding="utf-8")
    d = body_of(srv.bean_roast_candidates("b1"))
    c = next(c for c in d["candidates"] if c["profile_source"] == "custom")
    assert c["available"] is False


def test_カーブが無い記録でも落ちない(srv, tmp_path):
    """手で追加した過去の記録には実測カーブが無い。焙煎度はNoneで出す。"""
    _setup(srv, tmp_path)
    recs = srv._load_roast_records()
    recs["r5"] = {"bean_purchase_id": "b1", "profile_source": "preset", "profile_id": 77,
                  "profile_name": "カーブなし", "roasted_at": "2026-06-01T00:00:00Z"}
    (sandbox_path(tmp_path, "ROAST_RECORDS_PATH")).write_text(
        json.dumps(recs, ensure_ascii=False), encoding="utf-8")
    d = body_of(srv.bean_roast_candidates("b1"))
    c = next(c for c in d["candidates"] if c["profile_name"] == "カーブなし")
    assert c["roasts"][0]["roast_index"] is None


def test_無い豆の候補は404(srv, tmp_path):
    _setup(srv, tmp_path)
    assert srv.bean_roast_candidates("存在しない").status_code == 404
