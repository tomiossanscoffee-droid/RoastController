# -*- coding: utf-8 -*-
"""豆への入熱・豆温度の推定(roastlib/energy.py)のテスト。

画面側(app/static/index.html の estimateRoastEnergy)にも同じ計算があるため、
最後のテストでJavaScript側を実際に動かして数値を突き合わせている
(Deno または node がある環境でのみ実行し、無ければskip)。
"""
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from roastlib.energy import ROAST_INDEX_BANDS, estimate, latent_heat, roast_index_level

REPO = Path(__file__).resolve().parent.parent

# 実機プリセットに近い形のカーブ(投入190℃→ボトム→上昇→238℃で終了)
ROAST = [[0, 190], [60, 100], [120, 155], [180, 185], [300, 210], [450, 230], [515, 238]]
FAN = [[0, 50], [1, 80], [6, 76], [515, 56]]


# 標高による1ハゼ豆温度の補正。JS側と同じ答えになることを突き合わせる。
ALT_BUCKETS = ["1000m未満", "1000-1500m", "1500-2000m", "2000m以上", "指定なし", ""]
ALT_CASES = [[b, s] for b in ALT_BUCKETS for s in (0, 2, 4, -3)]


def _altitude_offsets():
    import roastlib.energy as E
    return [E.altitude_fc_offset(b, s) for b, s in ALT_CASES]


def test_短すぎるカーブはNoneを返す():
    assert estimate([], []) is None
    assert estimate([[0, 190]], []) is None
    assert estimate([[0, 190], [0, 200]], []) is None


def test_基本的な出力の形と桁():
    r = estimate(ROAST, FAN)
    assert r is not None
    # 50g・含水率10%で、豆に入る熱は7〜11kcalの範囲に収まるはず
    assert 7.0 < r["total_kcal"] < 11.0
    assert math.isclose(r["total_kj"], r["total_kcal"] * 4.184, rel_tol=1e-9)
    # 焙煎後の重量は生豆より軽く、焙煎指数は1.1〜1.3程度
    assert 38.0 < r["roasted_g"] < 46.0
    assert 1.10 < r["roast_index"] < 1.30
    assert r["roast_index_level"] in [b[0] for b in ROAST_INDEX_BANDS]
    # 時系列は0秒から終了時刻まで、1秒刻み
    s = r["series"]
    assert s[0]["t"] == 0 and s[0]["bean"] == 20.0 and s[0]["kcal"] == 0.0
    assert s[-1]["t"] == ROAST[-1][0]
    assert len(s) == ROAST[-1][0] + 1


def test_豆温度は空気温度を超えない():
    """熱風焙煎では起こり得ないため、モデルでも起きてはいけない。"""
    r = estimate(ROAST, FAN)
    for pt in r["series"]:
        assert pt["bean"] <= pt["air"] + 1e-6, f"t={pt['t']} で豆が空気を超えた"


def test_積算入熱は単調に増える():
    r = estimate(ROAST, FAN)
    vals = [pt["kcal"] for pt in r["series"]]
    assert all(b >= a - 1e-12 for a, b in zip(vals, vals[1:]))


def test_1ハゼで豆温度の上昇が鈍る():
    """細胞内の水分が気化するため、196℃付近で上昇が鈍る(ポップコーンの原理)。

    ただし止まったり下がったりはしない。実測では「上がりにくくなるが緩やかに
    上昇し続ける」ため、鈍ることと上がり続けることの両方を確かめる。
    """
    r = estimate(ROAST, FAN)
    s = r["series"]
    idx = next((i for i, p in enumerate(s) if p["bean"] >= 196.0), None)
    assert idx is not None, "1ハゼ豆温度に届いていない"
    before = s[idx]["bean"] - s[max(idx - 30, 0)]["bean"]
    after = s[min(idx + 30, len(s) - 1)]["bean"] - s[idx]["bean"]
    assert after < before, "1ハゼで鈍っていない"
    assert after > 0.0, "1ハゼで豆温度が上がらなくなっている"


def test_1ハゼの後に急な跳ね返りが出ない():
    """実測のRoR曲線では、急な落ち込み(crash)も跳ね返り(flick)も欠陥とされる。

    豆が100℃を超えて以降で「いったん下がってから上がり直した幅」を測る。
    切り替えのある蒸発の式を使っていた頃はここが22.9℃/分もあった。
    """
    s = estimate(ROAST, FAN)["series"]
    start = next((i for i, p in enumerate(s) if p["bean"] >= 100.0), None)
    assert start is not None
    win = 15
    rors = [(s[i + win]["bean"] - s[i]["bean"]) / win * 60
            for i in range(start, len(s) - win)]
    lowest, rebound = rors[0], 0.0
    for v in rors:
        lowest = min(lowest, v)
        rebound = max(rebound, v - lowest)
    assert rebound < 8.0, f"跳ね返りが大きすぎます: {rebound:.1f}℃/分"


def test_終了時の空気と豆の差はおおよそ20度():
    r = estimate(ROAST, FAN)
    assert 10.0 < r["end_gap"] < 30.0


def test_最終温度が高いほど入熱が増える():
    hot = [[t, v + (10 if t > 250 else 0)] for t, v in ROAST]
    assert estimate(hot, FAN)["total_kcal"] > estimate(ROAST, FAN)["total_kcal"]


def test_焙煎時間が長いほど入熱が増える():
    slow = [[t * 1.15, v] for t, v in ROAST]
    slow_fan = [[t * 1.15, v] for t, v in FAN]
    assert estimate(slow, slow_fan)["total_kcal"] > estimate(ROAST, FAN)["total_kcal"]


def test_終了直前の保持で入熱と焙煎指数が増える():
    """温度カーブの形はほぼ同じでも、いわゆるベイクでは熱が入り続ける。"""
    base = estimate(ROAST, FAN)
    baked_roast = ROAST + [[ROAST[-1][0] + 120, ROAST[-1][1]]]
    baked_fan = FAN + [[FAN[-1][0] + 120, FAN[-1][1]]]
    baked = estimate(baked_roast, baked_fan)
    assert baked["total_kcal"] > base["total_kcal"] * 1.05
    assert baked["roast_index"] > base["roast_index"]


def test_風量が多いほど入熱が増える():
    lo = estimate(ROAST, [[0, 50], [ROAST[-1][0], 50]])
    hi = estimate(ROAST, [[0, 80], [ROAST[-1][0], 80]])
    assert hi["total_kcal"] > lo["total_kcal"]


def test_豆の量に比例する():
    a = estimate(ROAST, FAN, bean_g=50.0)
    b = estimate(ROAST, FAN, bean_g=25.0)
    # 熱容量が半分になるぶん豆が速く温まるので厳密な比例ではないが、
    # 半分の豆で倍の熱が入ることはない
    assert b["total_kcal"] < a["total_kcal"]


def test_含水率の設定範囲いっぱいで壊れない():
    """含水率5〜15%(設定の上下限)で値が破綻しないこと。

    豆の量は焙煎機の仕様どおり50g固定で設定項目にはしていないが、引数としては
    受けるので(感度検証のスクリプトが使う)、極端な量でも壊れないことも見る。
    """
    for g in (10.0, 50.0, 100.0):
        for m in (0.05, 0.10, 0.15):
            r = estimate(ROAST, FAN, bean_g=g, moisture=m)
            assert r is not None
            assert r["total_kcal"] > 0
            assert 1.0 < r["roast_index"] < 2.0, f"{g}g/{m}: 指数{r['roast_index']}"
            assert 0 < r["roasted_g"] < g
            for pt in r["series"]:
                assert pt["bean"] <= pt["air"] + 1e-6, f"{g}g/{m}: 豆が空気を超えた"


def test_含水率が高いほど入熱が増える():
    """水を余分に蒸発させる分だけ熱が要る。"""
    dry = estimate(ROAST, FAN, moisture=0.08)
    wet = estimate(ROAST, FAN, moisture=0.12)
    assert wet["total_kcal"] > dry["total_kcal"]


def test_風量カーブが無くても計算できる():
    r = estimate(ROAST, None)
    assert r is not None and r["total_kcal"] > 0


def test_蒸発潜熱は高温ほど小さい():
    assert latent_heat(100.0) > latent_heat(196.0) > latent_heat(250.0)
    assert 2200 < latent_heat(100.0) < 2300


@pytest.mark.parametrize("index,expected", [
    (1.10, "浅煎り"), (1.145, "浅煎り"), (1.18, "中煎り"),
    (1.20, "中深煎り"), (1.25, "深煎り"), (1.50, "深煎り"),
])
def test_焙煎指数から焙煎度(index, expected):
    assert roast_index_level(index) == expected


# ------------------------------------------------------------
# 画面側(JavaScript)との突き合わせ
# ------------------------------------------------------------
def _js_runtime():
    for exe, args in (("deno", ["eval"]), ("node", ["-e"])):
        if shutil.which(exe):
            return exe, args
    return None, None


def test_JavaScript側と同じ数値になる():
    """index.htmlのestimateRoastEnergy()を実際に動かして結果を比べる。

    片方だけ直して数値がずれるのを防ぐためのテスト。
    JavaScriptを実行できない環境ではskipする。
    """
    exe, args = _js_runtime()
    if not exe:
        pytest.skip("deno も node も無いため、JavaScript側との突き合わせをとばします")
    html = (REPO / "app/static/index.html").read_text(encoding="utf-8")
    # 必要な部分(定数と4つの関数)だけを取り出す
    parts = []
    for pat in (r"const ENERGY = \{.*?\n\};",
                r"function energyInterp\(points, t\)\{.*?\n\}",
                r"function energyLatentHeat\(tC\)\{.*?\n\}",
                r"function energySatPressureRel\(tC\)\{.*?\n\}",
                r"function energyBurstFraction\(tB, E\)\{.*?\n\}",
                r"function energyAltitudeFcOffset\(bucket, slope\)\{.*?\n\}",
                r"const ROAST_INDEX_BANDS = .*?\n\}",
                r"function estimateRoastEnergy\(roastPoints, fanPoints\)\{.*?\n\}"):
        m = re.search(pat, html, re.S)
        assert m, f"index.htmlから切り出せませんでした: {pat}"
        parts.append(m.group(0))
    script = "\n".join(parts) + f"""
const r = estimateRoastEnergy({json.dumps(ROAST)}, {json.dumps(FAN)});
console.log(JSON.stringify({{
  totalKcal: r.totalKcal, roastIndex: r.roastIndex, endBeanTemp: r.endBeanTemp,
  level: r.roastIndexLevel, n: r.series.length,
  crackStart: r.crackStart, crackEnd: r.crackEnd,
  altOffsets: {json.dumps(ALT_CASES)}.map(([b, s]) => energyAltitudeFcOffset(b, s)),
  mid: r.series[Math.floor(r.series.length/2)],
}}));
"""
    out = subprocess.run([exe, *args, script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    js = json.loads(out.stdout.strip().splitlines()[-1])
    py = estimate(ROAST, FAN)
    assert js["n"] == len(py["series"])
    assert math.isclose(js["totalKcal"], py["total_kcal"], rel_tol=1e-9)
    assert math.isclose(js["roastIndex"], py["roast_index"], rel_tol=1e-9)
    assert js["crackStart"] == py["crack_start"]
    assert js["crackEnd"] == py["crack_end"]
    for got, want in zip(js["altOffsets"], _altitude_offsets()):
        assert math.isclose(got, want, abs_tol=1e-9)
    assert math.isclose(js["endBeanTemp"], py["end_bean_temp"], rel_tol=1e-9)
    assert js["level"] == py["roast_index_level"]
    mid_py = py["series"][len(py["series"]) // 2]
    assert math.isclose(js["mid"]["bean"], mid_py["bean"], rel_tol=1e-9)
    assert math.isclose(js["mid"]["kcal"], mid_py["kcal"], rel_tol=1e-9)
