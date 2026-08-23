#!/usr/bin/env python3
# ============================================================
# Roast Studio
# scripts/estimate_roast_energy.py
# ------------------------------------------------------------
# roastlib/energy.py のモデルを、プリセット(nhm.sqlite)全件で検証し、
# 定数を当てはめ直すためのツール。モデル本体はここには置かない
# (roastlib/energy.py が唯一の実装。画面側のJavaScriptとは
#  tests/test_energy.py で突き合わせている)。
#
# ■ 当てはめる定数と、それぞれの基準
#   U0     … ガイド1ハゼ温度を空気が横切った瞬間の豆温度が196℃
#   H_ENDO … 焙煎終了時の「空気温度 - 豆温度」が20℃(実務で言われる値)
#   K_PYRO … 中煎りの焙煎指数(生豆÷焙煎後)の中央値が1.1825(定義帯の中央)
#
#   3つとも独立した基準を持たせてある。特にH_ENDOの基準が要るのは、1ハゼで
#   内部水分が一気に気化して豆温度が196℃に張り付く(相変化の平坦域=ポップコーン
#   の原理そのもの)ため、U0を3割変えても1ハゼ時の豆温度がほとんど動かないから。
#
# ■ 検証結果(2026-08、174件)
#   1ハゼ時の豆温度 中央196.0℃・標準偏差4.2℃
#   終了時の空気-豆 中央19.7℃、豆が空気を超えた点 0
#   焙煎指数(中煎りだけ当てはめ、他3つは予測)
#       浅煎り1.142 / 中煎り1.183 / 中深煎り1.193 / 深煎り1.246
#       定義帯は 1.140-1.170 / 1.170-1.195 / 1.195-1.220 / 1.220以上
#
# ■ 分かったこと(表示を検討する上で)
#   ・総カロリーは終了温度と相関するが、それだけでは決まらない。最終温度と
#     焙煎時間の2変数で重回帰してもR²=0.889で、33%が説明されずに残る。
#     残差が一番相関するのは終盤のRoR(r=-0.458)。
#   ・終了直前の保持(ベイク)は、温度カーブがほぼ同じでも入熱が16%増え、
#     焙煎指数も1.179→1.242と動く。温度カーブだけでは見えない差。
#   ・風量50%→80%で総量は+9%、入熱速度の最大は+46%(終盤は逆転する)。
#
# 使い方:
#   venv/bin/python scripts/estimate_roast_energy.py           # 検証
#   venv/bin/python scripts/estimate_roast_energy.py --refit   # 定数を当てはめ直す
#   venv/bin/python scripts/estimate_roast_energy.py --sens    # 前提への感度
# ============================================================
import statistics as st
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import roastlib.energy as E  # noqa: E402
from analyze_ikawa_vs_presets import GUIDE, load_presets, rising_cross  # noqa: E402

BANDS = E.ROAST_INDEX_BANDS


def bean_at(x, t):
    s = E.estimate(x["roast"], x["fan"])["series"]
    return s[min(int(t), len(s) - 1)]["bean"]


def refit(profiles, rounds=2, iters=18):
    """3つの基準を順に満たす定数を求め、roastlib.energy に反映する。

    互いの結合は弱いので、数回まわせば落ち着く。当てはめは時間がかかるので
    プロファイルを間引いて使う(全件でも結果はほぼ同じことを確認済み)。
    """
    sub = profiles[::3]
    fc_t = [rising_cross(x["roast"], GUIDE["firstCrack"]) for x in sub]
    mid = [x for x in sub if x["level"] == "中煎り"]
    for _ in range(rounds):
        lo, hi = 0.0005, 0.02
        for _ in range(iters):
            E.U0 = (lo + hi) / 2
            v = [bean_at(x, t) for x, t in zip(sub, fc_t) if t is not None]
            lo, hi = (E.U0, hi) if st.median(v) < E.T_FC_BEAN else (lo, E.U0)
        E.U0 = (lo + hi) / 2
        lo, hi = 0.0, 20000.0
        for _ in range(iters):
            E.H_ENDO = (lo + hi) / 2
            g = st.median([E.estimate(x["roast"], x["fan"])["end_gap"] for x in sub])
            lo, hi = (E.H_ENDO, hi) if g < 20.0 else (lo, E.H_ENDO)
        E.H_ENDO = (lo + hi) / 2
        lo, hi = 1e0, 1e10
        for _ in range(30):
            E.K_PYRO = (lo + hi) / 2
            idx = [E.estimate(x["roast"], x["fan"])["roast_index"] for x in mid]
            lo, hi = (E.K_PYRO, hi) if st.median(idx) < 1.1825 else (lo, E.K_PYRO)
        E.K_PYRO = (lo + hi) / 2
    return E.U0, E.H_ENDO, E.K_PYRO


def main():
    P = load_presets()
    if not P:
        print("プリセット(nhm.sqlite)が無いため検証できません。")
        return
    if "--refit" in sys.argv:
        u0, h_endo, k_pyro = refit(P)
        print("当てはめ直した定数(roastlib/energy.py に手で反映すること):")
        print(f"   U0 = {u0:.5f}\n   H_ENDO = {h_endo:.1f}\n   K_PYRO = {k_pyro:.1f}\n")
    print(f"豆 {E.BEAN_G:.0f}g / 含水率 {E.MOISTURE * 100:.0f}% / "
          f"水分の{(1 - E.FREE_FRAC) * 100:.0f}%は1ハゼまで残る")
    print(f"U0={E.U0:.5f} kW/K  H_ENDO={E.H_ENDO:.0f} kJ/kg  K_PYRO={E.K_PYRO:.1f} /s\n")

    fc_t = [rising_cross(x["roast"], GUIDE["firstCrack"]) for x in P]
    fb, gaps, over, over_max = [], [], 0, 0.0
    rows = {}
    for x, t in zip(P, fc_t):
        r = E.estimate(x["roast"], x["fan"])
        if t is not None:
            fb.append(r["series"][min(int(t), len(r["series"]) - 1)]["bean"])
        gaps.append(r["end_gap"])
        for pt in r["series"]:
            if pt["bean"] > pt["air"] + 1e-9:
                over += 1
                over_max = max(over_max, pt["bean"] - pt["air"])
        rows.setdefault(x["level"], []).append(
            (r["total_kcal"], r["end_bean_temp"], r["roast_index"], x["roast"][-1][1]))

    print("■ 検証1 1ハゼ時のモデル豆温度(全件共通の定数ひとつ)")
    print(f"   n={len(fb)} 中央 {st.median(fb):.1f}℃ 標準偏差 {st.pstdev(fb):.1f}℃ (基準 {E.T_FC_BEAN:.0f}℃)")
    print(f"■ 検証2 終了時の 空気 - 豆: 中央 {st.median(gaps):.1f}℃ (基準 20℃)")
    print(f"■ 検証3 豆温度が空気温度を超えた点: {over}点"
          + (f"(最大 {over_max:.2f}℃)" if over else " ← 熱風焙煎では起きてはいけない"))
    print("\n■ 焙煎度別(中煎りだけ当てはめ、他3つは予測)")
    print("   焙煎度   積算入熱        終了豆温  空気との差  焙煎指数   定義帯")
    for name, lo, hi in BANDS:
        g = rows.get(name) or []
        if not g:
            continue
        idx = st.median([q[2] for q in g])
        mark = "○" if lo <= idx < hi else "△"
        hi_s = f"{hi:.3f}" if hi != float("inf") else "以上"
        print(f"   {name:5} {st.median([q[0] for q in g]):5.2f} kcal   "
              f"{st.median([q[1] for q in g]):6.1f}℃  {st.median([q[3] - q[1] for q in g]):6.1f}℃   "
              f"{idx:.3f}{mark}  {lo:.3f}〜{hi_s}")
    all_e = [q[0] for g in rows.values() for q in g]
    print(f"   全体 中央 {st.median(all_e):.2f} kcal")

    print("\n■ 風量はどれだけ効くか(同じ温度カーブで風量だけ固定)")
    base = [x for x in P if x["level"] == "中煎り"][0]
    for f in (50, 65, 80):
        flat = [[0, float(f)], [base["roast"][-1][0], float(f)]]
        r = E.estimate(base["roast"], flat)
        print(f"   風量{f}%: {r['total_kcal']:5.2f} kcal  終了豆温 {r['end_bean_temp']:.1f}℃")

    if "--sens" in sys.argv:
        print("\n■ 前提への感度(総入熱の中央値)")
        b = st.median(all_e)
        for tag, kw in [("含水率 8%", dict(moisture=0.08)), ("含水率 12%", dict(moisture=0.12)),
                        ("豆 40g", dict(bean_g=40.0)), ("豆 60g", dict(bean_g=60.0))]:
            v = st.median([E.estimate(x["roast"], x["fan"], **kw)["total_kcal"] for x in P])
            print(f"   {tag:12} {v:5.2f} kcal {(v - b) / b * 100:+6.1f}%")


if __name__ == "__main__":
    main()
