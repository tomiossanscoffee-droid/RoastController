# ============================================================
# Roast Studio
# roastlib/calibration.py
# ------------------------------------------------------------
# 豆温度モデル(roastlib/energy.py)を、実際に焙煎した結果で較正する。
#
# ■ なぜ要るか
# energy.py の定数は、プリセット174件に対して次の3つを満たすように当てはめてある。
#     ・ガイド1ハゼ温度を空気が横切った瞬間の豆温度が196℃
#     ・焙煎終了時の「空気温度 - 豆温度」が20℃
#     ・中煎りの焙煎指数の中央値が1.1825
# どれも「そういうものだ」と言われている値であって、この焙煎機・この豆・この人の
# 焙煎で測ったものではない。特に序盤は、焙煎機が吸入温度しか測らないため
# 裏づけが何も無い(較正点の1ハゼは焙煎の後半にしかない)。
#
# そこで、専用のプロファイルを1回焼いて、耳と秤で測れるものだけを入れてもらい、
# 定数を測定値に合わせ直す。測っていない項目は既定値のままにするので、
# 1つだけ入れて使うこともできる。
#
# ■ 何を測ると何が決まるか
#     1ハゼ開始時刻   → U0        その時刻に豆が196℃に達するよう、熱の入りやすさ全体
#     1ハゼ終了時刻   → CRACK_SPREAD  ハゼの続く長さ = 細胞の強度のばらつき
#     2ハゼ開始時刻   → H_ENDO    終盤の熱収支(下記の但し書きを参照)
#     焙煎後の重量    → K_PYRO    乾物がどれだけ分解して飛んだか
#     中断時刻と重量  → 乾燥速度   そこまでに水がどれだけ抜けたか
#
# ■ 但し書き(承知のうえで使うこと)
#  ・2ハゼの豆温度は測れないので、一般に言われる225℃を仮定している。この仮定が
#    ずれていれば H_ENDO もその分ずれる。5つのうち最も弱い項目。
#  ・中断時の重量は「豆が予想より冷たかった」のか「乾きにくい豆だった」のかを
#    区別できない(どちらも同じ重量になる)。ここでは後者として乾燥速度に寄せる。
#    したがって、序盤の豆温度そのものは較正できないまま残る。
#  ・焙煎指数は含水率とも結び付いている。含水率はアプリの設定のまま動かさず、
#    差はすべて K_PYRO に寄せる。
# ============================================================
from __future__ import annotations

from typing import Optional

from . import energy as E

# ------------------------------------------------------------
# 校正用プロファイル
# ------------------------------------------------------------
# 選び方:
#  ・1ハゼの前後で豆のRoRが12℃/分程度になるよう、ゆるやかに上げる。時刻を±5秒
#    読み違えても温度換算で±1℃に収まる(実在プリセットは1ハゼ付近で20℃/分を超え、
#    同じ読み違いが±1.7℃になってしまう)。
#  ・2ハゼまで届かせる。届かないと5項目のうち1つが測れない。
#  ・それでいて普段焼く範囲を外れない。焙煎指数1.256は実在の深煎りプリセット
#    (1.250〜1.258)と同じで、ここを外して焼き飛ばすと、普段使わない条件で
#    定数を当てはめることになってしまう。
# THE ROAST EXPERTの制限(255℃・20点・900秒)内。
CALIBRATION_PROFILE = {
    "name": "豆温度モデル 校正用",
    "roast": [[0, 185], [60, 95], [240, 180], [440, 216], [600, 240], [680, 247]],
    "fan": [[0, 50], [1, 80], [300, 70], [680, 60]],
    "cooldown": [700, 60],
}

# 2ハゼのときの豆温度(℃)。測れないので一般に言われる値を置いている。
T_SC_BEAN = 225.0

# 較正で動かす定数と、既定値からどこまで離れてよいか。
# 離れすぎたら測り間違いか、モデルの前提そのものが合っていない。
CAL_BOUNDS = {
    # 熱の入りやすさ。上限が1.45なのは、これを超えると豆が上昇中の空気温度を
    # 追い越してしまうため(174件×含水率3通りで検証。1.45倍まで0件、1.5倍で1件、
    # 1.6倍で31件、3.0倍では221件・最大16.8℃)。熱風焙煎では起こり得ないので、
    # 測定値がこれ以上を要求するなら測り間違いか、モデルの前提が合っていない。
    # その場合は端に張り付いて警告が出る(下の notes)。
    "U0": (0.3, 1.45),
    "CRACK_SPREAD": (0.2, 5.0),  # ハゼの続く長さ
    "H_ENDO": (0.2, 5.0),        # 終盤の吸熱
    "K_PYRO": (0.05, 20.0),      # 乾物の分解(指数の効き方が緩いので広めに取る)
    "K_DRY": (0.1, 10.0),        # 乾燥の速さ(表面・内部を同じ倍率で動かす)
}

MEASUREMENT_KEYS = ("fcStart", "fcEnd", "scStart", "greenG", "roastedG",
                    "abortAt", "abortG")

# 振り切ったときに何が起きているのかを、定数名ではなく言葉で伝える。
BOUND_LABELS = {
    "U0": "熱の入りやすさ", "CRACK_SPREAD": "ハゼの続く長さ",
    "H_ENDO": "終盤の吸熱", "K_PYRO": "乾物の分解", "K_DRY": "乾燥の速さ",
}
BOUND_REASONS = {
    "U0": "1ハゼが早すぎる(または遅すぎる)ため、これ以上熱の入りを強められません"
          "(強めると豆が吸入温度を追い越してしまい、熱風焙煎では起こり得ません)。",
    "CRACK_SPREAD": "ハゼの続く時間が、モデルで表せる範囲から外れています。",
    "H_ENDO": "1ハゼから2ハゼまでの間隔が、モデルで表せる範囲から外れています。",
    "K_PYRO": "焙煎後の重さが、含水率の設定と噛み合っていません"
              "(設定の生豆の含水率を見直すと収まることがあります)。",
    "K_DRY": "途中で止めたときの重さが、その時刻にしては減りすぎ(または減らなすぎ)です。",
}


def _overrides(scale: dict) -> dict:
    """倍率の辞書を、energy.estimate に渡す上書き値に変える。"""
    out = {}
    if "U0" in scale:
        out["U0"] = E.U0 * scale["U0"]
    if "CRACK_SPREAD" in scale:
        out["CRACK_SPREAD"] = E.CRACK_SPREAD * scale["CRACK_SPREAD"]
    if "H_ENDO" in scale:
        out["H_ENDO"] = E.H_ENDO * scale["H_ENDO"]
    if "K_PYRO" in scale:
        out["K_PYRO"] = E.K_PYRO * scale["K_PYRO"]
    if "K_DRY" in scale:
        out["K_SURFACE"] = E.K_SURFACE * scale["K_DRY"]
        out["K_INNER"] = E.K_INNER * scale["K_DRY"]
    return out


def _time_at_bean(series, temp: float) -> Optional[float]:
    """豆温度が temp に達した時刻。届かなければ None。"""
    for p in series:
        if p["bean"] >= temp:
            return p["t"]
    return None


def _bisect(lo: float, hi: float, value_of, target: float, rising: bool,
            iters: int = 24) -> float:
    """value_of(x) が target になる x を二分法で求める。

    rising=True なら x を大きくすると value_of も大きくなる関係。
    範囲の外に答えがある場合は端で止まる(呼び出し側が CAL_BOUNDS で判断する)。
    """
    for _ in range(iters):
        mid = (lo + hi) / 2
        v = value_of(mid)
        if v is None:
            # 目標の温度に届かなかった = 「時刻が無限に遅い」とみなす。
            # そうすれば下の判定がそのまま正しく働く(届かない側へは詰めない)。
            v = float("inf")
        lo, hi = (mid, hi) if (v < target) == rising else (lo, mid)
    return (lo + hi) / 2


def fit(measurements: dict, moisture: float = E.MOISTURE,
        profile: Optional[dict] = None) -> dict:
    """測定値から定数の上書き値を求める。

    measurements: MEASUREMENT_KEYS のうち入っているものだけを使う。
        fcStart / fcEnd / scStart / abortAt … 焙煎開始からの秒数
        greenG / roastedG / abortG … グラム
    戻り値:
        {"overrides": {定数名: 値}, "scale": {定数名: 既定値に対する倍率},
         "notes": [人が読む説明], "used": [使った測定項目]}
    """
    prof = profile or CALIBRATION_PROFILE
    roast, fan = prof["roast"], prof["fan"]
    scale: dict = {}
    notes: list = []
    used: list = []

    def series(extra: Optional[dict] = None):
        cal = _overrides({**scale, **(extra or {})})
        r = E.estimate(roast, fan, moisture=moisture, cal=cal)
        return r

    def num(key):
        v = measurements.get(key)
        if v is None or v == "":
            return None
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    fc_start, fc_end = num("fcStart"), num("fcEnd")
    sc_start = num("scStart")
    green_g = num("greenG") or E.BEAN_G
    roasted_g = num("roastedG")
    abort_at, abort_g = num("abortAt"), num("abortG")

    # 序盤→終盤の順に決めていく。定数どうしは互いに効くので(たとえば乾燥が遅いと
    # 熱容量が残って豆が上がりにくくなる)、1周では収まらない。4周まわすと、
    # 当てはめた定数で焼き直したときの時刻の再現が数秒以内に落ち着く。
    # 1周あたりの計算は5項目×二分法24回で、全部入れても数秒で終わる。
    for _round in range(4):
        # ---- 乾燥の速さ: 中断した時点の重量 ----
        if abort_at and abort_g:
            target_lost = green_g - abort_g
            if target_lost > 0:
                def lost_at(x):
                    r = series({"K_DRY": x})
                    # 中断時点までに減った重量(g)。水の減りがほとんどを占める。
                    return (green_g / 1000.0 - _mass_at(r, abort_at, green_g)) * 1000.0
                lo, hi = CAL_BOUNDS["K_DRY"]
                scale["K_DRY"] = _bisect(lo, hi, lost_at, target_lost, rising=True)
                if _round == 0:
                    used.append("abort")

        # ---- 熱の入りやすさ: 1ハゼ開始時刻 ----
        if fc_start:
            # 1ハゼの「開始」は、豆が T_FC_BEAN に達した瞬間(ごく一部の細胞が
            # 壊れ始める時点)。energy.burst_fraction の分布の置き方と揃えている。
            def fc_time(x):
                return series({"U0": x})["crack_start"]
            lo, hi = CAL_BOUNDS["U0"]
            # U0を上げるほど早く196℃に届く = 時刻は下がるので rising=False
            scale["U0"] = _bisect(lo, hi, fc_time, fc_start, rising=False)
            if _round == 0:
                used.append("fcStart")

        # ---- ハゼの続く長さ: 1ハゼ終了 - 開始 ----
        if fc_start and fc_end and fc_end > fc_start:
            want = fc_end - fc_start
            def crack_len(x):
                r = series({"CRACK_SPREAD": x})
                a, b = r["crack_start"], r["crack_end"]
                return None if (a is None or b is None) else b - a
            lo, hi = CAL_BOUNDS["CRACK_SPREAD"]
            scale["CRACK_SPREAD"] = _bisect(lo, hi, crack_len, want, rising=True)
            if _round == 0:
                used.append("fcEnd")

        # ---- 終盤の熱収支: 2ハゼ開始時刻 ----
        if sc_start:
            def sc_time(x):
                return _time_at_bean(series({"H_ENDO": x})["series"], T_SC_BEAN)
            lo, hi = CAL_BOUNDS["H_ENDO"]
            # H_ENDOを上げるほど吸熱が増えて遅くなる = rising=True
            scale["H_ENDO"] = _bisect(lo, hi, sc_time, sc_start, rising=True)
            if _round == 0:
                used.append("scStart")

        # ---- 乾物の分解: 焙煎後の重量 ----
        if roasted_g and roasted_g < green_g:
            want_index = green_g / roasted_g
            def index_of(x):
                return series({"K_PYRO": x})["roast_index"]
            lo, hi = CAL_BOUNDS["K_PYRO"]
            scale["K_PYRO"] = _bisect(lo, hi, index_of, want_index, rising=True)
            if _round == 0:
                used.append("roastedG")

    overrides = _overrides(scale)
    for name, mul in sorted(scale.items()):
        lo, hi = CAL_BOUNDS[name]
        if lo * 1.02 < mul < hi * 0.98:
            continue
        notes.append(f"{BOUND_LABELS.get(name, name)}が動かせる範囲の端({mul:.2f}倍)まで"
                     f"振り切りました。{BOUND_REASONS.get(name, '')}"
                     "測った値を見直すか、この項目は空欄にして他の項目だけで較正してください。")
    return {"overrides": overrides, "scale": scale, "notes": notes, "used": used}


# ------------------------------------------------------------
# 焙煎ログから学ぶ
# ------------------------------------------------------------
# 較正用プロファイルを焼くのは1回きりだが、普段の焙煎ログは焼くたびに増える。
# 「1ハゼ確認」を押した記録には、実測の吸入温度カーブと、本当に音が聞こえた時刻が
# 揃っている。その時刻にモデルが出す豆温度を集めれば、1ハゼの豆温度そのものを
# 実測から決められる。記録が増えるほど中央値は安定し、ばらつきも見えるようになる。
#
# 2ハゼも同じやり方で決められる。較正では2ハゼの豆温度を225℃と仮定していて、
# これが5項目のうち最も弱い前提だった。実測できればその仮定が要らなくなる。
#
# ■ 使う記録を選ぶ条件
#   ・実測カーブがある
#   ・「1ハゼ確認」を押している(fc_time_inferred が偽)。ガイド温度からの推定値は
#     モデルの入力から作った値なので、使うと自分で自分を較正することになる
#   ・その時刻がカーブの範囲に入っている
def learn_from_logs(records, moisture=E.MOISTURE, cal=None, altitude_of=None):
    """焙煎ログから、1ハゼ・2ハゼの豆温度を集める。

    records: 焙煎記録の一覧(roast_curve / fan_curve / fc_time / fc_time_inferred /
             sc_time を持つ辞書)
    altitude_of: 記録→標高帯 を返す関数(標高補正を効かせる場合)
    戻り値: {"fc": {...}, "sc": {...}, "altitude": {...}, "skipped": {理由: 件数}}

    altitude は、標高の分かる記録から求めた傾き(℃/1000m)。標高帯が2つ以上に
    またがり、かつ4件以上ないと出さない(1つの帯に固まっていると、傾きなのか
    基準値のずれなのか区別が付かないため)。
    """
    fc_temps, sc_temps = [], []
    by_alt = []          # (標高の代表値m, 1ハゼの豆温度) 標高が分かっている記録だけ
    skipped = {"カーブなし": 0, "1ハゼ未記録": 0, "推定値のみ": 0, "時刻が範囲外": 0}
    for rec in records or []:
        curve = rec.get("roast_curve") or []
        if len(curve) < 2:
            skipped["カーブなし"] += 1
            continue
        fc_t = rec.get("fc_time")
        if fc_t is None:
            skipped["1ハゼ未記録"] += 1
            continue
        if rec.get("fc_time_inferred"):
            # ガイド温度から推し量った値。これで較正すると循環する。
            skipped["推定値のみ"] += 1
            continue
        c = dict(cal or {})
        if altitude_of is not None:
            c["T_FC_BEAN"] = E.T_FC_BEAN + E.altitude_fc_offset(altitude_of(rec))
        r = E.estimate([[p[0], p[1]] for p in curve],
                       [[p[0], p[1]] for p in (rec.get("fan_curve") or [])] or None,
                       moisture=moisture, cal=c)
        if not r:
            skipped["カーブなし"] += 1
            continue
        s = r["series"]
        t_end = s[-1]["t"]
        if not (0 < fc_t <= t_end):
            skipped["時刻が範囲外"] += 1
            continue
        fc_bean = s[min(int(fc_t), len(s) - 1)]["bean"]
        fc_temps.append(fc_bean)
        if altitude_of is not None:
            m = E.ALTITUDE_BUCKET_M.get((altitude_of(rec) or "").strip())
            if m is not None:
                by_alt.append((m, fc_bean))
        sc_t = rec.get("sc_time")
        if sc_t is not None and fc_t < sc_t <= t_end:
            sc_temps.append(s[min(int(sc_t), len(s) - 1)]["bean"])

    def stat(vals):
        if not vals:
            return {"n": 0, "median": None, "sd": None, "min": None, "max": None}
        import statistics as st
        return {"n": len(vals), "median": round(st.median(vals), 1),
                "sd": round(st.pstdev(vals), 1) if len(vals) > 1 else 0.0,
                "min": round(min(vals), 1), "max": round(max(vals), 1)}

    # 標高の傾き(℃/1000m)。標高の分かる記録が2つ以上の帯にまたがって初めて出せる。
    # 1つの帯に固まっていると、傾きなのか基準値のずれなのか区別が付かない。
    alt = {"n": len(by_alt), "buckets": len({m for m, _ in by_alt}), "slope": None}
    if alt["buckets"] >= 2 and len(by_alt) >= 4:
        n = len(by_alt)
        mx = sum(m for m, _ in by_alt) / n
        my = sum(t for _, t in by_alt) / n
        den = sum((m - mx) ** 2 for m, _ in by_alt)
        if den > 0:
            # 最小二乗の傾き(℃/m)を ℃/1000m に直す
            per_m = sum((m - mx) * (t - my) for m, t in by_alt) / den
            alt["slope"] = round(max(min(per_m * 1000.0, 10.0), -10.0), 2)
            # 基準標高(ALTITUDE_FC_REF_M)での値。傾きを使うときの基準値になる。
            # 中央値をそのまま基準値にすると、記録の標高の偏りが基準値に入ったうえ
            # さらに傾きで足し引きされ、二重に効いてしまう。
            alt["base"] = round(my + per_m * (E.ALTITUDE_FC_REF_M - mx), 1)
    return {"fc": stat(fc_temps), "sc": stat(sc_temps),
            "altitude": alt, "skipped": skipped}


def _mass_at(result: dict, t: float, green_g: float) -> float:
    """焙煎開始から t 秒の時点での豆の重さ(kg)。

    estimate は最終重量しか返さないので、系列に持たせた残存重量を使う。
    """
    series = result["series"]
    if not series:
        return green_g / 1000.0
    prev = series[0]
    for p in series:
        if p["t"] >= t:
            return p.get("mass", prev.get("mass", green_g / 1000.0))
        prev = p
    return series[-1].get("mass", green_g / 1000.0)
