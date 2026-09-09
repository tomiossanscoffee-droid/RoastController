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
#  ・2ハゼは乾物の分解量で判定する(energy.py の SC_DRY_FRAC)。実測3本から
#    決めた値で、以前の「豆温度225℃」という仮定より外れが一桁小さい。
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
#
# UUIDは焙煎機に送るのに必須(16桁の数字)。無いとプロファイルを組み立てられず、
# 送信しても焙煎機は何もしない。新しいUUIDを発行する規則は解明できていないが、
# 「味を推測」「ABCモード」で作るプロファイルは str(int(time.time()*1000)).zfill(16)
# で作った値を使っており、それで実際に焙煎できている(実機の焙煎記録あり)。
# つまり焙煎機は16桁の数字であれば受け付ける。
# 校正用は使い捨てではなく毎回同じものを送りたいので、同じ形の固定値にしてある
# (送るたびに変わると、焙煎機側にどう溜まるか分からない)。
# 1700000000000ミリ秒 = 2023-11-14。このアプリが作られる前の時刻なので、
# 生成プロファイルのUUIDと衝突しない。
CALIBRATION_UUID = "0001700000000000"

CALIBRATION_PROFILE = {
    "name": "豆温度モデル 校正用(深煎り)",
    "roast": [[0, 185], [60, 95], [240, 180], [440, 216], [600, 240], [680, 247]],
    "fan": [[0, 50], [1, 80], [300, 70], [680, 60]],
    "cooldown": [700, 60],
}

# ■ 浅煎り用(2026-08 追加)
# 深煎り用だけでは、1ハゼより後の温度域しか裏づけが取れない。豆の水の大半は
# 1ハゼの前後で抜けるので、そこで止めた焙煎の重量が、いちばん効く実測になる。
#
# 440秒までは深煎り用とまったく同じ形にしてある。こうすると2つの焙煎の違いが
# 「1ハゼの前後で抜けた水」だけになり、焙煎後の重量の差がそのまま答えになる。
# 停止は1ハゼが終わってから約30秒後(豆208℃)。使う人の言う浅煎りの範囲
# 「1ハゼが始まって、ハゼが終わって少し温度があるくらい」に当たる。
# 1ハゼ前後の豆RoRは8.2℃/分で、時刻を±5秒読み違えても豆温度で±0.7℃に収まる
# (深煎り用より更にゆるやか)。焙煎指数1.124は浅煎りの帯の真ん中。
CALIBRATION_PROFILE_LIGHT = {
    "name": "豆温度モデル 校正用(浅煎り)",
    "roast": [[0, 185], [60, 95], [240, 180], [440, 216], [560, 222]],
    "fan": [[0, 50], [1, 80], [300, 70], [560, 60]],
    "cooldown": [580, 60],
}

# ■ 試験用プロファイル2本を削除した(2026-09)
# 「焙煎の発熱を測るためのプロファイル」と「昇温を遅くした検証用」は、どちらも
# モデルの疑問を1つ解くために作った使い捨ての形で、実際に焼いて役目を終えた。
#
# 発熱の検証(2026-08): 2ハゼで吸入温度を落として保ち、音が消えるまでを測った。
#   実測 2ハゼ618秒 / 音は688秒まで。発熱を入れると3本目の残差は縮むが、
#   AICcでも交差検証でも棄却された(定数を増やす価値がない)。
# 昇温を遅くした検証(2026-09): 1ハゼが13分に来るまで遅らせた。
#   実測 1ハゼ音は全体で3回のみ(12:20/12:50/14:13)、焙煎後43.1g。
#   9.3分で焼いた浅煎り用と完全に同じ重量で、「重量は時間ではなく到達温度で
#   決まる」ことがはっきりした。この1本でU0と乾燥の速さを当てはめ直した
#   (roastlib/energy.py の U0 の但し書き)。
#   同時に「ハゼがほとんど進まない領域は観測の質が落ちる」ことも分かった。
#   設計時は1ハゼ開始の精度を±8秒と見ていたが、音が数えられる程度しか鳴らず
#   実際は±30秒相当だった。以後の設計ではこの領域を避ける。
#
# ■ 校正用プロファイル(展開長め)(2026-09 追加)
# 新しいモデルで動かしてよい定数は2つだけと分かった(焙煎1本抜きの交差検証で、
# 3個以上にすると深煎りの予測が壊れる)。したがって較正用プロファイルは
# 「U0と乾燥の速さを最もよく分離できるか」だけで選べばよい。
#
# 既存2本は1ハゼがどちらも7.2分で重なっており、そこが情報の飽和点だった。
# 1ハゼを8.2分にずらし、その後の展開を長くとる形にすると、2つの定数の
# 決まり具合が約2倍になる:
#     現行2本のみ      U0 ±10.40%  乾燥 ±43.45%
#     現行2本 + この1本 U0 ± 4.97%  乾燥 ±25.07%
# 遅い昇温の失敗を踏まえ、1ハゼが確実に開始・終了する範囲に収めてある
# (U0と乾燥が±20%外れた9通りすべてで、1ハゼ開始471〜524秒・終了561〜617秒)。
# 測るのは1ハゼ開始・1ハゼ終了・2ハゼ開始・焙煎後の重量の4つすべて。
CALIBRATION_PROFILE_LONG = {
    "name": "豆温度モデル 校正用(展開長め)",
    "roast": [[0, 185], [60, 95], [240, 180], [500, 210], [760, 240]],
    "fan": [[0, 50], [1, 80], [300, 70], [760, 60]],
    "cooldown": [780, 60],
}

# 較正に使えるプロファイル。測った値はプロファイルごとに持ち、当てはめは
# 両方をまとめて見る(片方だけでも構わない)。
CALIBRATION_PROFILES = {
    "deep": CALIBRATION_PROFILE,
    "light": CALIBRATION_PROFILE_LIGHT,
    "long": CALIBRATION_PROFILE_LONG,
}
CALIBRATION_KIND_LABELS = {"deep": "深煎り", "light": "浅煎り", "long": "展開長め"}

# ------------------------------------------------------------
# アプリの初期値になる較正(2026-09)
# ------------------------------------------------------------
# 開発機(Panasonic The Roast)で較正用プロファイルを実際に焼き、耳と秤で
# 測った値。新規インストール時と「較正を捨てる」を押したときは、空ではなく
# この値から始まる。公開版にも同じ値が入る。
#
# 利用者が自分で測り直せば上書きされる(そのほうが自分の焙煎機に合う)。
# 消したときに空へ戻すのではなくここへ戻すのは、何も無い状態よりは
# 「別の個体で実測した値」のほうが確実にましだからである。
#
# overrides はこの measurements から当てはめた結果を、初回起動で計算し直さなくて
# 済むように持たせてあるだけ。modelVersion が変わると
# _refit_calibration_if_stale() が measurements から当てはめ直す。
DEFAULT_CALIBRATION = {
    "measurements": {
        "roasts": {
            "deep": {
                "fcStart": 442.0,
                "fcEnd": 508.0,
                "scStart": 622.0,
                "greenG": 50.0,
                "roastedG": 40.9,
                "aborts": [
                    {
                        "t": 180.0,
                        "g": 49.2
                    },
                    {
                        "t": 240.0,
                        "g": 49.0
                    },
                    {
                        "t": 300.0,
                        "g": 49.0
                    },
                    {
                        "t": 390.0,
                        "g": 47.9
                    }
                ]
            },
            "light": {
                "fcStart": 432.0,
                "fcEnd": 542.0,
                "roastedG": 43.1
            }
        }
    },
    "modelVersion": "2026-09-slowramp",
    "overrides": {
        "U0": 0.0038023231398269536,
        "CRACK_SPREAD": 1.8032633504867561,
        "H_ENDO": 1760.5655182218559,
        "K_PYRO": 2756.9902342416344,
        "K_SURFACE": 0.003564715010089278,
        "K_INNER": 0.00026735112153112884
    },
    "scale": {
        "K_DRY": 0.5008451133966445,
        "U0": 1.1011651143431664,
        "CRACK_SPREAD": 1.1186497211456303,
        "H_ENDO": 1.0927723407745367,
        "K_PYRO": 1.194174312055111
    },
    "notes": [],
    "used": [
        "abort",
        "fcStart",
        "fcEnd",
        "scStart",
        "roastedG"
    ]
}


# 焙煎機に送れるプロファイル。いまは較正に使う3本と同じ。
SENDABLE_PROFILES = dict(CALIBRATION_PROFILES)
SENDABLE_KIND_LABELS = dict(CALIBRATION_KIND_LABELS)

# 2ハゼのときに一般に言われる豆温度(℃)。モデルはこれを判定に使わない
# (energy.py の SC_DRY_FRAC が判定する)。テストが「その頃に2ハゼが来るか」を
# 大まかに確かめるのに使うだけの、目安の数字である。
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
    # 密閉水が抜けきるまでの温度の幅。既定10℃に対して5〜25℃。
    # 浅煎り用と深煎り用の両方を焼いて初めて決まる(片方だけでは、乾物の分解と
    # 見分けが付かない)。範囲は、狭すぎるとハゼの間に潜熱が集中して豆温度が
    # 止まり、広すぎると1ハゼより手前で水が抜けすぎる、という両側から取った。
    "VENT_SPREAD": (0.5, 2.5),
}

MEASUREMENT_KEYS = ("fcStart", "fcEnd", "scStart", "greenG", "roastedG",
                    "abortAt", "abortG", "aborts")

# 途中で止めて量った重量。1点だけだと、その1点に引っぱられて他の時刻がかえって
# 外れる(実際、3分の1点だけで当てはめたら乾燥が6.1倍になり、5分・6分30秒の
# 重量が2g以上ずれた)。何点でも受け取り、全部からの外れの2乗和で決める。
#   aborts: [{"t": 秒, "g": グラム}, ...]
# 旧い形(abortAt / abortG の1組)もそのまま受け付ける。

# 振り切ったときに何が起きているのかを、定数名ではなく言葉で伝える。
BOUND_LABELS = {
    "U0": "熱の入りやすさ", "CRACK_SPREAD": "ハゼの続く長さ",
    "H_ENDO": "終盤の吸熱", "K_PYRO": "乾物の分解", "K_DRY": "乾燥の速さ",
    "VENT_SPREAD": "水が抜ける温度の幅",
}
BOUND_REASONS = {
    "U0": "1ハゼが早すぎる(または遅すぎる)ため、これ以上熱の入りを強められません"
          "(強めると豆が吸入温度を追い越してしまい、熱風焙煎では起こり得ません)。",
    "CRACK_SPREAD": "ハゼの続く時間が、モデルで表せる範囲から外れています。",
    "H_ENDO": "1ハゼから2ハゼまでの間隔が、モデルで表せる範囲から外れています。",
    "K_PYRO": "焙煎後の重さが、含水率の設定と噛み合っていません"
              "(設定の生豆の含水率を見直すと収まることがあります)。",
    "K_DRY": "途中で止めたときの重さが、その時刻にしては減りすぎ(または減らなすぎ)です。",
    "VENT_SPREAD": "浅煎りと深煎りの焙煎後の重さの差が、モデルで表せる範囲から"
                   "外れています。",
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
    if "VENT_SPREAD" in scale:
        out["VENT_SPREAD"] = E.VENT_SPREAD * scale["VENT_SPREAD"]
    return out


def _bisect(lo: float, hi: float, value_of, target: float, rising: bool,
            iters: int = 24) -> float:
    """value_of(x) が target になる x を二分法で求める。

    rising=True なら x を大きくすると value_of も大きくなる関係。
    範囲の外に答えがある場合は端で止まる(呼び出し側が CAL_BOUNDS で判断する)。

    観測が複数あるときは、それぞれの値の合計を value_of、実測の合計を target に
    する。どの観測もその定数に対して同じ向きに動くので、合計も単調になり、
    「全部からの外れがいちばん小さいところ」に落ちる。2乗和を谷探しで最小化する
    やり方も試したが、届かなかった場合に一定の大きな値が並ぶ平らな区間ができて
    谷探しが迷子になった(単調性を使うこちらの方が確実)。
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


def _abort_points(measurements: dict, abort_at, abort_g) -> list:
    """途中で量った重量を [(秒, グラム), ...] にそろえる。

    新しい形(aborts の一覧)と、古い形(abortAt/abortG の1組)の両方を受ける。
    時刻・重量として読めないものは黙って捨てる(手入力なので空欄が混ざる)。
    """
    points = []
    for item in (measurements.get("aborts") or []):
        if not isinstance(item, dict):
            continue
        try:
            t, g = float(item.get("t")), float(item.get("g"))
        except (TypeError, ValueError):
            continue
        if t > 0 and g > 0:
            points.append((t, g))
    if abort_at and abort_g and not any(abs(t - abort_at) < 1e-6 for t, _ in points):
        points.append((abort_at, abort_g))
    points.sort()
    return points


def _roast_cases(measurements: dict, profile) -> list:
    """測定値を、プロファイルごとの一覧にそろえる。

    新しい形: {"roasts": {"deep": {...}, "light": {...}}}
    古い形  : 測定値をそのまま(深煎り用のプロファイルで測ったものとして扱う)
    """
    roasts = measurements.get("roasts")
    if isinstance(roasts, dict):
        out = []
        for kind, meas in roasts.items():
            prof = CALIBRATION_PROFILES.get(kind)
            if prof and isinstance(meas, dict) and meas:
                out.append((kind, prof, meas))
        if out:
            return sorted(out, key=lambda x: x[0])
    return [("deep", profile or CALIBRATION_PROFILE, measurements)]


def fit(measurements: dict, moisture: float = E.MOISTURE,
        profile: Optional[dict] = None) -> dict:
    """測定値から定数の上書き値を求める。

    measurements: MEASUREMENT_KEYS のうち入っているものだけを使う。
        fcStart / fcEnd / scStart … 焙煎開始からの秒数
        greenG / roastedG … グラム
        aborts … 途中で止めて量った重量 [{"t": 秒, "g": グラム}, ...]
        roasts … プロファイルごとに上記をまとめた辞書(浅煎り用・深煎り用)
    戻り値:
        {"overrides": {定数名: 値}, "scale": {定数名: 既定値に対する倍率},
         "notes": [人が読む説明], "used": [使った測定項目]}

    浅煎り用と深煎り用の両方を焼いてあれば、両方からの外れがいちばん小さくなる
    ように1組の定数を決める。深煎り用だけでは1ハゼより後しか裏づけが取れず、
    豆の水の大半が抜ける1ハゼ前後が合っているかを確かめられないため。
    """
    scale: dict = {}
    notes: list = []
    used: list = []

    def num(meas, key):
        v = meas.get(key)
        if v is None or v == "":
            return None
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    cases = []
    for kind, prof, meas in _roast_cases(measurements, profile):
        green = num(meas, "greenG") or E.BEAN_G
        cases.append({
            "kind": kind,
            "roast": prof["roast"], "fan": prof["fan"],
            "endT": prof["roast"][-1][0],
            "fcStart": num(meas, "fcStart"), "fcEnd": num(meas, "fcEnd"),
            "scStart": num(meas, "scStart"),
            "greenG": green, "roastedG": num(meas, "roastedG"),
            "aborts": _abort_points(meas, num(meas, "abortAt"), num(meas, "abortG")),
        })

    def series(case, extra: Optional[dict] = None):
        cal = _overrides({**scale, **(extra or {})})
        return E.estimate(case["roast"], case["fan"], moisture=moisture, cal=cal)

    def fit_one(name, cases_used, value_of, target_of, rising):
        """その定数を、使えるすべての焙煎の観測に合わせて決める。

        観測が複数あるときは合計どうしを突き合わせる(_bisectの説明を参照)。
        """
        if not cases_used:
            return False
        lo, hi = CAL_BOUNDS[name]

        def total(x):
            got = 0.0
            for c in cases_used:
                v = value_of(c, series(c, {name: x}))
                if v is None:
                    return None      # 1つでも届かなければ「届かない側」として扱う
                got += v
            return got

        target = sum(target_of(c) for c in cases_used)
        scale[name] = _bisect(lo, hi, total, target, rising=rising)
        return True

    # 序盤→終盤の順に決めていく。定数どうしは互いに効くので(たとえば乾燥が遅いと
    # 熱容量が残って豆が上がりにくくなる)、1周では収まらない。4周まわすと、
    # 当てはめた定数で焼き直したときの時刻の再現が数秒以内に落ち着く。
    for _round in range(4):
        # ---- 乾燥の速さ: 途中で止めて量った重量 ----
        # 乾燥を速くするほど、その時刻の豆は軽くなる = rising=False
        if fit_one("K_DRY", [c for c in cases if c["aborts"]],
                   lambda c, r: sum(_mass_at(r, t, c["greenG"]) * 1000.0
                                    for t, _ in c["aborts"]),
                   lambda c: sum(g for _, g in c["aborts"]), rising=False):
            if _round == 0:
                used.append("abort")

        # ---- 熱の入りやすさ: 1ハゼ開始時刻 ----
        # 1ハゼの「開始」は、豆が T_FC_BEAN に達した瞬間(ごく一部の細胞が
        # 壊れ始める時点)。energy.burst_fraction の分布の置き方と揃えている。
        # U0を上げるほど早く196℃に届く = 時刻は下がるので rising=False
        if fit_one("U0", [c for c in cases if c["fcStart"]],
                   lambda c, r: r["crack_start"],
                   lambda c: c["fcStart"], rising=False):
            if _round == 0:
                used.append("fcStart")

        # ---- ハゼの続く長さ: 1ハゼ終了 - 開始 ----
        def crack_len(c, r):
            a, b = r["crack_start"], r["crack_end"]
            return None if (a is None or b is None) else b - a

        if fit_one("CRACK_SPREAD",
                   [c for c in cases if c["fcStart"] and c["fcEnd"]
                    and c["fcEnd"] > c["fcStart"]],
                   crack_len, lambda c: c["fcEnd"] - c["fcStart"], rising=True):
            if _round == 0:
                used.append("fcEnd")

        # ---- 2ハゼ開始時刻は、当てはめには使わない(2026-09) ----
        # 2ハゼは乾物の分解量で決まる(roastlib/energy.py の SC_DRY_FRAC)。つまり
        # 焙煎後の重量と同じものを測っているので、別の定数を決める情報にはならない。
        # 以前は H_ENDO をここで決めていたが、それは「2ハゼ=豆温度225℃」という
        # 誤った判定が作り出した見かけの感度だった。判定を直すと H_ENDO の効き方は
        # 半分以下になり、当てはめは範囲の端に張り付く。
        # 2ハゼの測定値は、予想と見比べる検算として使う(_calibration_expected)。

        # ---- 乾物の分解: 焙煎後の重量 ----
        # 重量そのものではなく焙煎指数で合わせる。生豆の量が違っても比べられる。
        weighed = [c for c in cases if c["roastedG"] and c["roastedG"] < c["greenG"]]
        if fit_one("K_PYRO", weighed,
                   lambda c, r: r["roast_index"],
                   lambda c: c["greenG"] / c["roastedG"], rising=True):
            if _round == 0:
                used.append("roastedG")

        # 水が抜ける温度の幅(VENT_SPREAD)も、浅煎りと深煎りの焙煎後の重さの差から
        # 決められないか試したが、自動では当てはめないことにした。焙煎後の重量に
        # 出る違いが幅5〜25℃の全域で焙煎指数0.025ぶん(深煎り側で0.9g)しかなく、
        # 実測のばらつき(0.2g)に対して足りない。乾物の分解と取り合いになって、
        # 4周まわす間に両者が振動し、範囲の端に張り付いてしまう。
        # 定数としては上書きできるようにしてある(CALIBRATABLE)ので、実測が
        # 貯まって根拠が出たときに手で決められる。

    overrides = _overrides(scale)
    for name, mul in sorted(scale.items()):
        lo, hi = CAL_BOUNDS[name]
        if lo * 1.02 < mul < hi * 0.98:
            continue
        notes.append(f"{BOUND_LABELS.get(name, name)}が動かせる範囲の端({mul:.2f}倍)まで"
                     f"振り切りました。{BOUND_REASONS.get(name, '')}"
                     "測った値を見直すか、この項目は空欄にして他の項目だけで較正してください。")
    return {"overrides": overrides, "scale": scale, "notes": notes, "used": used}


# 焙煎ログからの学習は roastlib/learning.py に移した。ここにあった
# learn_from_logs() は /api/calibration/from_logs のためのもので、その経路が
# 無くなってから本体からは一度も呼ばれていなかった(テストだけが生かしていた)。
# 標高は線形の傾き1本で表していたが、学習層は標高・生産国・品種・精製方法を
# 件数で育つ縮小推定として扱う。


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
