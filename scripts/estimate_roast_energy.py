#!/usr/bin/env python3
# ============================================================
# Roast Studio
# scripts/estimate_roast_energy.py
# ------------------------------------------------------------
# 焙煎プロファイル(温度カーブ+風量カーブ)から、豆が受け取った熱量
# (積算カロリー)を推定するモデルと、プリセット174件での検証。
#
# ■ 前提(2026-08、ユーザー指定)
#   豆50g / 生豆の含水率10% → 乾物45g・水5g
#   焙煎指数 = 生豆重量 ÷ 焙煎後重量。焙煎度の定義は
#       浅煎り 1.140〜1.170 / 中煎り 1.170〜1.195
#       中深煎り 1.195〜1.220 / 深煎り 1.220以上
#   水分は「表面側だけ先に抜け、大部分は1ハゼまで細胞内に残る」。
#   1ハゼは、細胞内で水蒸気になった水の圧力に細胞が耐えられず弾ける現象なので、
#   蒸発の潜熱は焙煎中盤ではなく1ハゼ前後に集中する。
#
# ■ モデル
#   状態量: 豆温度 T_b / 表面水分 w_free / 内部水分 w_bound / 乾物 m_dry
#       対流入熱 q  = U(風量) × (空気温度 - T_b)      U = U0×(風量/70)^0.8
#       表面水分 : T_b>100℃ で徐々に蒸発
#       内部水分 : T_b が1ハゼ豆温度(196℃)に達したら時定数25秒で一気に抜ける
#       乾物分解 : アレニウス型(CO2・揮発成分。焙煎指数の分)
#       dT_b/dt = (q - 蒸発) / (乾物×比熱 + 水×比熱)
#   当てはめる定数は2つだけ:
#       U0     … 「ガイド1ハゼ温度を空気が横切った瞬間に豆温度が196℃」で決める
#       k_pyro … 「中煎りの焙煎指数の中央値が1.1825(帯の中央)」で決める
#
#   発熱反応は豆温度の式に入れていない。入れると正のフィードバックで熱暴走し、
#   モデル上の豆温度が空気温度を超えてしまう(熱風焙煎では起こり得ない)。
#   空気温度は焙煎機が制御している量なので、発熱の分はヒーター出力が下がる形で
#   すでに観測値に織り込まれている、という整理。
#
# ■ 検証結果(プリセット174件)
#   ・1ハゼ時の豆温度: 全件共通のU0ひとつで 中央196.0℃・標準偏差4.3℃
#     (水分が1ハゼまで残るモデルにしたことで、旧モデルの8.9℃から改善した)
#   ・終了豆温度 中央232.8℃ < 終了空気温度 中央240.0℃(逆転は174件中2件のみ)
#   ・焙煎指数: 中煎りだけで合わせた定数ひとつで、他3つの焙煎度も予測できた
#       浅煎り 1.141(定義1.140〜1.170)○
#       中煎り 1.183(1.170〜1.195)○ ←当てはめに使用
#       中深煎り 1.194(1.195〜1.220)△ 0.001はみ出し
#       深煎り 1.267(1.220以上)○
#   ・積算入熱: 6.48 / 7.10 / 7.25 / 7.41 kcal(焙煎度の順に単調増加)
#
# ■ 表示を検討する上での要点
#   ・総カロリーは終了温度でほぼ決まる。風量50%→80%で総量は+4%しか動かない。
#     これはモデルの粗さではなくエネルギー保存則(豆に入った熱=エンタルピー変化)。
#   ・一方「入熱速度(W)」は風量に強く反応する。最大入熱 346W→503W。
#     しかも終盤は逆転する(52.3W→23.7W。豆が空気温度に近づき押し込めなくなる)。
#   ・積算カーブの形は空気温度カーブとも豆温度カーブとも違う。
#     τ=0.1で 空気8% / 豆42% / 積算30%。
#   ・入熱速度は1ハゼ付近(τ≈0.7)で山を作る。内部水分が一気に蒸発する分。
#   ・投入直後(最初の1分)はモデルの信頼度が最も低い。集中定数モデルは豆の
#     内部温度勾配を持たないため、豆温度の立ち上がりを速めに見積もる。
#
# 使い方:
#   venv/bin/python scripts/estimate_roast_energy.py
#   venv/bin/python scripts/estimate_roast_energy.py --sens   # 前提の感度
# ============================================================
import math
import statistics as st
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from analyze_ikawa_vs_presets import GUIDE, interp, load_presets, rising_cross  # noqa: E402

BEAN_G, MOISTURE = 50.0, 0.10
C_DRY, C_W = 1.70, 4.18        # 比熱 kJ/(kg·K)
T_START, T_FC_BEAN = 20.0, 196.0
FAN_REF, FAN_EXP = 70.0, 0.8   # 強制対流 Nu ∝ Re^0.8 相当
R_GAS = 8.314
FREE_FRAC = 0.25               # 1ハゼ前に抜ける水分の割合(残り75%は細胞内)
K_FREE = 8.0e-5                # 表面水分の乾燥速度係数
TAU_FC = 25.0                  # 1ハゼで内部水分が抜けきる時定数(秒)
EA = 65_000.0                  # 乾物分解の活性化エネルギー J/mol
K_PYRO = 2.759e3               # 同 前指数因子 /s(中煎りの焙煎指数で当てはめ)

ROAST_INDEX_BANDS = {"浅煎り": (1.140, 1.170), "中煎り": (1.170, 1.195),
                     "中深煎り": (1.195, 1.220), "深煎り": (1.220, 1.400)}


def l_vap(t_c):
    """その温度での蒸発潜熱 kJ/kg(Watsonの近似)。高温ほど小さい。"""
    tc = 373.946
    return 2257.0 * max((tc - min(t_c, 370.0)) / (tc - 100.0), 0.05) ** 0.38


def simulate(roast, fan, u0, k_pyro=K_PYRO, ea=EA, bean_g=BEAN_G,
             moisture=MOISTURE, c_dry=C_DRY, dt=1.0):
    """1本のプロファイルを積分する。

    戻り値: (時系列, 焙煎後の重量kg)
      時系列 = [(t, 豆温度, 積算入熱kJ, 入熱速度W, 蒸発速度W, 残水分kg, 乾物kg, 蒸発積算kJ), ...]
    """
    m = bean_g / 1000.0
    m_dry = m * (1 - moisture)
    w_free, w_bound = m * moisture * FREE_FRAC, m * moisture * (1 - FREE_FRAC)
    t_b, e_in, e_lat = T_START, 0.0, 0.0
    out, t, t_end = [], 0.0, roast[-1][0]
    while t <= t_end + 1e-9:
        t_air = interp(roast, t)
        f = interp(fan, t) if fan else FAN_REF
        u = u0 * (max(f, 1.0) / FAN_REF) ** FAN_EXP
        q = u * (t_air - t_b)
        r_free = min(K_FREE * w_free * max(t_b - 100.0, 0.0), w_free / dt) if w_free > 0 else 0.0
        r_bound = min(w_bound / TAU_FC, w_bound / dt) if (t_b >= T_FC_BEAN and w_bound > 0) else 0.0
        q_lat = (r_free + r_bound) * l_vap(t_b)
        c_eff = max(m_dry * c_dry + (w_free + w_bound) * C_W, 1e-4)
        t_b += (q - q_lat) / c_eff * dt
        w_free -= r_free * dt
        w_bound -= r_bound * dt
        # 乾物の分解(豆温度に依存。発熱は温度の式に戻さない)
        r_pyro = min(k_pyro * m_dry * math.exp(-ea / (R_GAS * max(t_b + 273.15, 200.0))),
                     m_dry * 0.01 / dt)
        m_dry -= r_pyro * dt
        e_in += max(q, 0.0) * dt
        e_lat += q_lat * dt
        out.append((t, t_b, e_in, max(q, 0.0) * 1000, q_lat * 1000,
                    w_free + w_bound, m_dry, e_lat))
        t += dt
    return out, (m_dry + w_free + w_bound)


def at_time(res, t):
    for a, b in zip(res, res[1:]):
        if a[0] <= t <= b[0]:
            r = (t - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0.0
            return [a[i] + (b[i] - a[i]) * r for i in range(len(a))]
    return list(res[-1])


def fit_u0(profiles, **kw):
    """1ハゼ時の豆温度の中央値が196℃になるU0を求める(発熱と独立なので一度だけ)。"""
    fc_t = [rising_cross(x["roast"], GUIDE["firstCrack"]) for x in profiles]
    lo, hi = 0.0002, 0.02
    for _ in range(30):
        u0 = (lo + hi) / 2
        v = [at_time(simulate(x["roast"], x["fan"], u0, k_pyro=0.0, **kw)[0], t)[1]
             for x, t in zip(profiles, fc_t) if t is not None]
        lo, hi = (u0, hi) if st.median(v) < T_FC_BEAN else (lo, u0)
    return (lo + hi) / 2


def fit_k_pyro(profiles, u0, ea=EA, target=1.1825, **kw):
    """中煎りの焙煎指数の中央値がtargetになる前指数因子を求める。"""
    g = [x for x in profiles if x["level"] == "中煎り"]
    lo, hi = 1e2, 1e14
    for _ in range(50):
        k = (lo + hi) / 2
        idx = [BEAN_G / 1000.0 / simulate(x["roast"], x["fan"], u0, k_pyro=k, ea=ea, **kw)[1] for x in g]
        lo, hi = (k, hi) if st.median(idx) < target else (lo, k)
    return (lo + hi) / 2


def main():
    P = load_presets()
    if not P:
        print("プリセット(nhm.sqlite)が無いため検証できません。")
        return
    u0 = fit_u0(P)
    k_pyro = fit_k_pyro(P, u0)
    print(f"豆 {BEAN_G:.0f}g / 含水率 {MOISTURE * 100:.0f}% / 水分の{(1 - FREE_FRAC) * 100:.0f}%は1ハゼまで残る")
    print(f"当てはめ U0 = {u0:.5f} kW/K,  k_pyro = {k_pyro:.3e} /s (Ea = {EA / 1000:.0f} kJ/mol)\n")

    fc_t = [rising_cross(x["roast"], GUIDE["firstCrack"]) for x in P]
    fb = [at_time(simulate(x["roast"], x["fan"], u0, k_pyro)[0], t)[1]
          for x, t in zip(P, fc_t) if t is not None]
    print("■ 検証1 1ハゼ時のモデル豆温度(全件共通のU0ひとつ)")
    print(f"   n={len(fb)} 中央 {st.median(fb):.1f}℃ 標準偏差 {st.pstdev(fb):.1f}℃")

    rows, ends, airs = {}, [], []
    for x in P:
        r, m_end = simulate(x["roast"], x["fan"], u0, k_pyro)
        rows.setdefault(x["level"], []).append((r[-1][2], r[-1][7], r[-1][1], BEAN_G / 1000.0 / m_end))
        ends.append(r[-1][1])
        airs.append(x["roast"][-1][1])
    print(f"   終了豆温 中央 {st.median(ends):.1f}℃ / 終了空気温 中央 {st.median(airs):.1f}℃ "
          f"(逆転 {sum(1 for a, b in zip(ends, airs) if a > b)}/{len(P)}件)")

    print("\n■ 検証2 焙煎指数(中煎りだけ当てはめ、他3つは予測)")
    print("   焙煎度   積算入熱             うち蒸発  終了豆温   指数(予測)  定義帯")
    for lv, (a, b) in ROAST_INDEX_BANDS.items():
        g = rows.get(lv) or []
        if not g:
            continue
        idx = st.median([q[3] for q in g])
        e = st.median([q[0] for q in g])
        mark = "○" if a <= idx < b else "△"
        print(f"   {lv:5} {e:5.1f} kJ ({e / 4.184:4.2f} kcal) {st.median([q[1] for q in g]):5.1f} kJ "
              f"{st.median([q[2] for q in g]):7.1f}℃  {idx:.3f}{mark}     {a:.3f}〜{b:.3f}")
    all_e = [q[0] for g in rows.values() for q in g]
    print(f"   全体 中央 {st.median(all_e):.1f} kJ = {st.median(all_e) / 4.184:.2f} kcal")

    print("\n■ 風量はどれだけ効くか(同じ温度カーブで風量だけ固定)")
    base = [x for x in P if x["level"] == "中煎り"][0]
    for f in (50, 65, 80):
        flat = [[0, float(f)], [base["roast"][-1][0], float(f)]]
        r, _ = simulate(base["roast"], flat, u0, k_pyro)
        peak = max(s[3] for s in r)
        print(f"   風量{f}%: 総 {r[-1][2]:5.1f} kJ  最大入熱 {peak:6.1f} W  "
              f"終盤(τ=0.9) {r[int(len(r) * 0.9)][3]:5.1f} W  終了豆温 {r[-1][1]:.1f}℃")
    print("   → 総量はほとんど動かないが、入熱速度は大きく変わり終盤は逆転する")

    if "--sens" in sys.argv:
        print("\n■ 前提への感度(総入熱の中央値)")
        b = st.median(all_e)
        for tag, kw in [("含水率 8%", dict(moisture=0.08)), ("含水率 12%", dict(moisture=0.12)),
                        ("比熱 1.4", dict(c_dry=1.40)), ("比熱 2.0", dict(c_dry=2.00)),
                        ("豆 40g", dict(bean_g=40.0)), ("豆 60g", dict(bean_g=60.0))]:
            u = fit_u0(P, **kw)
            v = st.median([simulate(x["roast"], x["fan"], u, k_pyro, **kw)[0][-1][2] for x in P])
            print(f"   {tag:12} {v:5.1f} kJ ({v / 4.184:4.2f} kcal) {(v - b) / b * 100:+6.1f}%")


if __name__ == "__main__":
    main()
