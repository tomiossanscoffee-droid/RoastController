# -*- coding: utf-8 -*-
"""温度ガイド線の値の正規化(app/server.py の _clean_guide_temp)。

数値以外や範囲外が保存されると、焙煎度の判定やフェーズ分割の温度比較に
そのまま渡ってTypeErrorになり、味を推測・プロファイル生成が500になる
(2026-08、APIを直接叩いて発見)。読み書きの両方で正規化して防ぐ。
"""
import contextlib
import json
import os
import time

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


# ------------------------------------------------------------
# 配色(theme)
# ------------------------------------------------------------
import re  # noqa: E402
from pathlib import Path  # noqa: E402

from app.server import APP_THEMES, DEFAULT_APP_SETTINGS as _SETTINGS  # noqa: E402

INDEX = Path(__file__).resolve().parent.parent / "app/static/index.html"
MOBILE = Path(__file__).resolve().parent.parent / "app/static/mobile.html"


def test_配色の既定はダーク():
    assert _SETTINGS["theme"] == "dark"
    assert APP_THEMES == ("dark", "light", "contrast")


@pytest.mark.parametrize("page", [INDEX, MOBILE])
def test_JSが読むCSS変数が全配色で定義されている(page):
    """cssVar() で読む変数が :root に無いと、その色だけ既定の灰色になる。

    dark以外は :root を継承するので、:root に全部あることを確かめれば足りる。
    """
    s = page.read_text(encoding="utf-8")
    used = set(re.findall(r"cssVar\('(--[a-z0-9-]+)'", s))
    root = re.search(r":root\s*\{(.*?)\n  \}", s, re.S).group(1)
    defined = set(re.findall(r"(--[a-z0-9-]+):", root))
    missing = sorted(used - defined)
    assert not missing, f"{page.name} の :root に無い変数: {missing}"


@pytest.mark.parametrize("page", [INDEX, MOBILE])
def test_追加した2配色が定義されている(page):
    s = page.read_text(encoding="utf-8")
    for theme in ("light", "contrast"):
        assert f'[data-theme="{theme}"]' in s, f"{page.name} に {theme} が無い"


@pytest.mark.parametrize("page", [INDEX, MOBILE])
def test_グラフの色が直書きされていない(page):
    """JS側に色を直書きすると、配色を切り替えてもそこだけ変わらない。"""
    s = page.read_text(encoding="utf-8")
    tail = s.split("</style>", 1)[1]
    hard = set(re.findall(r"'#[0-9a-fA-F]{6}'|'rgba?\([0-9., ]+\)'", tail))
    hard.discard("'#888888'")   # cssVar() が変数を見つけられなかった時の保険
    assert not hard, f"{page.name} に直書きの色が残っている: {sorted(hard)}"


def test_配色のコントラストが基準を満たす():
    """3配色とも、文字4.5:1・線3.0:1を満たすこと(scripts/check_theme_contrast.py と同じ判定)。

    焙煎中は機器のそばから読むので、埋もれる色があると実害がある。
    2026-08、ダークの2ハゼのガイド線が2.04しかなく、ほぼ読めなかった。
    """
    import subprocess
    import sys as _sys
    script = Path(__file__).resolve().parent.parent / "scripts/check_theme_contrast.py"
    out = subprocess.run([_sys.executable, str(script)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"配色の基準を満たしていません\n{out.stdout}"


def test_PC版とモバイル版で同じ変数は同じ色():
    """片方だけ色を直すと、スマホとPCで見た目が食い違う。"""
    def block(text, name):
        pat = (r":root\s*\{(.*?)\n  \}" if name == ":root"
               else rf'\[data-theme="{name}"\]\s*\{{(.*?)\n  \}}')
        m = re.search(pat, text, re.S)
        return {k: v.split("/*")[0].strip()
                for k, v in re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", m.group(1))}
    pc, mo = INDEX.read_text(encoding="utf-8"), MOBILE.read_text(encoding="utf-8")
    for name in (":root", "light", "contrast"):
        a, b = block(pc, name), block(mo, name)
        diff = sorted(k for k in set(a) & set(b) if a[k] != b[k])
        assert not diff, f"{name} で食い違い: {diff}"


def test_フェーズ境界の決め方を保存できる(srv):
    """既定は吸入温度。豆温度モードに切り替えると保存され、読み直せる。"""
    assert srv._load_guide_temps()["mode"] == "air"
    srv.GUIDE_TEMPS_PATH.write_text(json.dumps({
        "colorChange": 170, "firstCrack": 223, "secondCrack": 242,
        "mode": "bean", "beanColorChange": 140}), encoding="utf-8")
    got = srv._load_guide_temps()
    assert got["mode"] == "bean" and got["beanColorChange"] == 140


def test_知らない決め方は吸入温度に戻す(srv):
    """壊れた設定でモードが決まらなくなるより、従来の動きに落とすほうが安全。"""
    srv.GUIDE_TEMPS_PATH.write_text(json.dumps({
        "colorChange": 170, "firstCrack": 223, "mode": "なにか"}), encoding="utf-8")
    assert srv._load_guide_temps()["mode"] == "air"


def test_豆温度のカラーチェンジは範囲外なら既定に戻す(srv):
    """低すぎると水が抜ける前、高すぎると1ハゼに近すぎてBフェーズが潰れる。"""
    import roastlib.profile_generator as pgen
    for bad in (10, 300, "abc", None):
        srv.GUIDE_TEMPS_PATH.write_text(json.dumps({
            "colorChange": 170, "firstCrack": 223,
            "mode": "bean", "beanColorChange": bad}), encoding="utf-8")
        assert srv._load_guide_temps()["beanColorChange"] == pgen.BEAN_COLOR_CHANGE, bad


def test_ガイド線は初期値から始まる(srv):
    """新規インストールでも、届いたその日からABCモード・味を推測が使える。"""
    assert not srv.GUIDE_TEMPS_PATH.exists()
    gt = srv._load_guide_temps()
    assert gt["colorChange"] == 170 and gt["firstCrack"] == 223 and gt["secondCrack"] == 242


def test_ガイド線を空にすると初期値に戻る(srv):
    """何も無い状態にはしない。空にすると使えなくなり、直し方も分かりにくい。"""
    srv.GUIDE_TEMPS_PATH.write_text(json.dumps({
        "colorChange": None, "firstCrack": "", "secondCrack": 9999}), encoding="utf-8")
    gt = srv._load_guide_temps()
    assert (gt["colorChange"], gt["firstCrack"], gt["secondCrack"]) == (170, 223, 242)


def test_較正は初期値から始まる(srv):
    """実測した較正を初期値として持つ。空の状態からは始めない。"""
    import roastlib.calibration as beancal
    assert not srv.CALIBRATION_PATH.exists()
    cal = srv._load_calibration()
    assert set(cal["measurements"]["roasts"]) == {"deep", "light"}
    assert cal["measurements"]["roasts"]["deep"]["fcStart"] == 442.0
    assert len(cal["measurements"]["roasts"]["deep"]["aborts"]) == 4
    # モデルにもその値が渡る(既定値のままではない)
    import roastlib.energy as E
    ov = srv._calibration_overrides_raw()
    assert ov["U0"] != E.U0 and abs(ov["U0"] - beancal.DEFAULT_CALIBRATION["overrides"]["U0"]) < 1e-12


def test_較正を捨てると初期値に戻る(srv):
    """自分で測り直した値を消したとき、空ではなく初期値へ戻ること。"""
    import roastlib.calibration as beancal
    srv.CALIBRATION_PATH.write_text(json.dumps({
        "measurements": {"roasts": {"deep": {"fcStart": 1.0}}},
        "overrides": {"U0": 0.001}, "modelVersion": "x"}), encoding="utf-8")
    assert srv._load_calibration()["overrides"]["U0"] == 0.001
    srv.CALIBRATION_PATH.unlink()
    back = srv._load_calibration()
    assert back["overrides"]["U0"] == beancal.DEFAULT_CALIBRATION["overrides"]["U0"]


def test_初期値は書き換えられない(srv):
    """読むたびにコピーを返すこと。使う側が触っても初期値は汚れない。"""
    import roastlib.calibration as beancal
    before = beancal.DEFAULT_CALIBRATION["overrides"]["U0"]
    srv._default_calibration()["overrides"]["U0"] = 999.0
    srv._load_calibration()["measurements"]["roasts"].clear()
    assert beancal.DEFAULT_CALIBRATION["overrides"]["U0"] == before
    assert set(beancal.DEFAULT_CALIBRATION["measurements"]["roasts"]) == {"deep", "light"}


# ------------------------------------------------------------
# 連続焙煎モード: チェックを外したら次を送らない
# ------------------------------------------------------------
class _FakeSession:
    """BLEの代わり。送信されたプロファイルを控えるだけ。"""

    def __init__(self, on_send=None):
        self.is_connected = True
        self.sent = []
        self._on_send = on_send

    async def send_profile(self, profile):
        if self._on_send is not None:
            await self._on_send()
        self.sent.append(profile)


def _prepare_continuous(srv, on_send=None):
    """再送に必要なもの(接続・前回送ったプロファイル)を揃える。"""
    srv._session = _FakeSession(on_send)
    srv._last_sent_profile = {
        "name": "test", "uuid": "0000000000000001",
        "roast": [[0, 180], [600, 230]], "fan": [[0, 70], [600, 70]],
        "cooldown": [620, 60],
        # 焙煎回数の加算には、どのプロファイルを焼いたかが要る
        "profile_source": "preset", "profile_id": 1,
    }
    srv._continuous_roast = True
    srv._continuous_task = None
    srv._continuous_sending = False
    return srv._session


def test_待っている間に外したら次を送らない(srv):
    """一番困るのは「外したのに次が始まる」こと。"""
    import asyncio

    async def run():
        sess = _prepare_continuous(srv)
        srv.CONTINUOUS_ROAST_RESTART_DELAY = 0.05
        srv.APP_SETTINGS_PATH.write_text(
            json.dumps({"continuousRoastDelay": 5}), encoding="utf-8")
        task = asyncio.create_task(srv._continuous_restart_after_delay())
        await asyncio.sleep(0.02)
        srv._continuous_roast = False        # 待っている間にチェックを外す
        srv._cancel_continuous_restart()
        try:
            await asyncio.wait_for(task, timeout=6)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        assert sess.sent == [], "外したのに送ってしまった"

    asyncio.run(run())


def test_送信の直前に外しても送らない(srv):
    """待ち時間が終わってから送るまでの間に外された場合も止める。"""
    import asyncio

    async def run():
        sess = _prepare_continuous(srv)
        srv.APP_SETTINGS_PATH.write_text(
            json.dumps({"continuousRoastDelay": 5}), encoding="utf-8")
        # 待ち時間を実質0にして、送信直前の判定だけを見る
        srv._clamp_continuous_delay = lambda v: 0.0
        srv._continuous_roast = False        # 送る直前に外れている状態
        await srv._continuous_restart_after_delay()
        assert sess.sent == [], "外れているのに送ってしまった"

    asyncio.run(run())


def test_転送中は取り消さない(srv):
    """BLEの転送を途中で切ると、焙煎機に半端なプロファイルが残る。

    送るか否かは転送を始める直前に判断済みなので、始まってしまったら
    最後まで送り切る。
    """
    import asyncio

    async def run():
        started = asyncio.Event()

        async def on_send():
            started.set()
            await asyncio.sleep(0.05)        # 転送中のつもり

        sess = _prepare_continuous(srv, on_send)
        srv._clamp_continuous_delay = lambda v: 0.0
        task = asyncio.create_task(srv._continuous_restart_after_delay())
        srv._continuous_task = task
        await started.wait()
        srv._continuous_roast = False        # 転送の最中に外す
        srv._cancel_continuous_restart()
        assert not task.cancelled(), "転送中に取り消してしまった"
        await task
        assert len(sess.sent) == 1, "送り切れていない"

    asyncio.run(run())


def test_焙煎中に外したら排出完了でも予約が入らない(srv):
    """ご指摘の経路。焙煎の最中にチェックを外したら、その回の終わりで
    次のプロファイルを送らないこと。"""
    import asyncio

    async def run():
        sess = _prepare_continuous(srv)
        srv._clamp_continuous_delay = lambda v: 0.0
        # 焙煎中に外す(アプリからの set_continuous_roast と同じ処理)
        srv._continuous_roast = False
        srv._cancel_continuous_restart()
        # そのまま最後まで進んで「排出完了」に至る
        srv._on_state("排出完了")
        await asyncio.sleep(0.05)
        assert srv._continuous_task is None, "予約が入ってしまった"
        assert sess.sent == [], "外したのに次を送ってしまった"
        # 入れたままなら、これまでどおり予約が入る
        srv._continuous_roast = True
        srv._on_state("排出完了")
        assert srv._continuous_task is not None, "入れているのに予約が入らない"
        await srv._continuous_task
        assert len(sess.sent) == 1

    asyncio.run(run())


def test_焙煎中にモードを変えてもその回のログは取り続ける(srv):
    """連続焙煎の入切と、いま走っている焙煎の記録は別物。

    切り替えた拍子に記録が止まると、その回の実測が失われる。
    """
    import asyncio

    async def run():
        _prepare_continuous(srv)
        srv._roast_counted = False
        srv._auto_save_claim = None
        srv._duplicate_confirm = None
        srv.UNHANDLED_ROAST_COUNT_DELAY = 0.01

        # 焙煎の最中に入切を繰り返す
        for enabled in (False, True, False):
            srv._continuous_roast = enabled
            if not enabled:
                srv._stop_continuous_roast("テスト")
            assert srv._roast_counted is False, "記録の状態が巻き戻された"

        # 焙煎完了。どの端末も保存しなかった場合の回数加算まで通ること
        srv._on_state("焙煎完了・冷却中")
        await asyncio.sleep(0.05)
        assert srv._roast_counted is True, "焙煎した事実が残らなかった"

    asyncio.run(run())


# ------------------------------------------------------------
# 焙煎ログは、プロファイルや豆を消しても残す
# ------------------------------------------------------------
def _make_record(**kw):
    rec = {"profile_source": "custom", "profile_id": "p1", "profile_name": "テスト用",
           "roasted_at": "2026-09-06T10:00:00",
           "roast_curve": [[0, 180], [600, 230]], "fan_curve": [[0, 70]],
           "duration": 600, "green_g": 50.0, "roasted_g": 42.0,
           "fc_time": 440.0, "fc_time_inferred": False, "sc_time": 620.0}
    rec.update(kw)
    return rec


def test_プロファイルを消してもログは残る(srv):
    """ログは豆温度モデルの学習データでもあるので、プロファイルの都合で
    消してはいけない。焼いた事実と実測値はプロファイルとは別物である。"""
    import roastlib.learning as L
    srv._save_custom({"p1": {"name": "テスト用", "uuid": "0000000000000001",
                             "roast": [[0, 180], [600, 230]], "fan": [[0, 70], [600, 70]],
                             "cooldown": [620, 60]}})
    srv._save_roast_records({"r1": _make_record()})
    srv.delete_custom_profile("p1")
    recs = srv._load_roast_records()
    assert "r1" in recs, "プロファイルと一緒にログまで消えた"
    assert not srv._load_custom(), "プロファイルは消えているべき"
    # 一覧にも出るし、学習にも使えるまま
    assert len(json.loads(srv.list_roast_records().body)) == 1
    assert L.is_learnable(recs["r1"]) is True
    # ログ単体でも開ける(名前とカーブは記録の中に持っている)
    one = json.loads(srv.get_roast_record("r1").body)
    assert one["profile_name"] == "テスト用" and len(one["roast_curve"]) == 2


def test_豆を消してもログは残り紐付けだけ外れる(srv):
    """豆を消しても、焼いた事実と実測値は残す(紐付けだけ外す)。"""
    srv._save_bean_purchases({"b1": {"country": "ケニア"}})
    srv._save_roast_records({"r1": _make_record(bean_purchase_id="b1")})
    srv.delete_bean_purchase("b1")
    recs = srv._load_roast_records()
    assert "r1" in recs, "豆と一緒にログまで消えた"
    assert recs["r1"]["bean_purchase_id"] is None, "紐付けが外れていない"
    assert recs["r1"]["green_g"] == 50.0 and recs["r1"]["fc_time"] == 440.0


# ------------------------------------------------------------
# 書き出し・取り込み
# ------------------------------------------------------------
def _seed_for_export(srv):
    srv._save_custom({"p1": {"name": "A", "uuid": "0000000000000001",
                             "roast": [[0, 180], [600, 230]], "fan": [[0, 70]],
                             "cooldown": [620, 60]}})
    srv._save_roast_records({"r1": _make_record()})
    srv._save_bean_purchases({"b1": {"country": "ケニア"}})


def test_一式には計算のもとが揃っている(srv):
    """別の端末で取り込んだときに、同じ状態を作り直せること。"""
    _seed_for_export(srv)
    d = json.loads(srv.export_data(scope="all").body)
    assert d["format"] == "roast-studio-export"
    for key in ("custom_profiles", "roast_records", "bean_purchases",
                "calibration", "model_structure", "guide_temps", "app_settings",
                "favorites", "learned"):
        assert key in d["data"], f"{key} が入っていない"


def test_プロファイルだけの書き出しは他を含まない(srv):
    """人に渡すときに、焙煎ログや豆の情報まで付いていかないこと。"""
    _seed_for_export(srv)
    d = json.loads(srv.export_data(scope="profiles").body)
    assert d["scope"] == "profiles"
    assert set(d["data"]) == {"custom_profiles"}


def test_取り込みは混ぜ合わせて控えを残す(srv, tmp_path):
    """同じidは上書きし、無いものは残す。取り込む前に控えを作る。"""
    import asyncio

    class _Req:
        def __init__(self, body): self._body = body
        async def json(self): return self._body

    _seed_for_export(srv)
    payload = json.loads(srv.export_data(scope="all").body)
    # 取り込む側には別のプロファイルがある。これは残るべき。
    srv._save_custom({"other": {"name": "B", "uuid": "0000000000000002",
                                "roast": [[0, 180], [600, 230]], "fan": [[0, 70]],
                                "cooldown": [620, 60]}})
    res = json.loads(asyncio.run(srv.import_data(_Req(payload))).body)
    assert res["ok"] is True
    got = srv._load_custom()
    assert set(got) == {"p1", "other"}, "取り込みで元のものが消えた"
    # 控えが残っている
    assert (tmp_path / res["backup"]).exists() or \
        (srv.ROAST_RECORDS_PATH.parent / res["backup"]).exists()


def test_知らない形式は断る(srv):
    import asyncio

    class _Req:
        def __init__(self, body): self._body = body
        async def json(self): return self._body

    r = asyncio.run(srv.import_data(_Req({"format": "なにか", "data": {}})))
    assert r.status_code == 400


def test_学習パラメータは食い違えば使わない(srv):
    """ログと食い違う学習結果を引き継ぐと、間違った推定をしてしまう。"""
    import asyncio

    class _Req:
        def __init__(self, body): self._body = body
        async def json(self): return self._body

    _seed_for_export(srv)
    payload = json.loads(srv.export_data(scope="all").body)
    assert payload["data"]["learned"]["fingerprint"]
    # そのまま取り込めば引き継ぐ
    res = json.loads(asyncio.run(srv.import_data(_Req(payload))).body)
    assert res["learned"] == "引き継いだ"
    # 中身を差し替えて指紋を食い違わせると、引き継がない
    payload["data"]["learned"]["fingerprint"] = "0" * 64
    res2 = json.loads(asyncio.run(srv.import_data(_Req(payload))).body)
    assert res2["learned"] == "計算し直す"


def test_学習結果は控えから読み直せる(srv):
    """毎回計算すると焙煎ログ23件で2.6秒かかる。控えがあれば読むだけ。"""
    _seed_for_export(srv)
    first = srv._learned_now()
    assert srv.LEARNED_CACHE_PATH.exists(), "控えが書かれていない"
    srv._LEARNED_CACHE.clear()
    srv._FILE_CACHE.clear()
    assert srv._learned_now() == first, "控えから同じ値が返らない"
    # もとが変われば指紋が変わり、作り直しになる
    fp = srv._learned_fingerprint()
    recs = srv._load_roast_records()
    recs["r2"] = _make_record(green_g=48.0)
    srv._save_roast_records(recs)
    srv._FILE_CACHE.clear()
    assert srv._learned_fingerprint() != fp


def _seed_three_profiles(srv):
    srv._save_custom({
        "p1": {"name": "浅煎りA", "uuid": "0000000000000001",
               "roast": [[0, 180], [600, 225]], "fan": [[0, 70]], "cooldown": [620, 60]},
        "p2": {"name": "深煎り/B", "uuid": "0000000000000002",
               "roast": [[0, 180], [600, 240]], "fan": [[0, 70]], "cooldown": [620, 60]},
        "p3": {"name": "中煎りC", "uuid": "0000000000000003",
               "roast": [[0, 180], [600, 232]], "fan": [[0, 70]], "cooldown": [620, 60]},
    })


def test_プロファイルは選んだものだけ書き出せる(srv):
    """人に1件だけ渡したいことがある。全部付いていかないこと。"""
    _seed_three_profiles(srv)
    d = json.loads(srv.export_data(scope="profiles", ids="p1,p3").body)
    assert set(d["data"]["custom_profiles"]) == {"p1", "p3"}


def test_1件だけの書き出しはファイル名に名前が入る(srv):
    """受け取った側が、開かなくても中身を見分けられるように。"""
    _seed_three_profiles(srv)
    import urllib.parse
    header = srv.export_data(scope="profiles", ids="p1").headers["content-disposition"]
    assert "浅煎りA" in urllib.parse.unquote(header)


def test_ファイル名に使えない記号は落とす(srv):
    """プロファイル名にスラッシュが入ることがある。そのまま名前にすると
    別のフォルダを指してしまう。"""
    _seed_three_profiles(srv)
    header = srv.export_data(scope="profiles", ids="p2").headers["content-disposition"]
    assert "/" not in header.split('filename="')[1].split('"')[0]
    # 日本語の名前は符号化された側に入る。素のスラッシュが混じらないこと
    assert "%2F" not in header.split("filename*=UTF-8''")[1]


def test_選んだidが無ければ書き出さない(srv):
    """消したプロファイルのidが残っていても、空のファイルを渡さない。"""
    _seed_three_profiles(srv)
    assert srv.export_data(scope="profiles", ids="いない").status_code == 404


def test_idの指定は一式には使えない(srv):
    """一式は絞れない(絞ると取り込んだ先で辻褄が合わなくなる)。"""
    _seed_three_profiles(srv)
    assert srv.export_data(scope="all", ids="p1").status_code == 400


def test_id無しの書き出しは全部入る(srv):
    """これまでの動きを変えないこと。"""
    _seed_three_profiles(srv)
    d = json.loads(srv.export_data(scope="profiles").body)
    assert set(d["data"]["custom_profiles"]) == {"p1", "p2", "p3"}


def _import(srv, payload, **extra):
    """取り込みを呼んで応答の中身を返す。"""
    import asyncio

    class _Req:
        def __init__(self, body): self._body = body
        async def json(self): return self._body

    return json.loads(asyncio.run(srv.import_data(_Req(dict(payload, **extra)))).body)


def _clashing_payload(srv):
    """同じidで中身の違うプロファイルが1件入った書き出しを作る。"""
    srv._save_custom({"p1": {"name": "取り込む側", "uuid": "0000000000000009",
                             "roast": [[0, 180], [600, 245]], "fan": [[0, 70]],
                             "cooldown": [620, 60]}})
    payload = json.loads(srv.export_data(scope="profiles").body)
    srv._save_custom({"p1": {"name": "いまの", "uuid": "0000000000000001",
                             "roast": [[0, 180], [600, 230]], "fan": [[0, 70]],
                             "cooldown": [620, 60]}})
    return payload


def test_重なりを残す指定なら今のものが生き残る(srv):
    """上書きの確認で「重ならないものだけ」を選んだとき。
    手を入れた自分のプロファイルが消えないこと。"""
    payload = _clashing_payload(srv)
    res = _import(srv, payload, on_conflict="skip")
    assert srv._load_custom()["p1"]["name"] == "いまの", "残す指定なのに上書きされた"
    assert res["merged"]["custom_profiles"]["残した"] == 1


def test_既定では取り込む側で上書きする(srv):
    """これまでの動きを変えないこと。"""
    payload = _clashing_payload(srv)
    res = _import(srv, payload)
    assert srv._load_custom()["p1"]["name"] == "取り込む側"
    assert res["merged"]["custom_profiles"]["上書き"] == 1


def test_重ならないものは残す指定でも取り込む(srv):
    """重なったものだけ避ければよく、他は普通に入ること。"""
    payload = _clashing_payload(srv)
    payload["data"]["custom_profiles"]["p9"] = {
        "name": "新しいの", "uuid": "0000000000000010",
        "roast": [[0, 180], [600, 235]], "fan": [[0, 70]], "cooldown": [620, 60]}
    _import(srv, payload, on_conflict="skip")
    got = srv._load_custom()
    assert set(got) == {"p1", "p9"}
    assert got["p1"]["name"] == "いまの" and got["p9"]["name"] == "新しいの"


def test_知らない重なりの扱いは断る(srv):
    """打ち間違いを黙って上書きとして扱わない。"""
    payload = _clashing_payload(srv)
    import asyncio

    class _Req:
        def __init__(self, body): self._body = body
        async def json(self): return self._body

    res = asyncio.run(srv.import_data(_Req(dict(payload, on_conflict="まぜる"))))
    assert res.status_code == 400
    assert srv._load_custom()["p1"]["name"] == "いまの", "断ったのに書き換わった"


def test_一覧に並べ替え用の数値が入る(srv):
    """概要欄の各カードで一覧を並べ替えるため、その数値が一覧にも要る。"""
    est = {"end_bean_temp": 220.0, "roast_index": 1.2, "roast_index_level": "中煎り",
           "energy_kcal": 9.0, "crack_start": 400.0}
    m = srv._list_metrics([[0, 185], [600, 240]], est)
    assert m["preheat_temp"] == 185
    assert m["energy_kcal"] == 9.0
    assert m["dev_time"] == 200.0, "1ハゼから終わりまでの時間になっていない"


def test_1ハゼに届かなければDevelopmentTimeは空(srv):
    """浅煎りで1ハゼ前に終えるカーブがある。0ではなく「無い」として扱う
    (0にすると並べ替えで最短として先頭に来てしまう)。"""
    est = {"energy_kcal": 5.0, "crack_start": None}
    assert srv._list_metrics([[0, 185], [400, 200]], est)["dev_time"] is None


def test_カーブが無ければ数値も空(srv):
    est = {"energy_kcal": None, "crack_start": None}
    m = srv._list_metrics([], est)
    assert m["preheat_temp"] is None and m["dev_time"] is None


def test_推定値の控えは形が変わると使わない(srv):
    """並べ替え用の項目を足したとき、古い控えをそのまま使うと
    その数値だけ空のまま一覧に出る。指紋に形の版を入れてある。"""
    src = srv._estimate_cache_fingerprint()
    assert isinstance(src, str) and len(src) == 64
    # 形の版が指紋に効いていること(版を変えたら指紋も変わる)
    import json
    import hashlib
    other = json.dumps({
        "cal": srv._calibration_overrides(),
        "moisture": srv._bean_moisture_frac(),
        "model": srv.energy_module.MODEL_VERSION,
        "shape": 1,
    }, ensure_ascii=False, sort_keys=True, default=str)
    assert hashlib.sha256(other.encode("utf-8")).hexdigest() != src


# ------------------------------------------------------------
# 焙煎日時の時差
# ------------------------------------------------------------
# roasted_at はUTCのISO文字列で保存している。日本の朝はUTCではまだ前日なので、
# 「今日」をUTCの日付1つで比べると境目がずれる(実際、朝9時前に焼いた分が
# 前日の焙煎と同じ日として扱われ、同日2回目の判定が効いていなかった)。
@contextlib.contextmanager
def _timezone(name):
    """この機械の時間帯を一時的に変える(検査を場所に依存させないため)。"""
    old = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if old is None: os.environ.pop("TZ", None)
        else: os.environ["TZ"] = old
        time.tzset()


def test_ローカルの1日はUTCの2日にまたがる(srv):
    import datetime
    with _timezone("Asia/Tokyo"):
        朝 = datetime.datetime(2026, 9, 9, 8, 26)      # 日本時間。UTCでは9/8 23:26
        days = srv._local_day_utc_prefixes(朝)
    assert "2026-09-08" in days, "日本の朝がUTCの前日として落ちている"
    assert "2026-09-09" in days


def test_時差の無い地域では1日だけ(srv):
    import datetime
    with _timezone("UTC"):
        days = srv._local_day_utc_prefixes(datetime.datetime(2026, 9, 9, 8, 26))
    assert days == {"2026-09-09"}


def test_手動追加の日時はUTCのISOで揃う(srv):
    """省略されたときにローカル時刻を素で入れると、他の記録と基準が食い違う。"""
    import asyncio
    import json as _json

    class _Req:
        def __init__(self, body): self._body = body
        async def json(self): return self._body

    res = asyncio.run(srv.create_roast_record(_Req({
        "profile_source": "preset", "profile_id": 1, "profile_name": "手で足した記録",
    })))
    rid = _json.loads(res.body)["id"]
    when = srv._load_roast_records()[rid]["roasted_at"]
    assert when.endswith("Z"), f"UTCの形になっていない: {when}"
    assert "T" in when
