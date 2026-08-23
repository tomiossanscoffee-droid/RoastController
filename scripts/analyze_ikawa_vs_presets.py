#!/usr/bin/env python3
# ============================================================
# Roast Studio
# scripts/analyze_ikawa_vs_presets.py
# ------------------------------------------------------------
# IKAWAのプロファイル(ikawa_profiles.json)と、THE ROASTのプリセット
# (nhm.sqlite)を突き合わせ、「IKAWA側から生成器(profile_generator.py)へ
# 持ち込める要素があるか」を検証する。
#
# 2026-08の検証結果(このスクリプトが出す数字):
#   ・IKAWAの温度は一定オフセットではなく、約2倍に圧縮されている。
#     1次式は2通りの独立な方法で一致した(2点当てはめ2.02 / 作動幅の比2.04)。
#     ずれ幅はカラーチェンジ+3.9℃・1ハゼ+23.6℃・終了+34℃と、上ほど開く。
#     → 絶対温度(投入・ボトム・1ハゼ・終了)は補正しても持ち込めない。
#   ・総焙煎時間も持ち込めない(IKAWA中央378秒 / プリセット520秒)。
#   ・風量も持ち込めない。水準(80→67% / 74→57%)だけでなく形が違い、
#     IKAWAは前半ほぼ横ばい・後半低下(流動層の流動化のため)。
#   ・A:B:C比の一致は、1次式をτ_CC・τ_FCに当てはめた結果なので循環。
#   ・裏付けとしては有効: RoRの単調減少(IKAWA 45/58・プリセット159/174)、
#     終盤の正規化RoR(τ=0.75で0.50/0.43、τ=0.90で0.31/0.36)がほぼ一致。
#     Bフェーズ内RoR変化はIKAWA中央-5.7とフラット寄りで、ばらつきは約2倍。
#     生成器の調整幅(-14〜+14℃/分)を外れるのは53件中4件のみ。
#   → 結論: 生成器の定数を変える根拠は無い。設計思想の裏付けとして使う。
#
# 使い方:
#   venv/bin/python scripts/analyze_ikawa_vs_presets.py
#   venv/bin/python scripts/analyze_ikawa_vs_presets.py shape ror   # 節を指定
#
# データが無い環境(公開版など)では、その節をとばして続行する。
# ============================================================
import json
import os
import re
import sqlite3
import statistics as st
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

DB_PATH = Path(os.environ.get("ROAST_DB_PATH", REPO / "nhm.sqlite"))
IKAWA_PATH = Path(os.environ.get("ROAST_IKAWA_PATH", REPO / "ikawa_profiles.json"))

# THE ROASTの温度系での基準。ユーザーの温度ガイド線設定と同じ意味。
GUIDE = {"colorChange": 184, "firstCrack": 223, "secondCrack": 227}
# IKAWA→THE ROAST の1次式(このスクリプトの map 節で求めた値)。
IKAWA_TO_ROAST_A, IKAWA_TO_ROAST_B = 2.02, -180.5


# ------------------------------------------------------------ 読み込み
def _pairs(s):
    v = [float(x) for x in s.split(",")] if s else []
    return [[v[i], v[i + 1]] for i in range(0, len(v) - 1, 2)]


def load_presets():
    """プリセット(nhm.sqlite)。無ければ空リスト。"""
    if not DB_PATH.exists():
        return []
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    out, names = [], {}
    for r in con.execute("SELECT * FROM profile"):
        pts = _pairs(r["roastPoints"])
        if len(pts) < 3:
            continue
        names[r["name"]] = r["id"]
        out.append({"name": r["name"], "roast": pts, "fan": _pairs(r["fanPoints"]), "level": ""})
    # 焙煎度はアプリ本体と同じ判定を使う(名前→id→compute_roast_levels)。
    try:
        from app import server as S
        levels = S.get_roast_levels()
        for p in out:
            p["level"] = levels.get(int(names.get(p["name"], -1)), "")
    except Exception as e:  # noqa: BLE001
        print(f"  (焙煎度の判定をとばしました: {e!r})")
    return out


def load_ikawa():
    if not IKAWA_PATH.exists():
        return []
    d = json.loads(IKAWA_PATH.read_text(encoding="utf-8"))
    return [{"name": x["name"], "category": x["category"], "desc": x.get("description") or "",
             "roast": [list(map(float, p)) for p in x["roast"]],
             "fan": [list(map(float, p)) for p in x["fan"]]} for x in d]


# ------------------------------------------------------------ 小道具
def interp(pts, t):
    if t <= pts[0][0]:
        return pts[0][1]
    if t >= pts[-1][0]:
        return pts[-1][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


def ror(pts, t, win=30.0):
    """t前後win秒での上昇率(℃/分)。一定の温度オフセットでは変わらない指標。"""
    a = max(pts[0][0], t - win / 2)
    b = min(pts[-1][0], t + win / 2)
    if b <= a:
        return None
    return (interp(pts, b) - interp(pts, a)) / (b - a) * 60.0


def rising_cross(pts, target):
    """上昇中にtarget℃を横切る時刻。届かなければNone。"""
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if v0 <= target <= v1 and v1 > v0:
            return t0 + (t1 - t0) * (target - v0) / (v1 - v0)
    return None


def lowpt(x):
    """上昇の起点。プリセットはボトム(投入直後の谷)、IKAWAは多くが開始点そのもの。"""
    lo = min(x["roast"], key=lambda p: p[1])
    return lo[0], lo[1]


def mapped_roast(x):
    """IKAWAの曲線をTHE ROASTの温度系へ写す。"""
    return [[t, IKAWA_TO_ROAST_A * v + IKAWA_TO_ROAST_B] for t, v in x["roast"]]


def q3(v):
    v = sorted(v)
    qq = st.quantiles(v, n=4)
    return f"中央{st.median(v):7.1f}  四分位 {qq[0]:7.1f}〜{qq[2]:7.1f}  幅{qq[2] - qq[0]:6.1f}"


LEVELS = ["浅煎り", "中煎り", "中深煎り", "深煎り"]


# ------------------------------------------------------------ 各節
def sec_basic(P, I):
    print("■ 基本統計")
    for tag, S_ in (("プリセット", P), ("IKAWA", I)):
        print(f"  {tag:8} n={len(S_):3}")
        print(f"    総時間(秒) {q3([x['roast'][-1][0] for x in S_])}")
        print(f"    投入温度   {q3([x['roast'][0][1] for x in S_])}")
        print(f"    終了温度   {q3([x['roast'][-1][1] for x in S_])}")
    if P:
        print("  プリセット 焙煎度別の終了温度・総時間")
        for lv in LEVELS:
            g = [p for p in P if p["level"] == lv]
            if not g:
                continue
            print(f"    {lv:5} n={len(g):3} 終了{st.median([x['roast'][-1][1] for x in g]):5.0f}℃ "
                  f"総時間{st.median([x['roast'][-1][0] for x in g]):5.0f}秒")


def sec_offset(P, I):
    """一定オフセットΔでは説明できないことを確認する。"""
    print("■ 温度オフセットΔの感度分析(Δ=THE ROAST - IKAWA と仮定)")
    print("   Δ  1ハゼ相当  到達数  τ_1ハゼ  デベロップメント割合")
    for delta in (0, 5, 10, 15, 20, 25, 30, 35, 40):
        fc = GUIDE["firstCrack"] - delta
        tau, dtr = [], []
        for x in I:
            t = rising_cross(x["roast"], fc)
            if t is None:
                continue
            tE = x["roast"][-1][0]
            tau.append(t / tE)
            dtr.append((tE - t) / tE)
        if len(dtr) < 5:
            print(f"  {delta:3}    {fc:5.0f}    {len(dtr):3}/{len(I)}   —")
            continue
        print(f"  {delta:3}    {fc:5.0f}    {len(dtr):3}/{len(I)}   {st.median(tau):.3f}   {st.median(dtr) * 100:5.1f}%")
    print("   → カラーチェンジ側を合わせるとΔ≈0〜5、1ハゼ側を合わせるとΔ≈25〜30。両立しない。")
    if P:
        for lv in LEVELS:
            v = [(x["roast"][-1][0] - rising_cross(x["roast"], GUIDE["firstCrack"])) / x["roast"][-1][0]
                 for x in P if x["level"] == lv and rising_cross(x["roast"], GUIDE["firstCrack"])]
            if v:
                print(f"     参考 プリセット{lv:5} デベロップメント割合 中央 {st.median(v) * 100:5.1f}%")


def sec_map(P, I):
    """カラーチェンジ・1ハゼの2点から1次式を求め、傾きが1から離れることを示す。"""
    print("■ IKAWA→THE ROAST の1次式")

    def median_tau_at(S_, temp):
        v = [rising_cross(x["roast"], temp) / x["roast"][-1][0]
             for x in S_ if rising_cross(x["roast"], temp) is not None]
        return (st.median(v), len(v)) if v else (None, 0)

    def solve(S_, target, lo, hi):
        for _ in range(60):
            mid = (lo + hi) / 2
            m, n = median_tau_at(S_, mid)
            if m is None or n < len(S_) * 0.7:
                hi = mid
                continue
            lo, hi = (mid, hi) if m < target else (lo, mid)
        return (lo + hi) / 2

    tau_cc, _ = median_tau_at(P, GUIDE["colorChange"])
    tau_fc, _ = median_tau_at(P, GUIDE["firstCrack"])
    print(f"  プリセット τ_CC={tau_cc:.3f}({GUIDE['colorChange']}℃) τ_1ハゼ={tau_fc:.3f}({GUIDE['firstCrack']}℃)")
    i_cc, i_fc = solve(I, tau_cc, 100, 230), solve(I, tau_fc, 100, 230)
    a = (GUIDE["firstCrack"] - GUIDE["colorChange"]) / (i_fc - i_cc)
    b = GUIDE["colorChange"] - a * i_cc
    print(f"  IKAWA側で同じτになる温度: CC相当 {i_cc:.1f}℃ / 1ハゼ相当 {i_fc:.1f}℃")
    print(f"  → T_roast = {a:.2f} × T_ikawa + {b:.1f}   (1.00なら一定オフセット)")
    print(f"     ずれ幅: CCで{GUIDE['colorChange'] - i_cc:+.1f}℃ / 1ハゼで{GUIDE['firstCrack'] - i_fc:+.1f}℃")
    spP = st.median([x["roast"][-1][1] - min(p[1] for p in x["roast"]) for x in P])
    spI = st.median([x["roast"][-1][1] - min(p[1] for p in x["roast"]) for x in I])
    print(f"  作動温度幅 プリセット{spP:.0f}℃ / IKAWA{spI:.0f}℃ → 比 {spP / spI:.2f}(2点当てはめの傾きと一致)")


def sec_shape(P, I):
    """到達割合fに達する時刻の割合τ(f)。オフセット・時間尺度の両方に不変。"""
    print("■ 形の比較 τ(f)= 上昇量のf割に達する時刻 / 上昇時間")
    FR = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]

    def tau_curve(x):
        t0, T0 = lowpt(x)
        tE, TE = x["roast"][-1]
        span, dur = TE - T0, tE - t0
        if span <= 20 or dur <= 60:
            return None
        out = []
        pts = [p for p in x["roast"] if p[0] >= t0]
        for f in FR:
            target = T0 + span * f
            tt = None
            for (ta, va), (tb, vb) in zip(pts, pts[1:]):
                if va <= target <= vb and vb > va:
                    tt = ta + (tb - ta) * (target - va) / (vb - va)
                    break
            if tt is None:
                return None
            out.append((tt - t0) / dur)
        return out

    print("  f            " + "".join(f"{f:>7.1f}" for f in FR))
    groups = [("プリセット", P)] + [(f"  {lv}", [p for p in P if p["level"] == lv]) for lv in LEVELS] + [("IKAWA", I)]
    for tag, S_ in groups:
        cs = [c for c in (tau_curve(x) for x in S_) if c]
        if not cs:
            continue
        print(f"  {tag:10}({len(cs):3})" + "".join(f"{st.median([c[i] for c in cs]):7.3f}" for i in range(len(FR))))


def sec_ror(P, I):
    print("■ RoRの形(一定オフセットに不変)")
    KEYS = [("r10", 0.10), ("r25", 0.25), ("r50", 0.50), ("r75", 0.75), ("r90", 0.90)]

    def metrics(x):
        t0, T0 = lowpt(x)
        tE, TE = x["roast"][-1]
        dur, span = tE - t0, TE - T0
        if dur < 90 or span < 20:
            return None
        m = {"mean_ror": span / dur * 60}
        for tag, f in KEYS:
            m[tag] = ror(x["roast"], t0 + dur * f, win=max(30, dur * 0.12))
        return m

    print("  τ=            0.10  0.25  0.50  0.75  0.90   (平均RoRに対する比)")
    for tag, S_ in [("プリセット", P)] + [(f"  {lv}", [p for p in P if p["level"] == lv]) for lv in LEVELS] + [("IKAWA", I)]:
        ms = [m for m in (metrics(x) for x in S_) if m and all(m[k] is not None for k, _ in KEYS)]
        if not ms:
            continue
        rel = [st.median([m[k] / m["mean_ror"] for m in ms]) for k, _ in KEYS]
        print(f"  {tag:10}({len(ms):3}) " + " ".join(f"{v:5.2f}" for v in rel))
    print("  単調に減少しているか(区間ごとに前より上がった回数=0の割合)")
    for tag, S_ in (("プリセット", P), ("IKAWA", I)):
        n = 0
        tot = 0
        for x in S_:
            m = metrics(x)
            if not m or any(m[k] is None for k, _ in KEYS):
                continue
            seq = [m[k] for k, _ in KEYS]
            tot += 1
            if all(b <= a + 0.5 for a, b in zip(seq, seq[1:])):
                n += 1
        if tot:
            print(f"    {tag:8} {n}/{tot}")


def sec_fan(P, I):
    print("■ 風量(%という同じ単位なので、温度オフセットの影響を受けない)")
    for tag, S_ in (("プリセット", P), ("IKAWA", I)):
        if not S_:
            continue
        print(f"  {tag:8} 最大{st.median([max(p[1] for p in x['fan']) for x in S_]):5.1f}% "
              f"→ 終了{st.median([x['fan'][-1][1] for x in S_]):5.1f}%")
    TS = [0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    print("  τ        " + "".join(f"{t:>6.2f}" for t in TS))
    for tag, S_ in (("プリセット", P), ("IKAWA", I)):
        if not S_:
            continue
        med = [st.median([interp(x["fan"], x["roast"][-1][0] * t) for x in S_ if len(x["fan"]) >= 2]) for t in TS]
        print(f"  {tag:8} " + "".join(f"{m:6.1f}" for m in med))
    print("  → IKAWAはτ=0.3までほぼ横ばい(流動層の流動化)、プリセットは最初から直線的に低下。")


def sec_abc(P, I):
    """写像後のIKAWAを本番のanalyze_abc_phases()にかけ、プリセットと比べる。"""
    from roastlib.profile_generator import analyze_abc_phases, ABC_LIMITS
    print("■ A/B/Cフェーズ(IKAWAは1次式でTHE ROASTの温度系へ写してから分割)")
    print("  ※ 1次式はτ_CC・τ_FCをプリセット中央値に合わせたものなので、")
    print("     A:B:Cの『割合』とデベロップメント割合の一致は循環。独立に意味があるのは")
    print("     Bフェーズ内のRoR変化・ばらつき・絶対時間。")
    pres = [r for r in (analyze_abc_phases(x["roast"], GUIDE) for x in P) if r]
    ika = [r for r in (analyze_abc_phases(mapped_roast(x), GUIDE) for x in I) if r]
    print(f"  分割できた数: プリセット {len(pres)}/{len(P)}  IKAWA {len(ika)}/{len(I)}")
    for key, label in (("a_sec", "Aフェーズ(投入→CC)"), ("b_sec", "Bフェーズ(CC→1ハゼ)"),
                       ("c_sec", "Cフェーズ(1ハゼ→終了)")):
        for tag, S_ in (("プリセット", pres), ("IKAWA", ika)):
            v = [r[key] for r in S_ if r.get(key) is not None]
            if v:
                print(f"  {label:22} {tag:8} n={len(v):3} {q3(v)} 秒")
        lo, hi = ABC_LIMITS[key]
        v = [r[key] for r in ika if r.get(key) is not None]
        if v:
            print(f"  {'':22} 生成器の可動域 {lo}〜{hi}秒 / IKAWA実測 {min(v):.0f}〜{max(v):.0f}秒")
    print("  Bフェーズ内のRoR変化(後半−前半、℃/分。写像で温度が約2倍になる点に注意)")
    for tag, S_ in (("プリセット", pres), ("IKAWA", ika)):
        v = [r["b_ror_diff"] for r in S_ if r.get("b_ror_diff") is not None]
        if v:
            outside = sum(1 for x in v if abs(x) > 14)
            print(f"    {tag:8} n={len(v):3} {q3(v)}  調整幅±14の外 {outside}件")
    print("    生成器の既定 ABC_ROR_DIFF_TARGET[-1] = -8.0 ℃/分")


def sec_usage(P, I):
    """IKAWAの用途別(カッピング/エスプレッソ/フィルター)に差が出るか。"""
    from roastlib.profile_generator import analyze_abc_phases
    print("■ IKAWA 用途別(名前・説明・カテゴリから分類、重複あり)")
    pat = {"カッピング/サンプル": r"cupping|sample|taste of harvest|screening",
           "エスプレッソ/競技": r"espresso|wbc|ikrc|barista|champion",
           "フィルター/オムニ": r"filter|omni|brew|pour"}
    for tag, rx in pat.items():
        g = [x for x in I if re.search(rx, (x["name"] + " " + x["desc"] + " " + x["category"]).lower())]
        rs = [r for r in (analyze_abc_phases(mapped_roast(x), GUIDE) for x in g) if r]
        if len(rs) < 4:
            continue
        tot = [r["a_sec"] + r["b_sec"] + (r.get("c_sec") or 0) for r in rs]
        cr = [(r.get("c_sec") or 0) / t for r, t in zip(rs, tot)]
        bd = [r["b_ror_diff"] for r in rs if r.get("b_ror_diff") is not None]
        print(f"  {tag:16} n={len(rs):2} 総時間{st.median(tot):5.0f}秒 "
              f"C割合{st.median(cr) * 100:5.1f}% B内RoR変化{st.median(bd):+5.1f}")
    print("  → 差はほとんど無い。IKAWAは58件すべて終了203〜210℃の浅〜中煎り一色で、")
    print("     焙煎度の幅が無いため味の3軸に載せられる情報が入っていない。")


SECTIONS = [("basic", sec_basic, "both"), ("offset", sec_offset, "both"), ("map", sec_map, "both"),
            ("shape", sec_shape, "both"), ("ror", sec_ror, "both"), ("fan", sec_fan, "both"),
            ("abc", sec_abc, "both"), ("usage", sec_usage, "ikawa")]


def main():
    want = set(sys.argv[1:])
    P, I = load_presets(), load_ikawa()
    print(f"プリセット {len(P)}件 ({DB_PATH})")
    print(f"IKAWA     {len(I)}件 ({IKAWA_PATH})")
    if not I:
        print("\nIKAWAのプロファイルが無いため、比較はできません。")
        print("scripts/extract_ikawa_profiles.py で取り込んでから実行してください。")
        return
    for name, fn, need in SECTIONS:
        if want and name not in want:
            continue
        if need == "both" and not P:
            print(f"\n── {name}: プリセット(nhm.sqlite)が無いためとばします")
            continue
        print()
        fn(P, I)


if __name__ == "__main__":
    main()
