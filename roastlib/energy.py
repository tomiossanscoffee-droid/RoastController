# ============================================================
# Roast Studio
# roastlib/energy.py
# ------------------------------------------------------------
# 焙煎プロファイル(温度カーブ+風量カーブ)から、豆が受け取った熱量と
# 豆の温度を推定する。あくまで推定値で、実測ではない。
#
# ■ 前提
#   豆50g(焙煎機の仕様どおり固定) / 生豆の含水率10%(既定値)→ 乾物45g・水5g
#   含水率はアプリの設定(beanMoisturePct、5〜15%)で変えられる。ニュークロップと
#   オールドクロップでも違うため、焙煎後の重量を量って実測の焙煎指数に合うよう
#   調整できる(含水率5→15%で指数の予測は1.136→1.244と大きく動く)。
#   焙煎指数 = 生豆重量 ÷ 焙煎後重量。焙煎度の目安は
#       浅煎り 1.140〜1.170 / 中煎り 1.170〜1.195
#       中深煎り 1.195〜1.220 / 深煎り 1.220以上
#   水分は「表面側だけ先に抜け、大部分は1ハゼまで細胞内に残る」。1ハゼは細胞内で
#   水蒸気になった水の圧力に細胞が耐えられず弾ける現象なので、蒸発の潜熱は
#   焙煎中盤ではなく1ハゼ前後に集中する。
#
# ■ モデル
#   状態量: 豆温度 T_b / 表面水分 w_free / 内部水分 w_bound / 乾物 m_dry
#       対流入熱 q  = U(風量) × (空気温度 - T_b)   U = U0×(風量/70)^0.8
#       表面水分 : T_b>100℃ で徐々に蒸発
#       内部水分 : T_b が1ハゼ豆温度(196℃)に達したら時定数25秒で一気に抜ける
#       乾物分解 : アレニウス型(CO2・揮発成分)。生成ガスは高温のまま出ていくので
#                  吸熱として温度の式に入れる
#       dT_b/dt = (q - 蒸発 - 脱ガス吸熱) / (乾物×比熱 + 水×比熱)
#
#   定数は3つを、それぞれ独立した基準1つで当てはめてある(プリセット174件、2026-08):
#       U0     … ガイド1ハゼ温度を空気が横切った瞬間の豆温度が196℃
#       H_ENDO … 焙煎終了時の「空気温度 - 豆温度」が20℃(実務で言われる値)
#       K_PYRO … 中煎りの焙煎指数の中央値が1.1825(定義帯の中央)
#
#   H_ENDOの基準が要るのは、1ハゼで内部水分が一気に気化して豆温度が196℃に
#   張り付く(相変化の平坦域=ポップコーンの原理そのもの)ため。U0を3割変えても
#   1ハゼ時の豆温度はほとんど動かず、1ハゼだけではU0が決まらない。
#
#   脱ガスを発熱として入れると、熱いほど分解が進んで更に熱くなる正のフィード
#   バックで熱暴走し、豆温度が空気温度を超えてしまう(熱風焙煎では起こり得ない)。
#   吸熱は負のフィードバックなので安定する。
#
# ■ 検証(プリセット174件)
#   1ハゼ時の豆温度 中央196.0℃・標準偏差4.2℃
#   終了時の空気-豆 中央19.7℃(浅24.9 / 中18.1 / 中深19.4 / 深19.8℃)
#   豆温度が空気温度を超えた点 0(熱風焙煎では起きてはいけない)
#   焙煎指数(中煎りだけ当てはめ、他3つは予測)
#       浅煎り1.142○ 中煎り1.184○ 中深煎り1.193△ 深煎り1.246○
#   積算入熱 7.51 / 8.86 / 9.14 / 10.52 kcal(焙煎度の順に単調増加)
#   検証手順は scripts/estimate_roast_energy.py を参照。
#
# ■ 使う側が知っておくべき限界
#   ・絶対値は±15%程度の推定。含水率±2%で±8.6%、比熱1.4〜2.0で±9%動く。
#     曲線の形は前提を振ってもほとんど動かないので、比較には使える。
#   ・投入直後が最も不確か。集中定数モデルは豆内部の温度勾配を持たないため、
#     U_COLDで熱伝達を絞って辻褄を合わせている(下記参照)。
#     なお、カラーチェンジはこのモデルの入力でも較正点でもない(1ハゼだけを使う)。
#     ユーザーのガイド設定184℃の時点でモデルは豆167.7℃を出し、一般値140〜150℃とは
#     ずれるが、これは目視の判断を「明らかに色が変わった時点」で取っているため。
#     モデル上「豆145℃」に達してからガイド設定を横切るまでの間隔は中央56秒
#     (総時間の11%)で、変わり始めとはっきり変わった差として妥当な範囲だった。
#   ・同じ焙煎度でも焙煎時間・最終温度で変わる。最終温度と焙煎時間の2変数で
#     重回帰してもR²=0.889にとどまり、33%が説明されずに残る(残差が一番相関
#     するのは終盤のRoR、r=-0.458)。終了直前の保持(ベイク)では温度カーブが
#     ほぼ同じでも入熱が16%増える。
#
# 画面側(app/static/index.html の estimateRoastEnergy)に同じ計算がある。
# 数値が食い違わないよう、tests/test_energy.py で突き合わせている。
# 片方を変えたら必ずもう片方も変えること。
# ============================================================
from __future__ import annotations

import math
from typing import Optional, Sequence

# ---- 豆の前提 ----
BEAN_G = 50.0        # 生豆の量(g)。焙煎機の仕様どおり固定で、設定項目にはしていない
MOISTURE = 0.10      # 生豆の含水率の既定値(アプリの設定 beanMoisturePct で変わる)
C_DRY = 1.70         # 乾物の比熱 kJ/(kg·K)
C_W = 4.18           # 水の比熱 kJ/(kg·K)
T_START = 20.0       # 投入時の豆温度(室温)
T_FC_BEAN = 196.0    # 1ハゼの豆温度

# ---- 熱伝達 ----
U0 = 0.00512         # 熱伝達係数 kW/K(風量70%基準、豆260℃相当の上限値)
FAN_REF = 70.0
FAN_EXP = 0.8        # 強制対流 Nu ∝ Re^0.8 相当
# 投入直後、豆が冷たく濡れている間は熱の入りが鈍い。豆の中心まで熱が伝わるのに
# 時間がかかる(内部の温度勾配)うえ、表面の水分が蒸発して表面を冷やすため。
# 集中定数モデルはこの内部勾配を持てないので、係数そのものを豆温度で変える。
# 20℃でU0のU_COLD倍、260℃でU0そのもの、として線形に補間する。焙煎が進むほど
# 豆は乾いて多孔質になり、熱が入りやすくなるので、向きとしても素直。
# これを入れないと、投入直後に豆が空気温度を追い越してしまう(熱風焙煎では
# 起こり得ない)。実際、この補正なしでは投入40秒で豆が125℃まで上がっていた。
U_COLD = 0.15
U_WARM_TEMP = 260.0

# ---- 水分 ----
FREE_FRAC = 0.25     # 1ハゼ前に抜ける水分の割合(残り75%は細胞内に残る)
K_FREE = 8.0e-5      # 表面水分の乾燥速度係数
TAU_FC = 25.0        # 1ハゼで内部水分が抜けきる時定数(秒)

# ---- 乾物の分解 ----
EA = 65000.0         # 活性化エネルギー J/mol
K_PYRO = 3153.5      # 前指数因子 /s
H_ENDO = 3024.0      # 脱ガス1kgあたりの吸熱 kJ/kg
R_GAS = 8.314

DT = 1.0             # 出力の刻み(秒)。画面側と必ず揃えること。
SUBSTEPS = 4         # 1秒あたりの内部積分回数。投入直後は空気温度が毎秒1.5℃以上
                     # 下がるため、1秒刻みのままだと豆が空気を追い越してしまう
                     # (物理ではなく離散化の誤差)。細かく刻んで避ける。
KJ_PER_KCAL = 4.184

# 焙煎指数の目安(生豆重量 ÷ 焙煎後重量)
ROAST_INDEX_BANDS = (
    ("浅煎り", 1.140, 1.170),
    ("中煎り", 1.170, 1.195),
    ("中深煎り", 1.195, 1.220),
    ("深煎り", 1.220, float("inf")),
)


def _interp(points: Sequence[Sequence[float]], t: float) -> float:
    """折れ線の t 秒での値。範囲外は端の値で止める。"""
    if not points:
        return 0.0
    if t <= points[0][0]:
        return points[0][1]
    if t >= points[-1][0]:
        return points[-1][1]
    for a, b in zip(points, points[1:]):
        if a[0] <= t <= b[0]:
            if b[0] == a[0]:
                return a[1]
            return a[1] + (b[1] - a[1]) * (t - a[0]) / (b[0] - a[0])
    return points[-1][1]


def latent_heat(t_c: float) -> float:
    """その温度での蒸発潜熱 kJ/kg(Watsonの近似)。高温ほど小さい。"""
    tc = 373.946
    ratio = (tc - min(t_c, 370.0)) / (tc - 100.0)
    return 2257.0 * (max(ratio, 0.05) ** 0.38)


def roast_index_level(index: float) -> str:
    """焙煎指数から焙煎度の目安を返す。帯の外は最も近い側の名前。"""
    for name, lo, hi in ROAST_INDEX_BANDS:
        if lo <= index < hi:
            return name
    return ROAST_INDEX_BANDS[0][0] if index < ROAST_INDEX_BANDS[0][1] else ROAST_INDEX_BANDS[-1][0]


def estimate(roast_points: Sequence[Sequence[float]],
             fan_points: Optional[Sequence[Sequence[float]]] = None,
             bean_g: float = BEAN_G, moisture: float = MOISTURE,
             dt: float = DT) -> Optional[dict]:
    """プロファイルから入熱と豆温度を推定する。

    roast_points: [[秒, ℃], ...]  fan_points: [[秒, %], ...](無ければ70%固定)
    戻り値:
        {"series": [{"t","air","bean","kcal"}, ...],
         "total_kj", "total_kcal", "roasted_g", "roast_index", "roast_index_level",
         "end_bean_temp", "end_gap"}
    点が2つ未満・時間が0以下なら None。
    """
    if not roast_points or len(roast_points) < 2:
        return None
    t_end = float(roast_points[-1][0])
    if t_end <= 0:
        return None

    m = bean_g / 1000.0
    m_dry = m * (1.0 - moisture)
    w_free = m * moisture * FREE_FRAC
    w_bound = m * moisture * (1.0 - FREE_FRAC)
    # 熱伝達係数は豆の量に比例させる。U0は50gの豆に対して当てはめた値で、
    # 豆が増えれば表面積も比例して増えるため。これをやらないと、少量にしたとき
    # 熱容量だけが減って豆が空気温度に追いつきすぎる(10gで追い越しが出た)。
    # 結果として豆温度のカーブは量によらずほぼ同じになり、積算入熱は量に比例する。
    u_scale = bean_g / BEAN_G
    t_b = T_START
    e_in = 0.0
    series = []

    steps = int(math.floor(t_end / dt)) + 1
    h = dt / SUBSTEPS
    for i in range(steps + 1):
        t = min(i * dt, t_end)
        series.append({"t": t, "air": _interp(roast_points, t), "bean": t_b,
                       "kcal": e_in / KJ_PER_KCAL})
        if t >= t_end:
            break
        for k in range(SUBSTEPS):
            tk = min(t + k * h, t_end)
            t_air = _interp(roast_points, tk)
            fan = _interp(fan_points, tk) if fan_points else FAN_REF
            warm = min(max((t_b - T_START) / (U_WARM_TEMP - T_START), 0.0), 1.0)
            u = (U0 * u_scale * ((max(fan, 1.0) / FAN_REF) ** FAN_EXP)
                 * (U_COLD + (1.0 - U_COLD) * warm))
            q = u * (t_air - t_b)                              # kW
            # 表面水分: 100℃を超えたら徐々に
            r_free = min(K_FREE * w_free * max(t_b - 100.0, 0.0), w_free / h) if w_free > 0 else 0.0
            # 内部水分: 1ハゼ豆温度に達したら一気に
            r_bound = min(w_bound / TAU_FC, w_bound / h) if (t_b >= T_FC_BEAN and w_bound > 0) else 0.0
            q_lat = (r_free + r_bound) * latent_heat(t_b)
            # 乾物の分解。生成ガスが熱を持ち去る分は吸熱として扱う。
            r_pyro = min(K_PYRO * m_dry * math.exp(-EA / (R_GAS * max(t_b + 273.15, 200.0))),
                         m_dry * 0.01 / h)
            c_eff = max(m_dry * C_DRY + (w_free + w_bound) * C_W, 1e-4)
            t_b += (q - q_lat - r_pyro * H_ENDO) / c_eff * h
            w_free -= r_free * h
            w_bound -= r_bound * h
            m_dry -= r_pyro * h
            if q > 0:
                e_in += q * h

    roasted = m_dry + w_free + w_bound
    index = (m / roasted) if roasted > 0 else 0.0
    end_air = float(roast_points[-1][1])
    return {
        "series": series,
        "total_kj": e_in,
        "total_kcal": e_in / KJ_PER_KCAL,
        "roasted_g": roasted * 1000.0,
        "roast_index": index,
        "roast_index_level": roast_index_level(index),
        "end_bean_temp": t_b,
        "end_gap": end_air - t_b,
    }
