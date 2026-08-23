#!/usr/bin/env python3
# ============================================================
# Roast Studio
# scripts/estimate_roast_energy.py
# ------------------------------------------------------------
# 焙煎プロファイル(温度カーブ+風量カーブ)から、豆が受け取った熱量
# (積算カロリー)を推定するモデルと、プリセット174件での検証。
#
# ■ モデル
#   豆 50g / 含水率10% → 乾物45g・水5g。焙煎終了時の残水分はほぼゼロ。
#   状態量: 豆温度 T_b, 残り水分 w
#       対流入熱 q  = U(風量) × (T_空気 - T_b)
#       蒸発     qe = (乾燥速度) × 潜熱     ※T_b > 100℃ のときだけ
#       dT_b/dt = (q - qe) / (乾物×比熱 + w×水の比熱)
#   未知数は熱伝達係数 U0 の1つだけ。これを「ユーザーのガイド1ハゼ温度を
#   空気が横切った瞬間に、モデル上の豆温度が196℃になる」ように当てはめる。
#   発熱反応の項は入れていない。文献値(乾物1kgあたり100〜250kJ)を素直に
#   入れると1ハゼ以降の発熱が対流入熱と同程度になり、豆温度が空気温度を
#   追い越してしまう(熱風焙煎では起こり得ない)ため。
#
# ■ 検証結果(2026-08、プリセット174件)
#   ・1ハゼ時のモデル豆温度: 全件共通のU0ひとつで 中央196℃・標準偏差8.9℃
#   ・積算入熱は焙煎度の順に単調増加 6.98 / 7.36 / 7.44 / 7.65 kcal
#   ・総入熱の中央値 30.8kJ = 7.36kcal。うち39%(12.0kJ)は水の蒸発
#
# ■ 分かったこと(表示を検討する上で重要)
#   ・総カロリーは終了温度とr=+0.971。風量を一律65%にしても総量は±0.6%しか
#     動かない(順位相関0.9987)。これはモデルの粗さではなくエネルギー保存則:
#     豆に入った熱=豆のエンタルピー変化=終了豆温度と蒸発量でほぼ決まる。
#   ・一方「入熱速度(W)」は風量に強く反応する。同じ温度カーブでも
#     最大入熱は風量50%で224W・80%で326W。終盤は逆転(27.7W→16.8W)。
#   ・積算カーブの「形」は温度カーブとは違う。乾燥に熱を食われる前半で遅れ、
#     τ=0.2では豆温度が41%進んでいるのに積算は28%しか進まない。
#   ・前提への感度: 総量は含水率±2%で±8.6%、比熱1.4〜2.0で±9%、豆の量に比例。
#     曲線の形はほぼ動かない(τ=0.3の積算割合 0.37〜0.41)。
#     → 絶対値は±15%程度の推定値、形は信頼できる。
#
# 使い方:
#   venv/bin/python scripts/estimate_roast_energy.py            # 検証を実行
#   venv/bin/python scripts/estimate_roast_energy.py --sens     # 前提の感度も見る
# ============================================================
import os
import statistics as st
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from analyze_ikawa_vs_presets import GUIDE, interp, load_presets, rising_cross  # noqa: E402

# ---- 豆の前提(50g・含水率10%) ----
BEAN_G = 50.0
MOISTURE = 0.10
C_DRY = 1.70      # 乾物の比熱 kJ/(kg·K)
C_W = 4.18        # 水の比熱
L_VAP = 2400.0    # 蒸発潜熱 kJ/kg(結合水なので純水2260より大きめ)
T_FC_BEAN = 196.0  # 1ハゼの豆温度(当てはめの基準)
T_START = 20.0     # 投入時の豆温度
K_DRY = 2.0e-4     # 乾燥速度係数(1ハゼ付近で抜け切り、終了時ほぼ0になる値)
FAN_REF, FAN_EXP = 70.0, 0.8   # 強制対流 Nu ∝ Re^0.8 相当


def simulate(roast, fan, u0, bean_g=BEAN_G, moisture=MOISTURE, dt=0.5,
             c_dry=C_DRY, l_vap=L_VAP, k_dry=K_DRY):
    """1本のプロファイルを積分する。

    戻り値: [(t, 豆温度, 積算入熱kJ, 入熱速度W, 蒸発に使われた速度W, 残水分kg), ...]
    """
    m = bean_g / 1000.0
    m_dry, w = m * (1 - moisture), m * moisture
    t_end = roast[-1][0]
    t_b, e_tot = T_START, 0.0
    out = []
    t = 0.0
    while t <= t_end + 1e-9:
        t_air = interp(roast, t)
        f = interp(fan, t) if fan else FAN_REF
        u = u0 * (max(f, 1.0) / FAN_REF) ** FAN_EXP
        q = u * (t_air - t_b)                       # kW(負なら放熱)
        rate = min(k_dry * w * max(t_b - 100.0, 0.0), w / dt) if w > 0 else 0.0
        q_lat = rate * l_vap
        t_b += (q - q_lat) / (m_dry * c_dry + w * C_W) * dt
        w -= rate * dt
        e_tot += max(q, 0.0) * dt
        out.append((t, t_b, e_tot, max(q, 0.0) * 1000, q_lat * 1000, w))
        t += dt
    return out


def at_time(res, t):
    for a, b in zip(res, res[1:]):
        if a[0] <= t <= b[0]:
            r = (t - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0.0
            return [a[i] + (b[i] - a[i]) * r for i in range(len(a))]
    return list(res[-1])


def fit_u0(profiles, **kw):
    """全プロファイル共通の熱伝達係数U0を、1ハゼ時の豆温度が196℃になるよう決める。"""
    fc_t = [rising_cross(x["roast"], GUIDE["firstCrack"]) for x in profiles]
    lo, hi = 0.0002, 0.02
    for _ in range(40):
        u0 = (lo + hi) / 2
        v = [at_time(simulate(x["roast"], x["fan"], u0, **kw), t)[1]
             for x, t in zip(profiles, fc_t) if t is not None]
        lo, hi = (u0, hi) if st.median(v) < T_FC_BEAN else (lo, u0)
    return (lo + hi) / 2


def kcal(kj):
    return kj / 4.184


def main():
    P = load_presets()
    if not P:
        print("プリセット(nhm.sqlite)が無いため検証できません。")
        return
    u0 = fit_u0(P)
    print(f"豆 {BEAN_G:.0f}g / 含水率 {MOISTURE * 100:.0f}% / 終了時の残水分ほぼ0")
    print(f"当てはめた熱伝達係数 U0 = {u0:.5f} kW/K(風量{FAN_REF:.0f}%基準)\n")

    fb, tot, lat, endt = [], [], [], []
    per_lv = {}
    for x in P:
        r = simulate(x["roast"], x["fan"], u0)
        t = rising_cross(x["roast"], GUIDE["firstCrack"])
        if t is not None:
            fb.append(at_time(r, t)[1])
        e = r[-1][2]
        e_lat = sum(s[4] / 1000 * 0.5 for s in r)
        tot.append(e)
        lat.append(e_lat)
        endt.append(r[-1][1])
        per_lv.setdefault(x["level"], []).append((e, r[-1][1]))

    print("■ 検証1 1ハゼ時のモデル豆温度(全件共通のU0ひとつで)")
    print(f"   n={len(fb)} 中央 {st.median(fb):.1f}℃ 標準偏差 {st.pstdev(fb):.1f}℃ "
          f"範囲 {min(fb):.0f}〜{max(fb):.0f}℃")
    print("\n■ 検証2 焙煎度別の積算入熱(単調に増えていれば筋が通っている)")
    for lv in ["浅煎り", "中煎り", "中深煎り", "深煎り"]:
        g = per_lv.get(lv) or []
        if not g:
            continue
        print(f"   {lv:5} n={len(g):3} {st.median([a for a, _ in g]):5.1f} kJ "
              f"({kcal(st.median([a for a, _ in g])):4.2f} kcal)  終了豆温 {st.median([b for _, b in g]):6.1f}℃")
    print(f"\n■ 全体 総入熱 中央 {st.median(tot):.1f} kJ = {kcal(st.median(tot)):.2f} kcal")
    print(f"        うち蒸発 {st.median(lat):.1f} kJ ({st.median(lat) / st.median(tot) * 100:.0f}%)")
    print(f"        終了豆温 中央 {st.median(endt):.1f}℃")

    print("\n■ 風量はどれだけ効くか(同じ温度カーブで風量だけ固定)")
    base = [p for p in P if p["level"] == "中煎り"][0]
    for f in (50, 65, 80):
        flat = [[0, float(f)], [base["roast"][-1][0], float(f)]]
        r = simulate(base["roast"], flat, u0)
        peak = max(s[3] for s in r)
        late = r[int(len(r) * 0.9)][3]
        print(f"   風量{f}%: 総 {r[-1][2]:5.1f} kJ  最大入熱 {peak:5.1f} W  終盤(τ=0.9) {late:5.1f} W")
    print("   → 総量はほとんど動かないが、入熱速度は大きく変わる(終盤は逆転する)")

    if "--sens" in sys.argv:
        print("\n■ 前提への感度")
        b = st.median(tot)
        for tag, kw in [("含水率 8%", dict(moisture=0.08)), ("含水率 12%", dict(moisture=0.12)),
                        ("比熱 1.4", dict(c_dry=1.40)), ("比熱 2.0", dict(c_dry=2.00)),
                        ("潜熱 2260", dict(l_vap=2260.0)), ("潜熱 2800", dict(l_vap=2800.0)),
                        ("豆 40g", dict(bean_g=40.0)), ("豆 60g", dict(bean_g=60.0))]:
            u = fit_u0(P, **kw)
            v = st.median([simulate(x["roast"], x["fan"], u, **kw)[-1][2] for x in P])
            print(f"   {tag:12} {v:5.1f} kJ ({kcal(v):4.2f} kcal) {(v - b) / b * 100:+6.1f}%")


if __name__ == "__main__":
    main()
