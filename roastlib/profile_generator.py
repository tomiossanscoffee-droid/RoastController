# ============================================================
# Roast Studio
# roastlib/profile_generator.py
# ------------------------------------------------------------
# 「産地(標高)・精製方法・焙煎度・味の好み(酸味/甘み/苦味/コク/
# アフターノートの強さ)」から、焙煎プロファイル(制御点)を自動生成する。
#
# ルールベースの設計方針:
#   1) カーブの「形」(投入→ディップ→上昇、という制御点の基本パターン)は、
#      実機で動作確認済みの純正プリセット174件を集計した結果、焙煎度に
#      よらずほぼ共通していた(投入175-185℃→60秒前後で95-100℃まで
#      ディップ→120秒時点で140-155℃…と、非常に近い形に収束する)。
#      ※2026-07訂正: 当初はこの「形」自体を機械の作法として固定していたが、
#      実際の焙煎では投入温度・ターニングポイント(投入直後に一旦下がって
#      底を打つ点)の深さ・戻ってくるまでの時間も、狙う味に応じて調整する
#      基本的なパラメータであるため、以下の理論に基づいて調整対象に含める。
#      (実機集計で見えた174件の"平均的な形"は、あくまでこの調整の基準点
#      として使う)
#        - 酸味を残したい場合: 投入温度を低め・ターニングポイントの戻りを
#          早めにし、序盤を手早く通過することで揮発しやすい酸のニュアンスを
#          守る。
#        - 甘み・コクを重視する場合: 投入温度を高め・ターニングポイントも
#          深め/戻りを遅めにし、序盤にじっくり熱を入れてメイラード反応の
#          土台を作る(実機174件の集計でも、深煎り(甘み・コクが乗った
#          プロファイル)ほどターニングポイントの時刻が遅い傾向が
#          見えている: 浅煎り平均64.6秒に対し深煎り平均84.4秒)。
#        - 高地産(密度が高い硬い豆)は熱が入りにくいため投入温度を高め・
#          ターニングポイントを浅め(早く回復)にし、低地産(密度が低い豆)は
#          逆に投入温度を抑え、ターニングポイントを深めにして焦げ付きを避ける。
#        - ナチュラル・パルプドナチュラルは糖度が高く焦げやすいため、
#          投入温度を控えめにしターニングポイントを深めにして序盤を慎重に
#          進める。水洗式は本来の酸味を活かす前提で、投入温度・ターニング
#          ポイントとも標準的な速さにする。
#   2) その上で、以下の一般的な焙煎理論に基づいてカーブの「中身」
#      (カラーチェンジ〜1ハゼまでの時間、1ハゼ後の温度の引っ張り方、
#      深煎りの場合は2ハゼ帯にかけての温度)を、味の好みスライダーで調整する。
#        - カラーチェンジ〜1ハゼの時間が長い(ゆっくり上昇)ほど、メイラード
#          反応が進み甘み・ボディが増し、酸味は穏やかになる。逆に短い(急な
#          上昇)ほど、揮発性の高い香気成分や酸味が残りやすい。
#        - 1ハゼ後の温度の上げ方(デベロップメント)が長い・急なほど、
#          カラメル化が進み苦味・コクが増す。短い・緩やかなほど、酸味が
#          残りやすく、未発達な青臭さのリスクとのトレードオフになる。
#        - 深煎りで2ハゼ帯(目安230℃前後)に近づく場合は、最後の区間で
#          上昇を緩める(専門的に「2ハゼ以降は失速させない程度にゆっくり」
#          という定石)ことで、えぐみ・灰っぽさを避ける。
#   3) 産地(標高)・精製方法による、1ハゼ以降(fc_t・end_t)の調整:
#        - 高地産(密度が高い硬い豆)は、発展時間もやや長めに取れる(熱が
#          入りにくい分、時間をかけても焦げにくく、むしろ十分な展開に必要)。
#        - 低地産(密度が低い豆)は、全体をやや短めにする(熱が入りやすく、
#          長く引っ張るとフラットな仕上がりになりやすいため)。
#        - 乾燥式(ナチュラル)・パルプドナチュラルは、発展をやや長め・甘み
#          寄りにする。水洗式はその豆本来の酸味を活かす前提で、1ハゼまでを
#          やや速めにする。
#      (投入温度・ターニングポイントへの標高・精製方法の効果は、上記1)を参照)
#
# ⚠️ 注意: これは実機の炒り上がりを保証するものではなく、あくまで
# 一般的な焙煎理論とTHE ROAST純正プリセットの構造傾向から導いた「たたき台」。
# 生成後は通常のプロファイル編集画面でグラフを確認・調整してから送信すること。
# ============================================================
from __future__ import annotations

import time
from typing import Optional

ROAST_LEVELS = ["浅煎り", "中煎り", "中深煎り", "深煎り"]

# THE ROAST EXPERT仕様(温度255℃上限・最大20ポイント・焙煎時間最大900秒)を
# 超えないよう、生成側でも同じ上限を参照する。
MAX_TEMPERATURE = 255
MAX_ROAST_SECONDS = 15 * 60
MIN_FAN = 50
MAX_FAN = 100

# 風量カーブの基準値(実機プリセット174件を、温度カーブ・焙煎度と突き合わせて解析した結果)。
# 実態: 投入50% → 約1秒で80%へ急上昇 → なだらかに単調減少。1ハゼを境に排気の勾配が
# 変わり(前=急・後=緩)、深い焙煎ほど発達期(1ハゼ)・終盤の風量を高めに保つ。終盤で
# 風量を上げる個体は161件中0件。フェーズ境界(カラーチェンジ・1ハゼ)にアンカーを置く。
_FAN_CHARGE = MIN_FAN          # 投入時(全焙煎度で一律)
_FAN_PEAK = 80                 # 投入直後のピーク(約1秒で到達)
_FAN_PEAK_T = 1
_FAN_CC = 67                   # カラーチェンジ時(焙煎度によらずほぼ一定)
# 1ハゼ時・終了時は焙煎度依存(深いほど高め)。プリセット中央値に基づく。
_FAN_FC_BY_LEVEL = {"浅煎り": 59, "中煎り": 61, "中深煎り": 62, "深煎り": 63}
_FAN_END_BY_LEVEL = {"浅煎り": 56, "中煎り": 56, "中深煎り": 58, "深煎り": 58}


def _abc_fan_curve(roast_level, t_cc, t_fc, end_t):
    """フェーズ境界(カラーチェンジ=t_cc・1ハゼ=t_fc)にアンカーした風量カーブを返す。
    投入50 → ピーク80(t=1) → カラーチェンジ67 → 1ハゼ(焙煎度依存) → 終了(焙煎度依存)。
    区間の勾配差で「1ハゼでの折れ(前=急・後=緩)」が自然に出る。"""
    fan_fc = _FAN_FC_BY_LEVEL.get(roast_level, 60)
    fan_end = _FAN_END_BY_LEVEL.get(roast_level, 57)
    raw = [[0, _FAN_CHARGE], [_FAN_PEAK_T, _FAN_PEAK],
           [t_cc, _FAN_CC], [t_fc, fan_fc], [end_t, fan_end]]
    out: list[list[int]] = []
    for x, y in sorted(raw, key=lambda p: p[0]):
        xi = int(round(x))
        yi = int(round(_clamp(y, MIN_FAN, MAX_FAN)))
        if out and xi <= out[-1][0]:
            xi = out[-1][0] + 1
        out.append([xi, yi])
    return out

# 実機プリセット174件の集計に基づく、焙煎度ごとの基準値。
# ※2026-07再校正: 「味を推測」機能で実プリセット(焙煎度が既知)を検証したところ、
# 従来の基準値では焙煎度の判定がしばしば実際とズレる(正答率65.8%)ことが判明した。
# 原因は、当初この基準値が理論値として設定されており、実プリセット174件の
# 終了温度・終了時間の実測中央値と数℃・数十秒のズレがあったため。実測中央値
# (浅煎り226℃/435秒、中煎り240℃/518秒、中深煎り248℃/540秒、深煎り252℃/600秒)
# に合わせて校正し直した(判定正答率65.8%→82.6%に改善。実プリセットの焙煎度
# ラベル自体、境界付近では焙煎士の主観に依るブレがあるため、これ以上の完全一致は
# 見込めない)。charge/dip/dip_tは焙煎度による実測差がほぼ見られなかった
# (標高・精製方法による調整のみで焙煎度によらず共通の基準値とする)ため、
# 全項目(charge/dip/dip_tを含む)を、下記の味の好み・産地・精製方法で
# 調整する。ここに書かれているのはあくまで調整前の出発点(標準=3の場合の値)。
# charge/dip/dip_tは、ABC生成側(_ABC_CHARGE_BASE / _ABC_DIP_TEMP / _ABC_DIP_T)と同じ
# 実測の最頻値に合わせてある(2026-08再検証。投入185℃・ボトム95℃・ボトム到達60秒)。
_BASE_TEMPLATES = {
    "浅煎り":   {"charge": 185, "dip": 95, "dip_t": 60, "fc_t": 227, "fc_temp": 187, "end_t": 435, "end_temp": 226},
    "中煎り":   {"charge": 185, "dip": 95, "dip_t": 60, "fc_t": 251, "fc_temp": 190, "end_t": 518, "end_temp": 240},
    "中深煎り": {"charge": 185, "dip": 95, "dip_t": 60, "fc_t": 252, "fc_temp": 193, "end_t": 540, "end_temp": 248},
    "深煎り":   {"charge": 185, "dip": 95, "dip_t": 60, "fc_t": 264, "fc_temp": 195, "end_t": 600, "end_temp": 252},
}

# 焙煎度ごとの、終了温度の上限(THE ROAST EXPERT全体の255℃上限とは別に、
# 「浅煎りなのに深煎り並みの温度」にならないよう、味スライダーの調整幅を
# 各焙煎度らしい範囲に収めるための緩やかな上限)。基準値の再校正(上記)に
# 合わせて、隣接焙煎度との相対的な余白を保ったまま更新。
_END_TEMP_CEILING = {"浅煎り": 236, "中煎り": 248, "中深煎り": 253, "深煎り": 255}
_END_TEMP_FLOOR = {"浅煎り": 209, "中煎り": 226, "中深煎り": 236, "深煎り": 242}

# 焙煎度ごとの、終了時間の上下限。味スライダー(苦味・コク・アフターノートは
# 終了時間を伸ばす方向、酸味は縮める方向)を極端な組み合わせで同時に選ぶと、
# 例えば「深煎りを選んだのに酸味MAX・苦味MINで実質的に中煎り並みの短さに
# なる」といった、選んだ焙煎度と矛盾する結果になりうる。終了温度と同じ考え方で、
# 隣接する焙煎度自身の基準値を超えない範囲(=1段階分の変化までは許容するが、
# 焙煎度を飛び越えない範囲)に収める。基準値の再校正(上記)に合わせて更新。
_END_T_CEILING = {"浅煎り": 518, "中煎り": 540, "中深煎り": 600, "深煎り": 670}
_END_T_FLOOR = {"浅煎り": 350, "中煎り": 435, "中深煎り": 518, "深煎り": 540}

# 標高: 密度が高い(高地)ほど投入温度を上げ・ターニングポイントを浅く/早く
# 戻し、密度が低い(低地)ほど投入温度を下げ・ターニングポイントを深く/遅く
# 戻す(低地は熱が入りやすく急激な立ち上がりだと焦げやすいため)。
_ALTITUDE_ADJUST = {
    # (charge_delta, dip_delta, dip_t_delta, fc_t_mult, end_t_mult)
    "2000m以上": (8, 5, -8, 1.06, 1.05),
    "1500-2000m": (4, 2, -4, 1.03, 1.025),
    "1000-1500m": (0, 0, 0, 1.0, 1.0),
    "1000m未満": (-6, -4, 6, 0.95, 0.97),
}

# 精製方法: 糖度が高く焦げやすいナチュラル系は投入温度を控えめにし、
# ターニングポイントを深め・戻りを遅めにして序盤を慎重に進める。水洗式は
# 標準よりやや速め。スマトラ式(ジャイリンバサ、湿式脱穀)は独特の密度・
# 含水率を踏まえてやや高めで安定させる。
_PROCESS_ADJUST = {
    # (charge_delta, dip_delta, dip_t_delta, end_temp_delta, end_t_mult, fc_t_mult)
    "水洗式": (-2, 0, -3, -2, 1.0, 0.97),
    "パルプドナチュラル式": (-2, -2, 4, 1, 1.01, 1.0),
    "乾燥式": (-4, -3, 6, 2, 1.02, 1.0),
    "スマトラ式": (2, 2, -2, 3, 1.04, 0.98),
}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _clamp5(v: float) -> int:
    return int(_clamp(round(v), 1, 5))


def _rising_crossing(pts: list, target: float) -> Optional[float]:
    """ディップ(最低温)後の上昇局面で、targetを初めて上回る時刻を線形補間で返す。

    プロファイルは投入→ディップ→上昇の形をしており、投入直後の下降局面でも
    targetを横切りうるため、必ずディップ以降だけを見る。見つからなければNone。
    """
    if len(pts) < 2:
        return None
    lo_i = min(range(len(pts)), key=lambda i: pts[i][1])
    for i in range(lo_i, len(pts) - 1):
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        if y0 <= target <= y1 and y1 > y0:
            return x0 + (x1 - x0) * (target - y0) / (y1 - y0)
    return None


def _apply_guide_shape(points: list, guide_temps: dict, d_target: float, fc_shift: float = 0.0) -> list:
    """温度ガイド線(カラーチェンジ・1ハゼ)を使い、Bフェーズを明示した制御点列に
    組み替える(Jake Hu氏のABC理論に基づく処理)。

    1) 現在の計画カーブがカラーチェンジ温度・1ハゼ温度を(ディップ後の上昇局面で)
       横切る時刻 t_cc・t_fc を求める
    2) その2点を明示的な制御点として置き、間にあった既存の制御点を取り除く
    3) Bフェーズを3分割し、RoRが線形に変化していく3区間(等間隔の制御点2つ)で、
       後半RoR-前半RoR(2分割測定)が d_target(℃/分)になるよう温度を配分する
       (d_target<0=前半に熱を入れる減少形、>0=増加形)

    fc_shift: 1ハゼ到達時刻(t_fc)を指定秒だけ前後させ、B⇔Cの時間配分を変える
    (3軸モードの「甘さの系統」。負=Bを短くCへ配分=カラメル寄り、正=フルーティ寄り。
     中間アンカーをずらすだけでは両交点が比例して動き配分が変わらないため、
     交点そのものを動かす)。

    横切る点が見つからない・B区間が短すぎる場合は、元の制御点列をそのまま返す
    (ガイド温度がこのプロファイルの温度帯と合っていないケース)。
    """
    cc = guide_temps.get("colorChange")
    fc = guide_temps.get("firstCrack")
    if cc is None or fc is None or fc <= cc:
        return points
    t_cc = _rising_crossing(points, cc)
    t_fc_orig = _rising_crossing(points, fc)
    if t_cc is None or t_fc_orig is None or t_fc_orig - t_cc < 60:
        return points

    end_t = points[-1][0]
    t_fc = _clamp(t_fc_orig + fc_shift, t_cc + 60, end_t - 45)

    b_min = (t_fc - t_cc) / 60.0
    # 3分割の端区間同士のRoR差Δ。analyze_abc_phases()やプリセット統計は
    # 「前半/後半の2分割」でRoR差を測り、線形RoR変化の3分割では測定値が
    # (2/3)Δになるため、Δ=1.5×d_targetとすれば測定値=d_targetになる
    # (generate_profile_abc()と同じ校正)。
    r_avg = (fc - cc) / b_min
    delta = 1.5 * d_target
    b1_t = t_cc + (t_fc - t_cc) / 3
    b2_t = t_cc + 2 * (t_fc - t_cc) / 3
    b1_temp = _clamp(cc + (r_avg - delta / 2) * b_min / 3, cc + 1, fc - 3)
    b2_temp = _clamp(cc + (2 * r_avg - delta / 2) * b_min / 3, b1_temp + 1, fc - 1)

    # 旧B区間と新B区間の両方に掛かる既存点を取り除く(fc_shiftで交点を動かした
    # 場合、旧交点と新交点の間に残った点は温度の単調性を壊すため)。
    remove_until = max(t_fc_orig, t_fc)
    kept = [p for p in points if p[0] < t_cc - 1 or p[0] > remove_until + 1]
    kept.extend([
        [round(t_cc), round(cc)],
        [round(b1_t), round(b1_temp)],
        [round(b2_t), round(b2_temp)],
        [round(t_fc), round(fc)],
    ])
    kept.sort(key=lambda p: p[0])
    cleaned: list[list[float]] = []
    for x, y in kept:
        if cleaned and x <= cleaned[-1][0]:
            x = cleaned[-1][0] + 1
        cleaned.append([x, round(y)])
    return cleaned


def generate_profile(
    roast_level: str,
    altitude_bucket: str = "",
    process: str = "",
    acidity: int = 3,
    sweetness: int = 3,
    bitterness: int = 3,
    body: int = 3,
    aftertaste: int = 3,
    country: str = "",
    guide_temps: Optional[dict] = None,
) -> dict:
    """味の好み・産地・精製方法から、焙煎プロファイル(roast/fan/cooldown)を生成する。

    acidity/sweetness/bitterness/body/aftertaste は 1〜5 の5段階
    (3が標準)。roast_levelは"浅煎り"/"中煎り"/"中深煎り"/"深煎り"。
    guide_temps({"colorChange":.., "firstCrack":..}、温度ガイド線設定)を渡すと、
    Jake Hu氏のABC理論に基づく追加処理を行う(v0.11.48):
      - カラーチェンジ温度を横切る位置に制御点を明示(Bフェーズ開始が見える)
      - カラーチェンジ〜1ハゼ(Bフェーズ)内の温度配分を、甘み・コク(前半に熱を
        入れる=RoR減少形)/酸味(後半に駆け上がる=RoR増加形)スライダーと連動
    渡さない場合は従来と同一の出力(既存の校正を壊さないため)。
    戻り値は {"name", "roast", "fan", "cooldown"} で、そのままプロファイル
    エディタに読み込める形式(送信・保存前に必ず内容を確認すること)。
    """
    if roast_level not in _BASE_TEMPLATES:
        roast_level = "中煎り"

    acidity, sweetness, bitterness, body, aftertaste = (
        _clamp5(acidity), _clamp5(sweetness), _clamp5(bitterness), _clamp5(body), _clamp5(aftertaste)
    )
    da, ds, db_, dbody, daf = (acidity - 3, sweetness - 3, bitterness - 3, body - 3, aftertaste - 3)

    t = dict(_BASE_TEMPLATES[roast_level])

    # ---- 味の好みスライダーによる調整(理論ベース) ----
    # 酸味: 投入温度を下げ・ターニングポイントを深く/早く戻すことで、序盤を
    # 手早く通過させる。あわせてカラーチェンジ〜1ハゼも速める・浅めにする
    # ことで、酸が残りやすくなる。
    t["charge"] += da * -4
    t["dip"] += da * -3
    t["dip_t"] += da * -6
    t["fc_t"] -= da * 15
    t["fc_temp"] -= da * 2
    t["end_temp"] -= da * 3
    t["end_t"] -= da * 10

    # 甘み: 投入温度を上げ・ターニングポイントを浅く/遅く戻すことで、序盤に
    # じっくり熱を入れる。あわせてメイラード期間(カラーチェンジ〜1ハゼ)も
    # ゆっくりにするほど甘みが乗る。
    t["charge"] += ds * 4
    t["dip"] += ds * 3
    t["dip_t"] += ds * 6
    t["fc_t"] += ds * 12
    t["end_temp"] += ds * 1.5

    # 苦味: 1ハゼ後の展開(温度・時間)を強めるほど、カラメル化が進み苦味が増す。
    # 投入温度もわずかに上げ、序盤からしっかり熱を入れる方向に寄せる。
    t["charge"] += db_ * 1.5
    t["end_temp"] += db_ * 5
    t["end_t"] += db_ * 8

    # コク: 投入温度を上げ・ターニングポイントを浅く/遅く戻すことで、序盤の
    # 熱量(質量あたりの蓄熱)を増やす。全体もやや長めにし、ボディ感を
    # 強める方向に寄せる。
    t["charge"] += dbody * 3
    t["dip"] += dbody * 2
    t["dip_t"] += dbody * 4
    t["end_t"] += dbody * 12

    # アフターノート: 全体をやや長めにし、終盤の展開を緩めて(後述のプラトー
    # 処理と合わせて)余韻が残る方向に寄せる。
    t["end_t"] += daf * 10
    plateau = daf >= 1  # 終盤で温度上昇を緩め、余韻を強める

    # ---- 産地(標高)による調整 ----
    charge_delta, alt_dip_delta, alt_dip_t_delta, fc_mult, end_mult = _ALTITUDE_ADJUST.get(
        altitude_bucket, (0, 0, 0, 1.0, 1.0)
    )
    t["charge"] += charge_delta
    t["dip"] += alt_dip_delta
    t["dip_t"] += alt_dip_t_delta
    t["fc_t"] *= fc_mult
    t["end_t"] *= end_mult

    # ---- 精製方法による調整 ----
    proc_charge_delta, dip_delta, proc_dip_t_delta, end_temp_delta, end_t_mult, fc_t_mult = _PROCESS_ADJUST.get(
        process, (0, 0, 0, 0, 1.0, 1.0)
    )
    t["charge"] += proc_charge_delta
    t["dip"] += dip_delta
    t["dip_t"] += proc_dip_t_delta
    t["end_temp"] += end_temp_delta
    t["end_t"] *= end_t_mult
    t["fc_t"] *= fc_t_mult

    # ---- Hu理論の調整単位への量子化(v0.11.48) ----
    # Jake Hu氏のABC理論では、味に意味のある変化が出る最小単位は
    # Bフェーズ(≒1ハゼまでの中盤)で15〜20秒、Cフェーズ(1ハゼ以降)で3〜5秒
    # とされる(Q10則: 温度10℃上昇で反応速度2倍、に基づく)。それより細かい
    # 端数は「味に効かないノイズ」なので、基準値からの調整量を単位に丸める。
    base_tmpl = _BASE_TEMPLATES[roast_level]
    t["fc_t"] = base_tmpl["fc_t"] + round((t["fc_t"] - base_tmpl["fc_t"]) / 15) * 15
    t["end_t"] = base_tmpl["end_t"] + round((t["end_t"] - base_tmpl["end_t"]) / 5) * 5

    # ---- 焙煎度らしい範囲・THE ROAST EXPERT仕様の上限に収める ----
    t["charge"] = _clamp(t["charge"], 145, 210)
    t["dip"] = _clamp(t["dip"], 70, 120)
    t["dip"] = min(t["dip"], t["charge"] - 50)  # 投入からの下落幅は最低50℃を保証する
    t["dip_t"] = _clamp(t["dip_t"], 25, 95)
    t["end_temp"] = _clamp(t["end_temp"], _END_TEMP_FLOOR[roast_level], min(_END_TEMP_CEILING[roast_level], MAX_TEMPERATURE))
    t["end_t"] = _clamp(
        t["end_t"],
        max(t["dip_t"] + 180, _END_T_FLOOR[roast_level]),
        min(_END_T_CEILING[roast_level], MAX_ROAST_SECONDS),
    )
    t["fc_t"] = _clamp(t["fc_t"], t["dip_t"] + 60, t["end_t"] - 90)
    t["fc_temp"] = _clamp(t["fc_temp"], t["dip"] + 40, t["end_temp"] - 10)

    charge, dip, dip_t = round(t["charge"]), round(t["dip"]), round(t["dip_t"])
    fc_t, fc_temp = round(t["fc_t"]), round(t["fc_temp"])
    end_t, end_temp = round(t["end_t"]), round(t["end_temp"])

    # 120秒時点の目安温度(実機プリセットで一貫して見られる、ディップからの
    # 回復途中のポイント)。ディップ〜1ハゼ想定温度の間を、経過時間の比で補間する。
    t120 = 120 if 120 > dip_t and 120 < fc_t else round((dip_t + fc_t) / 2)
    ratio_120 = (t120 - dip_t) / max(1, (fc_t - dip_t))
    temp_120 = round(dip + (fc_temp - dip) * min(0.9, max(0.1, ratio_120 * 0.75)))

    # 1ハゼ後の展開中間点。苦味が強いほど、1ハゼ直後から一気に温度を
    # 引っ張る(RoRが高い)配分にする。
    dev_ratio = 0.35 if bitterness >= 4 else (0.65 if acidity >= 4 else 0.5)
    dev_t = round(fc_t + (end_t - fc_t) * dev_ratio)
    dev_temp = round(fc_temp + (end_temp - fc_temp) * (0.5 if not plateau else 0.42))

    points = [(0, charge), (dip_t, dip), (t120, temp_120), (fc_t, fc_temp)]

    if roast_level == "深煎り":
        # 深煎りは2ハゼ帯(目安230℃前後)を通過するため、そこを一つの制御点として
        # 明示し、最後の区間の上昇を緩めて(専門的に言う「失速させすぎない範囲で
        # 2ハゼ以降はゆっくり」)、えぐみ・灰っぽさを避ける。
        second_crack_temp = round(min(232, end_temp - 6))
        second_crack_t = round(fc_t + (end_t - fc_t) * 0.55)
        if second_crack_t <= dev_t:
            second_crack_t = dev_t + 20
        points.append((dev_t, dev_temp))
        points.append((second_crack_t, second_crack_temp))
    else:
        points.append((dev_t, dev_temp))

    points.append((end_t, end_temp))

    # 制御点の時間が重複・逆転しないよう、最低1秒間隔を保証しながら整列する。
    points = sorted(points, key=lambda p: p[0])
    cleaned: list[list[float]] = []
    for x, y in points:
        if cleaned and x <= cleaned[-1][0]:
            x = cleaned[-1][0] + 1
        cleaned.append([x, round(y)])

    # ---- 温度ガイド線が設定されていれば、Bフェーズ(カラーチェンジ〜1ハゼ)を
    # 明示し、B内の温度配分を味スライダーと連動させる(v0.11.48、Hu理論) ----
    if guide_temps:
        # 甘み・コクを強めるほど前半に熱を入れる(RoR減少形)、酸味を強める
        # ほど後半に駆け上がる(RoR増加形)。基準はプリセット標準の-8℃/分
        # (実測173件の中央値-8.4℃/分)。
        d_target = -8.0 - (ds + dbody) * 1.5 + da * 2.0
        d_target = _clamp(d_target, -14.0, 10.0)
        cleaned = _apply_guide_shape(cleaned, guide_temps, d_target)

    # ---- 風量カーブ ----
    # 序盤は純正プリセットに共通する立ち上がり(50%→80%)を踏襲。終盤の風量は、
    # 酸味重視ならやや高め(クリーンに仕上げる)、甘み・コク重視ならやや低め
    # (熱をこもらせてカラメル化を後押しする)に振る。
    fan_end = _clamp(56 + da * 4 - ds * 3 - dbody * 3, MIN_FAN, MAX_FAN)
    fan_hold_t = min(6, max(2, dip_t // 10))
    fan = [[0, MIN_FAN], [1, 80], [fan_hold_t, 78], [cleaned[-1][0], round(fan_end)]]
    fan_cleaned: list[list[float]] = []
    for x, y in fan:
        if fan_cleaned and x <= fan_cleaned[-1][0]:
            x = fan_cleaned[-1][0] + 1
        fan_cleaned.append([x, round(y)])

    cooldown = [cleaned[-1][0] + 120, 60]

    name_parts = [p for p in [country, altitude_bucket, process, roast_level] if p]
    name = " ".join(name_parts) + "(自動生成)" if name_parts else f"自動生成プロファイル({roast_level})"

    # UUIDは実機との通信で使う16桁の数字文字列(roastlib.ble.session.profile_from_points
    # が要求する形式)。自動生成プロファイルは実プリセットに由来しないため、
    # ミリ秒精度のタイムスタンプを16桁ゼロ埋めして割り当てる(実プリセットのUUID体系
    # とは無関係の、この保存用に便宜上発行する値)。
    uuid_ascii = str(int(time.time() * 1000)).zfill(16)

    return {
        "name": name,
        "uuid": uuid_ascii,
        "roast": cleaned,
        "fan": fan_cleaned,
        "cooldown": cooldown,
        "params": {
            "roast_level": roast_level, "altitude_bucket": altitude_bucket, "process": process,
            "country": country, "acidity": acidity, "sweetness": sweetness,
            "bitterness": bitterness, "body": body, "aftertaste": aftertaste,
        },
    }


# ============================================================
# 逆推定: 焙煎カーブ(制御点)から、焙煎度・味の傾向を推測する
# ------------------------------------------------------------
# generate_profile()で使った係数を逆向きに当てはめる、あくまで目安の推測。
#
# ⚠️ 構造上の限界: 酸味・甘みは、投入温度・ターニングポイントの深さ/時間
# という同じカーブ上の特徴に、互いに逆向きの係数で効いてくる(酸味を上げるのと
# 甘みを下げるのがほぼ同じ方向にカーブを動かす)。そのため、この2つを
# カーブから独立に読み取ることはできず、「明るさ(酸味寄り)⇔リッチさ
# (甘み寄り)」という一つの軸として推測する(片方が高ければもう片方は低いと
# 判定される)。産地(標高)・精製方法も、投入温度に似た効き方をするため、
# カーブだけからは味の好みと切り分けられない(推測結果には含めない)。
# ============================================================
def _interp_at(pts: list, t: float) -> float:
    """制御点列(t昇順)から、任意の時刻tの温度を線形補間で求める。"""
    if t <= pts[0][0]:
        return pts[0][1]
    if t >= pts[-1][0]:
        return pts[-1][1]
    for i in range(1, len(pts)):
        if pts[i][0] >= t:
            x0, y0 = pts[i - 1]
            x1, y1 = pts[i]
            if x1 == x0:
                return y1
            ratio = (t - x0) / (x1 - x0)
            return y0 + (y1 - y0) * ratio
    return pts[-1][1]


def infer_roast_level(end_temp: float, end_t: float) -> str:
    # 各焙煎度の基準テンプレートのうち、終了温度・終了時間が最も近いものを採用する。
    # 重み(終了時間1秒 = 終了温度0.1℃相当)は、実プリセット161件(焙煎度既知)で
    # 判定正答率が最も高かった値(2026-07校正、65.8%→82.6%)。
    best, best_dist = "中煎り", None
    for lvl, tmpl in _BASE_TEMPLATES.items():
        dist = abs(end_temp - tmpl["end_temp"]) + abs(end_t - tmpl["end_t"]) * 0.1
        if best_dist is None or dist < best_dist:
            best, best_dist = lvl, dist
    return best


def _late_ror_ratio(pts: list, dip_t: float, end_t: float) -> tuple:
    """終盤を2区間(全体の55〜85%、85%〜終了)に分け、それぞれのRoR(温度上昇率)
    を返す。generate_profile()のdev_ratio(苦味が強い時は0.35=早めに温度を
    引っ張り終盤は失速、アフターノートを強めた時は逆に終盤で加速)を、
    カーブの前半/後半のRoR比較から読み取るための共通ヘルパー。
    """
    span = end_t - dip_t
    if span <= 0:
        return 0.0, 0.0
    mid_t = dip_t + span * 0.55
    late_t = dip_t + span * 0.85
    mid_temp = _interp_at(pts, mid_t)
    late_temp = _interp_at(pts, late_t)
    end_temp = pts[-1][1]
    mid_ror = (late_temp - mid_temp) / max(1, (late_t - mid_t))
    end_ror = (end_temp - late_temp) / max(1, (end_t - late_t))
    return mid_ror, end_ror


def _decel_signal(pts: list, dip_t: float, end_t: float) -> float:
    """終盤のRoRが、その手前より失速しているほど0〜1で高い値を返す
    (苦味を強めた時のdev_ratio=0.35〈1ハゼ後すぐに引っ張り、終盤は失速〉
    の兆候)。苦味の判定を補強するための信号。
    """
    mid_ror, end_ror = _late_ror_ratio(pts, dip_t, end_t)
    if mid_ror <= 0:
        return 0.0
    return max(0.0, min(1.0, 1 - (end_ror / mid_ror)))


def _accel_signal(pts: list, dip_t: float, end_t: float) -> float:
    """終盤のRoRが、その手前より加速しているほど0以上の値を返す
    (アフターノートを強めた時、終盤にかけて上昇が速まる兆候。ただし
    酸味を強めた時のdev_ratio=0.65〈序盤ゆっくり、終盤に追い上げ〉でも
    同様の形になるため、呼び出し側でbrightness軸と重ならない範囲に限定
    して使うこと)。
    """
    mid_ror, end_ror = _late_ror_ratio(pts, dip_t, end_t)
    if mid_ror <= 0:
        return 0.0
    return max(0.0, (end_ror / mid_ror) - 1)


def infer_taste_profile(roast_points: list, guide_temps: Optional[dict] = None) -> dict:
    """焙煎カーブ(制御点)から、想定される焙煎度と味の傾向(5段階)を推測する。

    roast_points: [[t, 温度], ...] のリスト(t昇順でなくてもよい)。
    guide_temps(温度ガイド線設定)を渡すと、カーブからA/B/Cフェーズの時間と
    Bフェーズ内RoRの傾向を計算し(Jake Hu氏のABC理論)、酸味・甘みの推測に
    反映する。結果の "abc" キーにフェーズ分析結果も含める(算出できない場合はNone)。

    ⚠️ 設計メモ(2026-07訂正): 当初は焙煎度を先に1つ判定し、その基準
    テンプレートとの差分から味を逆算する方式だったが、味の好みスライダー
    自体が終了温度・終了時間を動かすため、判定した焙煎度が隣の帯にずれると
    差分の符号ごと反転してしまう不具合があった(例: 苦味を最小にしたのに
    「苦味が強い」と判定される)。そのため、焙煎度の判定と味の推測を分離し、
    味の推測には特定の焙煎度テンプレートに依存しない、カーブ自身の形だけで
    完結する特徴量(投入温度・ターニングポイントは焙煎度によらずほぼ一定の
    基準値との比較、苦味・アフターノートは同じカーブの中での前半/後半の
    伸び方の比較)を使うようにした。焙煎度の判定は表示用のラベルとして
    別途行うのみで、味の推測結果には影響しない。
    戻り値には "note" として、この推測の限界(酸味・甘みは独立に判定できない等)
    を含める。
    """
    pts = sorted((list(p) for p in roast_points), key=lambda p: p[0])
    if len(pts) < 2:
        raise ValueError("roast_pointsが不足しています(2点以上必要)")

    charge = pts[0][1]
    end_t, end_temp = pts[-1]

    early = [p for p in pts if p[0] <= 150] or pts[:2]
    dip_point = min(early, key=lambda p: p[1])
    dip_temp, dip_t = dip_point[1], dip_point[0]

    roast_level = infer_roast_level(end_temp, end_t)  # 表示用ラベル。以下の味推測には使わない。

    # 「明るさ(酸味寄り)⇔リッチさ(甘み・コク寄り)」軸。投入温度・ターニング
    # ポイントの深さ/時間は、_BASE_TEMPLATES上、焙煎度によらずほぼ一定
    # (184℃・97℃・59秒前後、実プリセット174件の中央値に基づく)なので、
    # その固定値との差分を使う(焙煎度の判定結果には依存しない)。
    charge_delta = charge - _BASE_TEMPLATES["浅煎り"]["charge"]
    dip_delta = dip_temp - _BASE_TEMPLATES["浅煎り"]["dip"]
    dip_t_delta = dip_t - _BASE_TEMPLATES["浅煎り"]["dip_t"]
    brightness = -(charge_delta / 4 + dip_delta / 3 + dip_t_delta / 6) / 3

    # 終了温度・終了時間から、苦味を分離する。generate_profile()では、
    # 「浅煎り→深煎り」も「苦味を上げる」も、どちらも終了温度・終了時間を
    # 増やす方向に働くため、単純な差分では両者を区別できない
    # (実際、これが原因で「苦味を最小にしたのに苦味が強いと判定される」
    # 不具合になっていた)。そこで、浅煎り基準(_BASE_TEMPLATES実測値)からの
    # 変化量を、「焙煎度が進んだ分の方向(浅煎り→深煎りの変化幅)」と
    # 「苦味1単位あたりの方向(+5℃・+8秒、generate_profile()のdb_係数)」
    # という2つのベクトルの合成とみなし、連立方程式として解く
    # (2元1次方程式なのでクラメルの公式で厳密に解ける)。
    _LEVEL_ANCHOR_TEMP = _BASE_TEMPLATES["浅煎り"]["end_temp"]
    _LEVEL_ANCHOR_T = _BASE_TEMPLATES["浅煎り"]["end_t"]
    _LEVEL_TEMP = _BASE_TEMPLATES["深煎り"]["end_temp"] - _LEVEL_ANCHOR_TEMP
    _LEVEL_T = _BASE_TEMPLATES["深煎り"]["end_t"] - _LEVEL_ANCHOR_T
    _BIT_TEMP, _BIT_T = 5.0, 8.0         # 苦味(1単位)あたりの終了温度・終了時間の効き方
    det = _LEVEL_TEMP * _BIT_T - _BIT_TEMP * _LEVEL_T
    dtemp = end_temp - _LEVEL_ANCHOR_TEMP
    dtime = end_t - _LEVEL_ANCHOR_T
    t_level = (dtemp * _BIT_T - _BIT_TEMP * dtime) / det
    t_bitter = (_LEVEL_TEMP * dtime - _LEVEL_T * dtemp) / det

    # 注意: 上の連立方程式は2元1次(未知数2つ)なので、(end_temp, end_t)の
    # 2つの実測値だけからは常にぴったり解けてしまい、「説明しきれない残差」は
    # 構造的に生まれない(=0になる)。コク・アフターノートは苦味と同じ方向
    # (終了時間だけを伸ばす)に効くため、終了温度・終了時間の2値だけからは
    # 苦味と完全には分離できない。そのため、コクは「明るさ⇔リッチさ」軸
    # (brightness、序盤のカーブ形状に由来。generate_profile()でもdbodyは
    # 投入温度・ターニングポイントを甘みと同じ向きに動かすため、実際の
    # 効き方と整合する)を主な手がかりにし、アフターノートは、終盤のRoRが
    # 加速するという
    # dev_ratio由来の固有の形状変化(_accel_signal)を主な手がかりにする
    # (どちらも苦味の連立方程式とは別の、カーブ形状の別の側面を見ている)。
    decel = _decel_signal(pts, dip_t, end_t)  # 苦味(dev_ratio=0.35)の補強シグナル
    accel = _accel_signal(pts, dip_t, end_t)  # アフターノート(またはdev_ratio=0.65)の兆候
    # 酸味が強い場合もほぼ同じ終盤加速の形になるため、brightnessが酸味寄りに
    # 大きく振れている分は差し引く(二重計上を避ける)。
    accel_for_aftertaste = accel * max(0.0, 1 - abs(brightness) / 2)

    # 焙煎理論上、酸味・苦味は「味の好みスライダー」以前に焙煎度そのものに
    # 強く連動する(浅煎りほど酸が残り苦味は弱く、深煎りほど酸は減り苦味・
    # コクが強まる)。t_levelは浅煎り=0・深煎り=1相当の連続値なので、これを
    # 主要な軸として酸味・苦味・コクのベースラインを決め、カーブ形状由来の
    # 信号(brightness・t_bitter・decel)は、その焙煎度なりの標準からの
    # 「ズレ」を表す補助的な補正として重みを下げて加える(これにより、
    # 「深煎りなのに酸味が高く苦味が少ない」のような焙煎度と矛盾する推測結果を防ぐ)。
    level_pos = _clamp(t_level, -0.3, 1.3)  # 0=浅煎り相当 〜 1=深煎り相当(多少の外挿を許容)
    level_bias = level_pos - 0.5

    acidity_f = 3 - level_bias * 3.0 + brightness * 1.2
    sweetness_f = 3 + level_bias * 1.0 - brightness * 1.2
    bitterness_f = 3 + level_bias * 3.0 + t_bitter * 0.6 + decel * 0.6
    body_f = 3 + level_bias * 1.5 - brightness * 0.8
    aftertaste_f = 3 + level_bias * 0.8 + accel_for_aftertaste * 6.0

    # 温度ガイド線があれば、ABCフェーズ分析(Bフェーズの長さ・B内RoRの傾向)を
    # 追加の手がかりにする(Jake Hu氏の理論: BのRoRが低い=前半に熱を入れるほど
    # 酸はやわらかく、高いほど酸が強く出る。Bが長いほどフルーティで柔らかい酸)。
    abc = analyze_abc_phases(pts, guide_temps) if guide_temps else None
    if abc:
        # プリセット標準(実測173件の中央値: RoR変化-8.4℃/分・B156秒)との差分を、
        # 既存の推測を壊さない控えめな重みで加える。
        ror_delta = abc["b_ror_diff"] - _PRESET_B_ROR_DIFF_MEDIAN
        acidity_f += _clamp(ror_delta * 0.05, -0.8, 0.8)
        b_units = (abc["b_sec"] - _PRESET_B_SEC_MEDIAN) / 15.0
        acidity_f -= _clamp(b_units * 0.1, -0.6, 0.6)
        sweetness_f += _clamp(b_units * 0.08, -0.5, 0.5)

    acidity = _clamp5(acidity_f)
    sweetness = _clamp5(sweetness_f)
    bitterness = _clamp5(bitterness_f)
    body = _clamp5(body_f)
    aftertaste = _clamp5(aftertaste_f)

    return {
        "roast_level": roast_level,
        "acidity": acidity, "sweetness": sweetness, "bitterness": bitterness,
        "body": body, "aftertaste": aftertaste,
        "abc": abc,
        "note": (
            "焙煎度(浅煎り〜深煎り)を主な軸にし、投入温度・ターニングポイント・"
            "終盤の伸び方といったカーブ形状をその焙煎度なりの標準からの補正として"
            "組み合わせた目安です。酸味と甘み・コクはカーブ上の同じ特徴を逆方向に"
            "見ているため独立には判定できず、一つの軸として推測しています。"
            "苦味・コク・アフターノートも終了時間を伸ばす向きに似た効き方をする"
            "ため、境界付近では互いに影響し合うことがあります。産地・精製方法の"
            "影響はこの推測に含まれていません。"
        ),
    }


# ============================================================
# ABCモード(Jake Hu氏のABC焙煎理論に基づくプロファイル生成)
# ------------------------------------------------------------
# Jake Hu氏(Taster's Coffee創業者・WCRCヘッドジャッジ)のABC理論:
#   - 焙煎を A(投入→カラーチェンジ) / B(→1ハゼ) / C(→終了) に分け、
#     従来の「乾燥・メイラード・デベロップメント」という不正確な用語を使わない
#   - 温度10℃上昇で化学反応速度は2倍(Q10則)。そのため味に効く調整単位は
#     フェーズごとに異なる: A=30秒 / B=15〜20秒 / C=3〜5秒
#   - 設計はC→B→Aの順(味への影響が大きい順)
#   - 味への効果(2026-03セミナー、実カッピングでの説明):
#       A長く/高エネルギー → ざらつきが減り、ボディが強くなる
#       B長い → フルーティで柔らかい酸。短い → 酸が明るく尖り、未熟な甘味、
#               クロロゲン酸の分解不足でザラザラ感が増す
#       BのRoRが低い → やわらかい酸。高い → 強い酸
#       C長い → キャラメル感が増し、酸が少なくなる
#
# フェーズ境界は「温度ガイド線設定」(guide_temps.json、ユーザーが実機を目視
# しながら決めた熱源温度ベースのカラーチェンジ・1ハゼ温度)を使う。
# プロファイル温度は熱源温度であり実豆温度より高く表示されるが、ガイド線自体が
# 同じ熱源温度基準で決められているため、両者は整合する。
#
# 現時点では浅煎り〜中煎りがHu氏の動画で明言されているオリジナルの範囲
# (2ハゼ以降・深煎りのアプローチは別体系で未公開。動画26:31でも「深煎りは
# 別アプローチ」と明言されている)。中深煎り・深煎りは、プリセット実測
# (174件、2026-07)を独自に解析して拡張したDフェーズ(2ハゼ以降)付きの
# 理論を適用する。詳細な分析結果・設計根拠はREADME
# 「検討中: ABCモードへのDフェーズ(2ハゼ以降)拡張」を参照。
# ============================================================

ABC_ROAST_LEVELS = ["浅煎り", "中煎り", "中深煎り", "深煎り"]
# Dフェーズ(2ハゼ以降)の調整を表示・適用する焙煎度。この2レベルのみ
# Cフェーズの意味が「1ハゼ→2ハゼ」に変わり、Dフェーズ「2ハゼ→終了」が
# 追加される。浅煎り・中煎りの挙動には一切影響しない。
ABC_LEVELS_WITH_D = ("中深煎り", "深煎り")
# Dフェーズ境界の2ハゼ温度。ユーザーが温度ガイド線設定でsecondCrackを
# 指定していればそちらを優先し、未設定時のみこの値を使う
# (プリセット解析時の閾値感度チェック223/225/227/230℃のうち227℃を採用)。
ABC_SECOND_CRACK_TEMP = 227

# 基準値: 動画内のKenya例(A3分/B3分/C80秒)。RoRは実機プリセットの標準形
# (実測173件でB内RoR変化の中央値-8.4℃/分=「減少」)をデフォルトにする。
# A/Bは焙煎度によらずほぼ一定(プリセット実測: 中煎り以降のa_sec中央値は
# 焙煎度に関係なく約203〜204秒、b_sec中央値も約155〜156秒でほぼ横ばい)
# だったため、焙煎度別には分けていない。
ABC_BASE = {"a_sec": 180, "b_sec": 180, "b_ror": -1}
# Cフェーズ基準値(焙煎度別)。浅煎り・中煎りは「1ハゼ→終了」までの時間、
# 中深煎り・深煎りは「1ハゼ→2ハゼ」までの時間(Dフェーズ追加に伴い定義が
# 変わる)。中深煎り・深煎りの値はプリセット実測のCフェーズ中央値
# (43秒/40秒)に整合。
ABC_C_BASE = {"浅煎り": 80, "中煎り": 80, "中深煎り": 45, "深煎り": 40}
# Dフェーズ基準値(2ハゼ→終了、中深煎り・深煎りのみ)。プリセット実測の
# Dフェーズ中央値(136秒/203秒)に整合。
ABC_D_BASE = {"中深煎り": 135, "深煎り": 200}
ABC_UNITS = {"a_sec": 30, "b_sec": 15, "c_sec": 5, "d_sec": 3}
ABC_LIMITS = {"a_sec": (90, 330), "b_sec": (60, 360), "c_sec": (30, 240), "d_sec": (15, 320)}

ABC_ROR_LABELS = {-2: "減少(強)", -1: "減少", 0: "フラット", 1: "増加", 2: "増加(強)"}
# 「後半RoR - 前半RoR」(℃/分)の目標値。減少(-8)が実機プリセットの標準形。
ABC_ROR_DIFF_TARGET = {-2: -14.0, -1: -8.0, 0: 0.0, 1: 8.0, 2: 14.0}

# プリセット実測(2026-07、ガイド温度175/220℃で全173件を集計)の中央値。
# infer_taste_profile()のABC特徴量の基準として使う。
_PRESET_B_ROR_DIFF_MEDIAN = -8.4
_PRESET_B_SEC_MEDIAN = 156

# 実機プリセットの投入・ターニングポイントの実測値(2026-08、ボトムを形成する151件で再検証)。
#
# 以前は投入182℃・ボトム97℃・ボトム到達59秒としていたが、いずれもプリセットには
# ほぼ存在しない値だった(97℃・59秒は該当0件、182℃は2件)。実測の最頻値へ合わせる。
#   投入温度  : 最頻185℃(50件)・中央値185℃。180℃(45件)との二極分布
#   ボトム温度: 最頻/中央値とも95℃(151件中106件=70%)。標準偏差2.35℃と極めて安定
#   ボトム到達: 最頻/中央値とも60秒(141件=93%)
#
# 焙煎度による変化は不要と確認済み。ボトム温度は全焙煎度で中央値95℃と完全に一定、
# 投入温度も浅185/中181.5/中深180/深185と非単調(相関r=-0.15)で、同一豆内の比較でも
# 方向性が無かった(深い方が高い38ペア/低い62ペア/同じ43ペア、平均-0.86℃)。
# 味との関係も、数値評価(5軸)・テキストコメントの両方で無相関だった。
_ABC_CHARGE_BASE = 185
_ABC_DIP_T, _ABC_DIP_TEMP = 60, 95

# 標高補正は撤去した(2026-08)。標高帯ごとの投入温度の中央値の開きは2℃しかなく
# (1500-2000m:185 / 1000-1500m:183 / 2000m以上:183.5 / 1000m未満:185)、しかも
# 1000m未満は実測185℃に対し旧補正が180℃と逆方向だった。Cフェーズの補正
# (_ABC_ALT_C_DELTA)は実測どおり残す。
_ABC_ALT_C_DELTA = {"1000m未満": -5}

# 焙煎度→終了温度(既存の_BASE_TEMPLATES実測中央値と同じ値)。
# 動画のCフェーズ比較に合わせ、Cの長さは時間のみを変え終了温度は固定する。
# 中深煎り・深煎りは_BASE_TEMPLATES(旧5軸生成器)の実測中央値と同じ値を採用。
ABC_END_TEMP = {"浅煎り": 226, "中煎り": 240, "中深煎り": 248, "深煎り": 252}


def compute_preset_phase_bases(level_points, guide_temps: Optional[dict]) -> dict:
    """プリセットのカーブ群を、ユーザーの温度ガイド線(カラーチェンジ・1ハゼ・2ハゼ)で
    A/B/C/Dに分割し、焙煎度ごとのフェーズ時間の中央値を返す(方針1)。

    level_points: [(roast_level, points), ...]  points = [[t, temp], ...]
    戻り値: {roast_level: {"a_sec", "b_sec", "c_sec", "d_sec"}}(算出できた項目のみ)。

    フェーズ境界温度をユーザーのガイド設定で決めることで、豆の品種・栽培地の標高で
    焙煎の進み方が変わっても、実機を見ながら決めた温度を基準に補正が効くようにする。
    C/Dの意味は analyze_abc_phases と同じ:
      - 中深煎り・深煎り(2ハゼまで焼く): C=1ハゼ→2ハゼ, D=2ハゼ→終了(2ハゼ到達個体のみ)
      - 浅煎り・中煎り: C=1ハゼ→終了(Dなし)
    """
    from statistics import median
    acc = {lv: {"a": [], "b": [], "c": [], "d": []} for lv in ABC_ROAST_LEVELS}
    for level, pts in level_points:
        if level not in acc:
            continue
        abc = analyze_abc_phases(pts, guide_temps)
        if abc is None:
            continue
        acc[level]["a"].append(abc["a_sec"])
        acc[level]["b"].append(abc["b_sec"])
        if level in ABC_LEVELS_WITH_D:
            # 2ハゼに到達した個体だけをC(1ハゼ→2ハゼ)・D(2ハゼ→終了)の母集団にする
            # (生成側は必ず2ハゼの制御点を置くため、到達個体で揃える)。
            if abc["c_sec_before_d"] is not None and abc["d_sec"] is not None:
                acc[level]["c"].append(abc["c_sec_before_d"])
                acc[level]["d"].append(abc["d_sec"])
        else:
            acc[level]["c"].append(abc["c_sec"])  # 1ハゼ→終了
    bases = {}
    for lv, d in acc.items():
        entry = {}
        if d["a"]:
            entry["a_sec"] = int(median(d["a"]))
        if d["b"]:
            entry["b_sec"] = int(median(d["b"]))
        if d["c"]:
            entry["c_sec"] = int(median(d["c"]))
        if d["d"]:
            entry["d_sec"] = int(median(d["d"]))
        bases[lv] = entry
    return bases


# ------------------------------------------------------------
# プロファイル・ヘルスチェック(フリー編集時の逸脱警告)
# ------------------------------------------------------------
# プリセット(焙煎士ラベルを正解とする)を焙煎度ごとに集計し、各指標の正常帯
# (中央値・10/90パーセンタイル)を作る。編集中のカーブがこの帯を外れたら、
# 「最終温度は高いが発達不足」等の注意喚起を返す。焙煎度分類自体は変えない。

Q10_REF_TEMP = 190.0  # Q10反応ドーズの基準温度(この温度で重み1、+10℃ごとに×2)


def _q10_dose(points, ref: float = Q10_REF_TEMP) -> float:
    """反応ドーズ ∫ 2^((T-ref)/10) dt を分単位で返す(Huの Q10 則: 10℃で反応速度2倍)。
    高温側(=1ハゼ以降のCフェーズ)を指数的に重み付けする、焙煎の発達度の代理量。"""
    import bisect
    if not points or len(points) < 2:
        return 0.0
    pts = sorted([[float(t), float(y)] for t, y in points])
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    def air(t):
        if t <= xs[0]:
            return ys[0]
        if t >= xs[-1]:
            return ys[-1]
        j = bisect.bisect_right(xs, t)
        x0, x1 = xs[j - 1], xs[j]
        a, b = ys[j - 1], ys[j]
        return a + (b - a) * (t - x0) / (x1 - x0)

    total = 0.0
    t = 0.0
    tend = xs[-1]
    while t < tend:
        total += 2.0 ** ((air(t) - ref) / 10.0)
        t += 1.0
    return total / 60.0


# 「これ以上外れて初めて警告する」最小マージン(味に効く最小単位・測定ノイズの目安)。
# 帯の端ぎりぎり・浮動小数の丸め誤差で誤警告しないための下限でもある。
HEALTH_METRIC_TOL = {
    "final": 1.0,   # ℃
    "total": 5.0,   # 秒
    "q10": 3.0,
    "dtr": 1.0,     # %
    "a_sec": 15.0,  # 秒(A調整単位30の半分)
    "b_sec": 8.0,   # 秒(B調整単位15の約半分)
    "c_sec": 3.0,   # 秒(C調整単位5の目安)
    "d_sec": 2.0,   # 秒
}

# ヘルスチェックで見る指標のキーと、表示ラベル・単位。
HEALTH_METRIC_LABELS = {
    "final": ("最終温度", "℃"),
    "total": ("総焙煎時間", "秒"),
    "q10": ("熱ドーズ(発達度)", ""),
    "dtr": ("発達率", "%"),
    "a_sec": ("A(投入→カラーチェンジ)", "秒"),
    "b_sec": ("B(→1ハゼ)", "秒"),
    "c_sec": ("C(1ハゼ→終了)", "秒"),
    "d_sec": ("D(2ハゼ→終了)", "秒"),
}


def profile_health_metrics(points, guide_temps: Optional[dict]) -> Optional[dict]:
    """1本のカーブから、ヘルスチェック用の指標を計算する。
    ガイド温度でA/B/C/Dに分割できない場合は None。"""
    abc = analyze_abc_phases([tuple(p) for p in points], guide_temps)
    if abc is None:
        return None
    total = abc["a_sec"] + abc["b_sec"] + abc["c_sec"]
    if total <= 0:
        return None
    pts = sorted([list(p) for p in points])
    c_before_d = abc["c_sec_before_d"] if abc["c_sec_before_d"] is not None else abc["c_sec"]
    return {
        "final": float(pts[-1][1]),
        "total": float(total),
        "q10": _q10_dose(points),
        "dtr": 100.0 * abc["c_sec"] / total,   # 発達率 = (1ハゼ→終了)/総時間
        "a_sec": float(abc["a_sec"]),
        "b_sec": float(abc["b_sec"]),
        "c_sec": float(c_before_d),
        "d_sec": float(abc["d_sec"] or 0),
    }


def _percentile(sorted_vals, q: float):
    n = len(sorted_vals)
    if n == 0:
        return None
    return sorted_vals[min(n - 1, max(0, int(round(q * (n - 1)))))]


def a_phase_style(points) -> str:
    """Aフェーズの形状を判定する。投入(予熱状態)から一旦温度が下がって底を打つ
    「ディップあり(dip)」か、下がらずそのまま上昇する「ディップなし(nodip)」か。
    最低温が投入から15℃以上低く、かつ15秒以上経ってから訪れる場合を dip とする。
    プリセットの大多数(約9割)は dip 型で、nodip 型は投入温度が低く序盤を長く
    かけて昇温する別スタイル。フェーズ時間(特にA・B)の性質が dip 型と大きく
    異なるため、ヘルスチェックの基準帯を分けて比較する。"""
    pts = sorted([[float(t), float(y)] for t, y in points])
    if len(pts) < 2:
        return "dip"
    charge = pts[0][1]
    mi = min(range(len(pts)), key=lambda i: pts[i][1])
    return "dip" if (charge - pts[mi][1] >= 15 and pts[mi][0] >= 15) else "nodip"


# 正常帯は「プリセットが実際に取っている値の全域(最小〜最大の包絡)」とする。
# プリセットが基準なのだから、どのプリセットも自分の帯から外れてはいけない
# (=1本でも外れるのはおかしい、という考え方)。編集ミスはこの包絡から外側に
# 出た時だけ拾う。パーセンタイルで内側に切ると正常なプリセットが弾かれてしまう。
HEALTH_BAND_LO = 0.0   # = 母集団の最小値
HEALTH_BAND_HI = 1.0   # = 母集団の最大値
HEALTH_BAND_MIN_N = 5  # これ未満のサンプルしかない帯は信頼できないので作らない
HEALTH_GLOBAL_KEY = "*"  # 焙煎度をまたいだ全プリセットの包絡(狙いの焙煎度が不明なとき用)


def compute_preset_health_bands(level_points, guide_temps: Optional[dict]) -> dict:
    """プリセット群から、(焙煎度 × Aフェーズ形状)ごとの各指標の正常帯(包絡)を返す。
    戻り値: {roast_level: {"dip": {metric: {...}}, "nodip": {...}}, "*": {"dip":..,"nodip":..}}。
    各帯は {"median","lo"(最小),"hi"(最大),"n"}。"*" は焙煎度ラベルの無いプリセットも
    含めた全体の包絡で、狙いの焙煎度が不明なカーブの判定に使う。
    サンプル過少な帯(中深のD相・nodip型の一部焙煎度など)は作らず過剰警告を避ける。
    dip型とnodip型を分けるのは、予熱から下降しないnodip型はA・Bフェーズ時間の性質が
    dip型と大きく異なり、同じ帯で比べると正常でも警告が出てしまうため。"""
    from statistics import median
    from collections import defaultdict
    acc = defaultdict(lambda: defaultdict(list))  # (level_or_"*", style) -> metric -> [values]
    for level, pts in level_points:
        m = profile_health_metrics(pts, guide_temps)
        if m is None:
            continue
        style = a_phase_style(pts)
        for k, v in m.items():
            if k == "d_sec" and not v:
                continue  # 2ハゼ未到達個体はD相の母集団に入れない
            acc[(HEALTH_GLOBAL_KEY, style)][k].append(v)   # 全体包絡(未ラベルも含む)
            if level in ABC_ROAST_LEVELS:
                acc[(level, style)][k].append(v)
    bands = {lv: {} for lv in ABC_ROAST_LEVELS}
    for (lv, style), metrics in acc.items():
        entry = {}
        for k, vals in metrics.items():
            sv = sorted(vals)
            if len(sv) < HEALTH_BAND_MIN_N:
                continue
            entry[k] = {
                "median": round(median(sv), 1),
                "lo": round(_percentile(sv, HEALTH_BAND_LO), 1),
                "hi": round(_percentile(sv, HEALTH_BAND_HI), 1),
                "n": len(sv),
            }
        bands.setdefault(lv, {})[style] = entry
    return bands


def _health_diagnoses(flags: dict) -> list:
    """個別指標の逸脱フラグ(key -> "low"/"high")から、焙煎用語での総合診断を組む。"""
    d = []
    if flags.get("c_sec") == "low" or flags.get("dtr") == "low":
        d.append("終盤の発達が不足しています(1ハゼ以降が短い・追い込み過ぎ)。"
                 "最終温度が高くても芯まで火が入らず、とがった酸・青臭さが出やすい傾向です。")
    # 2026-07: final(最終温度)・q10(熱ドーズ)が単独で「低い」場合の診断文が
    # 無く、個別指標のチップ(数値)だけでは見逃しやすかったため追加。
    # (IKAWA等、温度センサの基準が異なる機種のプロファイルは全体に低温になりがちで、
    # このケースに該当することが多い)
    if flags.get("final") == "low":
        d.append("最終温度が低すぎます。生焼け・青臭さ・水っぽさが出やすい傾向です。")
    if flags.get("total") == "high" and flags.get("q10") == "low":
        d.append("長時間・低火力(ベイクド)気味です。風味がぼやけ、甘みが痩せやすい傾向です。")
    elif flags.get("q10") == "low":
        d.append("熱量(発達度)が不足しています。芯まで熱が入りにくく、生っぽさが出やすい傾向です。")
    if flags.get("q10") == "high" or flags.get("final") == "high":
        d.append("熱量・温度が過剰です。焦げ・煙っぽさ(過焙煎)が出やすい傾向です。")
    if flags.get("b_sec") == "high" and flags.get("dtr") == "low":
        d.append("1ハゼまでが間延びしています。フレーバーの複雑さ・明るさが出にくい傾向です。")
    return d


def _select_health_band(m, bands, roast_level, style):
    """評価に使う (焙煎度, スタイル) の帯を選ぶ。戻り値 (level, band)。
    - 狙いの焙煎度が分かっている場合はその (焙煎度, スタイル) の帯で判定する
      (宣言した焙煎度に対してカーブが逸脱していないか、という意図どおりの判定)。
    - 焙煎度が不明な場合は、焙煎度をまたいだ全体包絡("*")で判定する
      (「どの焙煎度のプリセットにも無いほど極端か」だけを見る=最も保守的)。
    - nodip型で、その焙煎度の nodip帯も全体包絡も無い場合は判定対象外(=正常)。"""
    if roast_level and bands.get(roast_level, {}).get(style):
        return roast_level, bands[roast_level][style]
    glob = bands.get(HEALTH_GLOBAL_KEY, {}).get(style, {})
    return (roast_level or ""), glob


def evaluate_profile_health(points, guide_temps: Optional[dict], bands: dict,
                            roast_level: Optional[str] = None) -> dict:
    """編集中のカーブを、(焙煎度 × Aフェーズ形状)の正常帯と照らして評価する。
    予熱から下降しない nodip 型は、その焙煎度の nodip 帯があるときだけ判定し、
    無ければ判定対象外(=正常)として扱う(dip型の帯で誤警告しないため)。"""
    m = profile_health_metrics(points, guide_temps)
    style = a_phase_style(points)
    if m is None:
        # 2026-07: カーブがカラーチェンジ・1ハゼの温度ガイド線に到達せず、A/B/C分割
        # 自体ができないケース。以前は「基準データ不足」と同じ judged:false・正常扱いに
        # していたが、これは全く別の意味を持つ(=温度センサの基準がこの機種と異なる
        # プロファイル〈IKAWA等〉を取り込んだ場合や、カーブが極端に低温・短時間の
        # 場合に典型的に起きる)。「比較できないので正常として扱う」では、本来
        # 気づくべき「温度・熱量が低すぎる」兆候を見逃してしまう。ガイド温度自体は
        # 設定済み(呼び出し元のAPIエンドポイントで未設定時は先に弾いている)なので、
        # ここに来た場合は「カーブがガイド線に届いていない」ことを明示的な注意として返す。
        gt = guide_temps or {}
        cc, fc = gt.get("colorChange"), gt.get("firstCrack")
        if cc is not None and fc is not None:
            return {
                "ok": False, "judged": False, "reason": "below_guides",
                "level": roast_level or "", "a_phase_style": style,
                "metrics": {}, "warnings": [],
                "diagnoses": [
                    f"焙煎終了までにカラーチェンジ({cc}℃)・1ハゼ({fc}℃)の温度ガイド線に"
                    "到達していません。この焙煎機の温度ガイド設定に対して、終了温度・熱量が"
                    "低すぎる可能性があります(温度センサの基準が異なる機種のプロファイルを"
                    "取り込んだ場合によく起こります)。"
                ],
            }
        return {"ok": True, "judged": False, "level": roast_level or "",
                "a_phase_style": style, "metrics": {}, "warnings": [], "diagnoses": []}
    if not bands:
        return {"ok": True, "judged": False, "level": roast_level or "",
                "a_phase_style": style, "metrics": {}, "warnings": [], "diagnoses": []}
    metrics_out = {k: round(v, 1) for k, v in m.items()}
    level_used, band = _select_health_band(m, bands, roast_level, style)
    if not band:
        # 比較できる基準帯が無い(nodip型で基準データ少・分割不能等)→ 判定対象外=正常。
        return {"ok": True, "judged": False, "level": level_used,
                "a_phase_style": style, "metrics": metrics_out, "warnings": [], "diagnoses": []}
    warnings = []
    flags = {}
    for k, b in band.items():
        if k not in m:
            continue
        # 2ハゼ未到達(d_sec=0)は「D相が短い」と責めない。最終温度・C相の警告で足りる。
        if k == "d_sec" and m[k] == 0:
            continue
        v, lo, hi = m[k], b["lo"], b["hi"]
        beyond = (lo - v) if v < lo else (v - hi) if v > hi else 0.0
        # 帯内、または「味に効く最小単位」未満のわずかな逸脱は警告しない。
        if beyond <= HEALTH_METRIC_TOL.get(k, 0.0):
            continue
        span = max(1.0, hi - lo)
        direction = "low" if v < lo else "high"
        flags[k] = direction
        label, unit = HEALTH_METRIC_LABELS.get(k, (k, ""))
        warnings.append({
            "key": k, "label": label, "unit": unit,
            "value": round(v, 1), "low": lo, "high": hi,
            "direction": direction,
            "severity": "warn" if beyond > span else "caution",
        })
    # 重大(warn)を先に、次にcaution。指標の並びは崩さず安定させる。
    warnings.sort(key=lambda w: (w["severity"] != "warn",))
    return {
        "ok": len(warnings) == 0,
        "judged": True,
        "level": level_used,
        "a_phase_style": style,
        "metrics": metrics_out,
        "warnings": warnings,
        "diagnoses": _health_diagnoses(flags),
    }


def _effective_abc_bases(roast_level: str, phase_bases: Optional[dict]) -> dict:
    """焙煎度ごとの A/B/C/D 基準値を、プリセット実測(phase_bases)を優先し、
    無い項目はハードコードの既定値にフォールバックして返す。
    phase_bases=None(=DB未接続・テスト等)では従来どおり全て既定値になる。"""
    pb = (phase_bases or {}).get(roast_level, {})
    return {
        "a_sec": pb.get("a_sec", ABC_BASE["a_sec"]),
        "b_sec": pb.get("b_sec", ABC_BASE["b_sec"]),
        "c_sec": pb.get("c_sec", ABC_C_BASE[roast_level]),
        "d_sec": pb.get("d_sec", ABC_D_BASE.get(roast_level, 0)),
    }


def _quantize(value: float, base: int, unit: int, lo: int, hi: int) -> int:
    """基準値からの差分をunit刻みに丸め、[lo, hi]にクランプする。"""
    q = base + round((value - base) / unit) * unit
    return int(_clamp(q, lo, hi))


def analyze_abc_phases(roast_points: list, guide_temps: Optional[dict]) -> Optional[dict]:
    """カーブ(制御点列)と温度ガイド線から、A/B/Cフェーズの時間とB内RoRの傾向を返す。

    戻り値: {"a_sec", "b_sec", "c_sec", "b_ror_diff"(後半-前半, ℃/分),
             "b_ror_level"(-2〜+2), "b_ror_label", "c_sec_before_d", "d_sec"}
    または、境界を算出できない場合(ガイド未設定・カーブの温度帯が合わない等)は
    None。"c_sec"は常に「1ハゼ→カーブ終了」(infer_taste_profile()/
    infer_taste_axes()が参照しているため意味は変更しない)。
    "c_sec_before_d"(1ハゼ→2ハゼ)・"d_sec"(2ハゼ→終了)は、カーブが1ハゼ後に
    2ハゼ温度(ガイド線のsecondCrack、未設定時はABC_SECOND_CRACK_TEMP)を
    上向きに通過する場合のみ算出し、それ以外はどちらもNoneになる。
    """
    gt = guide_temps or {}
    cc, fc = gt.get("colorChange"), gt.get("firstCrack")
    if cc is None or fc is None or fc <= cc:
        return None
    pts = sorted((list(p) for p in roast_points), key=lambda p: p[0])
    if len(pts) < 2:
        return None
    t_cc = _rising_crossing(pts, cc)
    t_fc = _rising_crossing(pts, fc)
    if t_cc is None or t_fc is None or t_fc - t_cc < 30:
        return None
    end_t = pts[-1][0]
    mid_t = (t_cc + t_fc) / 2
    mid_temp = _interp_at(pts, mid_t)
    half_min = (t_fc - t_cc) / 2 / 60.0
    if half_min <= 0:
        return None
    ror1 = (mid_temp - cc) / half_min / 60.0 * 60  # 前半RoR(℃/分)
    ror2 = (fc - mid_temp) / half_min / 60.0 * 60  # 後半RoR(℃/分)
    diff = ror2 - ror1
    level = min(ABC_ROR_DIFF_TARGET, key=lambda k: abs(ABC_ROR_DIFF_TARGET[k] - diff))

    c_sec_before_d = None
    d_sec = None
    sc = gt.get("secondCrack") or ABC_SECOND_CRACK_TEMP
    if sc > fc:
        t_sc = _rising_crossing(pts, sc)
        if t_sc is not None and t_fc < t_sc < end_t:
            c_sec_before_d = round(t_sc - t_fc)
            d_sec = round(end_t - t_sc)

    return {
        "a_sec": round(t_cc),
        "b_sec": round(t_fc - t_cc),
        "c_sec": round(end_t - t_fc),
        "b_ror_diff": round(diff, 1),
        "b_ror_level": level,
        "b_ror_label": ABC_ROR_LABELS[level],
        "c_sec_before_d": c_sec_before_d,
        "d_sec": d_sec,
    }


# 焙煎度ごとの苦味の基準値(浅煎り→深煎りで単調に増える)。
# 2026-07訂正: 以前は「浅煎りだけ基準-1・それ以外(中煎り/中深煎り/深煎り)は
# 全て同じ基準3」という二値の扱いだったため、中煎り〜深煎りの間には基準の差が
# 一切無く、フェーズ時間の実測ノイズ(特に後述のDフェーズ)だけで苦味の大小関係が
# 逆転し、浅煎りの方が深煎りより苦味が強く表示される不具合があった。焙煎が深いほど
# 苦味が増すという焙煎理論に沿って、浅煎りの基準(2.0、従来の3-1と同じ)を起点に
# 深煎り(4.0)まで単調に増える基準値にする(infer_taste_profile()のlevel_bias*3.0と
# 同じ考え方のスケール)。
ABC_BITTERNESS_BASE = {"浅煎り": 2.0, "中煎り": 2.67, "中深煎り": 3.33, "深煎り": 4.0}

# 焙煎度ごとの酸の基準値(苦味と対称に、浅煎り→深煎りで単調に減る)。
# 2026-07訂正: 苦味と同じ理由で、酸も従来「浅煎りだけ基準+1・それ以外は全て
# 同じ基準3」という二値の扱いだったため、中煎り〜深煎りの基準に差が無く、
# 実プリセット全体の平均を取ると深煎り(3.27)が中深煎り(2.81)より高くなる
# (焙煎が深いのに酸の基準だけ見ると逆転する)という不安定な結果になっていた。
# 浅煎りの基準(4.0、従来の3+1と同じ)を起点に、焙煎が深くなるほど酸の基準は
# 下がるようにする。ただし基準はあくまで「その焙煎度で典型的な値」であり、
# db/dc/dr/dd(Bフェーズの長さ・RoRの形・Dフェーズの長さ等)による補正は
# そのまま残るため、深煎りでも実際のフェーズ時間・RoRの形次第では基準より
# 酸が強めに出ることは引き続きあり得る(浅煎り寄りの投入・急冷却気味のBフェーズ等)。
ABC_ACIDITY_BASE = {"浅煎り": 4.0, "中煎り": 3.33, "中深煎り": 2.67, "深煎り": 2.0}

# 焙煎度ごとのコクの基準値(苦味と同じ理由・同じ考え方で、浅煎り→深煎りで単調に増える)。
# 2026-07訂正: コクも酸味・苦味と同じ「浅煎りだけ基準-0.5・それ以外は全て同じ基準3」
# という二値の扱いが残っており、_abc_expected_taste()のdocstring(基準=全て3、
# 酸・苦味のみ焙煎度で異なる)と矛盾していた。Aフェーズ(投入→カラーチェンジ)の
# 熱量が多いほどコクが強まるというHu理論、および「深煎りほどコクが増す」という
# 一般的な焙煎理論に沿って、苦味と同じ単調増加の基準値にする。
ABC_BODY_BASE = {"浅煎り": 2.0, "中煎り": 2.67, "中深煎り": 3.33, "深煎り": 4.0}

# 焙煎度ごとの甘みの基準値。酸味・苦味・コクと違い、甘み(メイラード反応由来の
# カラメル感)は焙煎度に対して単調ではなく、中煎り〜中深煎りでピークを迎え、
# 深煎りではカラメルが焦げ・苦味に転じて穏やかになる(浅煎りは糖の発達が
# 浅くまだ控えめ)、という山型の関係が一般的な焙煎理論。
# 2026-07訂正: こちらも「浅煎りだけ基準-0.5・それ以外は全て同じ基準3」という
# 二値の扱いが残っていたため、中煎り〜深煎りの間で基準の差が一切無かった。
ABC_SWEETNESS_BASE = {"浅煎り": 2.5, "中煎り": 3.5, "中深煎り": 3.7, "深煎り": 3.0}

# アフターノート(余韻)の目安となる発達率(DTR=Development Time Ratio、
# 1ハゼ→終了の時間 ÷ 総焙煎時間 ×100%)。低すぎる(発達不足=浅漬け・青臭さで
# 余韻が短い)・高すぎる(過発達=フラット・炭っぽさで複雑さが失われ余韻が
# ぼやける)のどちらも余韻の評価を下げる、山型の関係にする(2026-07新設。
# 以前は aftertaste = 3 + da*0.2 + dc*0.1 という、焙煎度に依らずAフェーズ・
# Cフェーズが長いほど単調に余韻が強くなる式だった)。
#
# 「理想のDTR」は固定値ではなく、その焙煎度のbases(a_sec/b_sec/c_sec/d_sec)
# から都度算出する(_abc_expected_taste内)。実プリセットの実測では、深煎りほど
# 1ハゼ後2ハゼまで焼き込む分だけ絶対値としてのDTRが自然に高くなる
# (浅煎り中央値約13%・深煎り中央値約40%、2026-07実測)ため、一般に言われる
# 「18〜23%が適正」という固定の目安をそのまま全焙煎度に当てはめると、
# 深煎りは焙煎度なりの正しい発達をしていても機械的に「過発達」と判定され、
# 余韻が不当に低く出てしまう。basesはそもそも「その焙煎度で典型的なフェーズ
# 時間」を表す値なので、bases通りのフェーズ時間(=他の軸の基準もそのまま
# 適用される状態)のときのDTRをこの焙煎度の「理想」とみなすことで、
# 焙煎度ごとに自然な理想DTRになる。ABC_IDEAL_DTR_FALLBACKは、basesの合計が
# 0になる異常系のみで使う保険値。
ABC_IDEAL_DTR_FALLBACK = 20.0
ABC_DTR_TOLERANCE = 4.0  # 理想からこの範囲内(±4%pt)は満点(5)
ABC_DTR_SPAN = 6.0       # 範囲を外れて何%pt離れるごとに1点ずつ下げるかの目安

# フェーズ時間の基準からのズレ(単位数)がこれを超えて味に影響しないよう抑える。
# 2026-07: Dフェーズ(2ハゼ以降)は基準値(135〜200秒)に対して調整単位が3秒と
# 細かいため、実測カーブを取り込んだ際に基準から大きく外れると(例: 実測52秒 vs
# 基準200秒 → 差148秒 ÷ 3秒 = 約49単位)、A/B/Cフェーズ(基準からの差が数〜十数
# 単位程度に収まる)とは桁違いに大きい「ズレ」になり、他のどの要素よりも味の計算を
# 支配してしまう(苦味が下限/上限に張り付く等)不具合があった。各フェーズの
# 「ズレ」をこの範囲に抑えることで、ステッパーでの少しずつの調整では従来と
# 変わらず反応しつつ、基準から大きく外れた実測カーブを取り込んだ場合でも
# 味の推測が暴走しないようにする。
ABC_TASTE_DELTA_CLAMP = 10.0


def _abc_expected_taste(
    roast_level: str, a_sec: int, b_sec: int, c_sec: int, b_ror: int, d_sec: int = 0,
    bases: Optional[dict] = None,
) -> dict:
    """ABCパラメータから、想定される味(5段階)を直接マッピングで算出する。

    infer_taste_profile()(カーブ形状からの逆推測)とは独立した、Hu理論の
    対応表に基づく順方向の計算。酸味・苦味・コクは焙煎度ごとに基準が異なり
    (ABC_ACIDITY_BASE/ABC_BITTERNESS_BASE/ABC_BODY_BASE、酸味は単調減少・
    苦味とコクは単調増加)、甘みは中煎り〜中深煎りをピークとする山型
    (ABC_SWEETNESS_BASE)。いずれも基準はフェーズ時間がbasesの値・RoRが
    「減少」のときの値で、そこからの各フェーズの基準比ズレ(da/db/dc/dd)で
    補正する。アフターノートのみ焙煎度ごとの基準を持たず、発達率
    (DTR=1ハゼ→終了の時間の割合)が、その焙煎度のbasesから決まる理想DTRに
    どれだけ近いかで決まる(ABC_DTR_TOLERANCE/ABC_DTR_SPAN参照)。
    basesは焙煎度ごとのフェーズ時間の基準値({a_sec,b_sec,c_sec,d_sec})で、
    省略時はハードコード既定値(ABC_BASE/ABC_C_BASE/ABC_D_BASE)を使う。
    中深煎り・深煎り(ABC_LEVELS_WITH_D)のみ、d_secの基準からのズレを味の補正に
    加える(プリセットのテイスティングメモ語彙頻度分析: Dフェーズが基準より
    長いほど苦味・ボディに、短いほど酸・果実感に寄る傾向)。
    """
    if bases is None:
        bases = _effective_abc_bases(roast_level, None)
    clamp_d = ABC_TASTE_DELTA_CLAMP
    da = _clamp((a_sec - bases["a_sec"]) / ABC_UNITS["a_sec"], -clamp_d, clamp_d)   # 30秒単位
    db = _clamp((b_sec - bases["b_sec"]) / ABC_UNITS["b_sec"], -clamp_d, clamp_d)   # 15秒単位
    dc = _clamp((c_sec - bases["c_sec"]) / ABC_UNITS["c_sec"], -clamp_d, clamp_d)   # 5秒単位
    dr = b_ror - ABC_BASE["b_ror"]                          # 「減少」基準

    acidity = ABC_ACIDITY_BASE.get(roast_level, 3.0) - db * 0.25 - dc * 0.12 + dr * 0.4
    sweetness = ABC_SWEETNESS_BASE.get(roast_level, 3.0) + db * 0.2 + dc * 0.15
    bitterness = ABC_BITTERNESS_BASE.get(roast_level, 3.0) + dc * 0.1
    body = ABC_BODY_BASE.get(roast_level, 3.0) + da * 0.5 + db * 0.1

    total_sec = a_sec + b_sec + c_sec + d_sec
    base_total = bases["a_sec"] + bases["b_sec"] + bases["c_sec"] + bases["d_sec"]
    ideal_dtr = 100.0 * (bases["c_sec"] + bases["d_sec"]) / base_total if base_total > 0 else ABC_IDEAL_DTR_FALLBACK
    dtr = 100.0 * (c_sec + d_sec) / total_sec if total_sec > 0 else ideal_dtr
    dtr_gap = max(0.0, abs(dtr - ideal_dtr) - ABC_DTR_TOLERANCE)
    aftertaste = 5.0 - dtr_gap / ABC_DTR_SPAN

    if roast_level in ABC_LEVELS_WITH_D:
        dd = _clamp((d_sec - bases["d_sec"]) / ABC_UNITS["d_sec"], -clamp_d, clamp_d)  # 3秒単位
        acidity -= dd * 0.08
        bitterness += dd * 0.12
        body += dd * 0.1

    return {
        "acidity": _clamp5(acidity), "sweetness": _clamp5(sweetness),
        "bitterness": _clamp5(bitterness), "body": _clamp5(body),
        "aftertaste": _clamp5(aftertaste),
    }


def _abc_explanations(
    roast_level: str, a_sec: int, b_sec: int, c_sec: int, b_ror: int,
    altitude_bucket: str, guide_temps: dict, end_temp: int, d_sec: int = 0,
    bases: Optional[dict] = None,
) -> list:
    """基準値からの差分に応じて、期待される味の変化の説明文を組み立てる。

    A/B/Cフェーズの文言はJake Hu氏のセミナー(2026-03、動画)でのコメントに
    基づく。D(2ハゼ以降、中深煎り・深煎りのみ)は動画に無い独自拡張のため、
    プリセット実測とテイスティングメモの語彙頻度分析に基づく。
    basesは焙煎度ごとのフェーズ時間基準値(省略時はハードコード既定値)。
    """
    if bases is None:
        bases = _effective_abc_bases(roast_level, None)
    notes = []
    da_units = round((a_sec - bases["a_sec"]) / ABC_UNITS["a_sec"])
    db_units = round((b_sec - bases["b_sec"]) / ABC_UNITS["b_sec"])
    dc_units = round((c_sec - bases["c_sec"]) / ABC_UNITS["c_sec"])

    if da_units > 0:
        notes.append(f"Aフェーズ +{da_units * 30}秒: 豆にじっくり熱が入り、ざらつきが減ってボディが強くなる方向です。")
    elif da_units < 0:
        notes.append(f"Aフェーズ {da_units * 30}秒: 序盤を手早く通過します。短すぎるとざらつきが残り、ボディが軽くなる可能性があります。")

    if db_units > 0:
        notes.append(f"Bフェーズ +{db_units * 15}秒: フルーティで柔らかい酸に。クロロゲン酸の分解が進み、ザラザラ感も減る方向です。")
    elif db_units < 0:
        notes.append(f"Bフェーズ {db_units * 15}秒: 酸が明るく尖った印象になります。短すぎると未熟な甘味になり、ザラザラ感が増す可能性があります。")

    ror_label = ABC_ROR_LABELS[b_ror]
    if b_ror <= -1:
        notes.append(f"BフェーズRoR「{ror_label}」: Bの前半に熱を入れる配分で、酸はやわらかくなる方向です。" +
                     ("(この焙煎機のプリセットの標準的な形です)" if b_ror == -1 else ""))
    elif b_ror == 0:
        notes.append("BフェーズRoR「フラット」: 一定ペースでBを通過します。酸のやわらかさは減少形と増加形の中間です。")
    else:
        notes.append(f"BフェーズRoR「{ror_label}」: Bの後半に駆け上がる配分で、酸が強く出る方向です。")

    if dc_units > 0:
        notes.append(f"Cフェーズ +{dc_units * 5}秒: キャラメル感が増し、酸が少なくなる方向です。")
    elif dc_units < 0:
        notes.append(f"Cフェーズ {dc_units * 5}秒: キャラメル感が減り、酸を残す方向です。")

    if roast_level in ABC_LEVELS_WITH_D:
        dd_units = round((d_sec - bases["d_sec"]) / ABC_UNITS["d_sec"])
        if dd_units > 0:
            notes.append(
                f"Dフェーズ(2ハゼ以降) +{dd_units * 3}秒: 苦味・ボディが強くなり、"
                "酸・果実感は控えめになる方向です(プリセットのテイスティングメモ傾向)。"
            )
        elif dd_units < 0:
            notes.append(
                f"Dフェーズ(2ハゼ以降) {dd_units * 3}秒: 酸・果実感が残りやすくなる方向です。"
            )
        else:
            notes.append("Dフェーズ(2ハゼ以降)は基準値です。")

    if altitude_bucket in ("2000m以上", "1500-2000m"):
        notes.append(f"標高{altitude_bucket}: 密度の高い硬い豆のため投入温度をやや高く設定します(プリセット実測に基づく)。"
                     "熱が入りにくいと感じる場合はAを+30秒してください(Hu氏)。")
    elif altitude_bucket in ("1000-1500m", "1000m未満"):
        notes.append(f"標高{altitude_bucket}: 熱が入りやすい豆のため投入温度をやや低く設定します(プリセット実測に基づく)。")

    if roast_level not in ABC_LEVELS_WITH_D:
        sc = (guide_temps or {}).get("secondCrack") or ABC_SECOND_CRACK_TEMP
        if end_temp >= sc - 2:
            notes.append(f"⚠ 終了温度{end_temp}℃は2ハゼ目安({sc}℃)に達しています。2ハゼ以降はHu氏の別アプローチ(未対応)の領域です。")

    notes.append("※ 浅煎りでスモークの香りがした場合は、投入温度(≒Aの入り方)を下げるのがHu氏の推奨する診断法です。")
    return notes


def generate_profile_abc(
    roast_level: str,
    a_sec: Optional[int] = None,
    b_sec: Optional[int] = None,
    c_sec: Optional[int] = None,
    b_ror: int = -1,
    guide_temps: Optional[dict] = None,
    altitude_bucket: str = "",
    country: str = "",
    d_sec: Optional[int] = None,
    phase_bases: Optional[dict] = None,
    charge_temp: Optional[int] = None,
    end_temp: Optional[int] = None,
) -> dict:
    """Jake Hu氏のABC理論に基づき、フェーズ時間の直接指定でプロファイルを生成する。

    a_sec/b_sec/c_sec: 各フェーズの時間(秒)。Hu理論の調整単位(A=30秒/B=15秒/
    C=5秒)に量子化される。b_ror: BフェーズのRoR形状(-2=減少(強)〜+2=増加(強)、
    デフォルト-1=減少)。guide_temps: 温度ガイド線設定(カラーチェンジ・1ハゼ必須)。
    roast_levelは浅煎り/中煎り/中深煎り/深煎り。中深煎り・深煎り
    (ABC_LEVELS_WITH_D)のみ d_sec(Dフェーズ=2ハゼ以降の時間、3秒単位)が
    有効になり、Cフェーズの意味も「1ハゼ→2ハゼ」に変わる(それ以外の
    焙煎度ではd_secは無視され、Cは従来通り「1ハゼ→終了」)。a_sec/b_sec/c_sec/
    d_secを省略(None)した場合は、焙煎度ごとの基準値になる。
    phase_bases: プリセットをユーザーのガイド温度で分割して求めたフェーズ時間の
    基準値(compute_preset_phase_bases()の戻り値)。渡すと、各フェーズの既定値・
    量子化の中心・味の基準がこれに切り替わる(方針1)。渡さない場合は従来の
    ハードコード既定値(ABC_BASE/ABC_C_BASE/ABC_D_BASE)を使う。
    charge_temp/end_temp: 投入温度・ドロップ(焙煎終了)温度を明示指定する(2026-07)。
    既存プロファイルを取り込んでABCモードで調整する場合、呼び出し元がそのカーブの
    実際の投入温度・終了温度をここに渡すことで、A〜Dのどのフェーズ時間を変えても
    この2点の温度は変わらない(省略時は従来通り、標高補正込みの既定温度
    _ABC_CHARGE_BASE・焙煎度ごとの既定終了温度ABC_END_TEMPを使う)。
    """
    gt = guide_temps or {}
    cc, fc = gt.get("colorChange"), gt.get("firstCrack")
    if cc is None or fc is None:
        raise ValueError("温度ガイド線(カラーチェンジ・1ハゼ)が設定されていません。先に「⚙ 温度ガイド線設定」で入力してください。")
    if roast_level not in ABC_ROAST_LEVELS:
        raise ValueError(f"ABCモードは{'/'.join(ABC_ROAST_LEVELS)}のみ対応しています")
    # フェーズ時間の基準値(プリセット実測優先・無ければ既定値)。標高補正(c_sec)は
    # ここで基準値に一度だけ織り込む(2026-07: 以前は量子化後に毎回c_secへ加算して
    # いたため、abcParams.c_secに前回の呼び出しで既に補正済みの値が入っている状態で
    # 再度generate_profile_abc()を呼ぶ〈標高選択中に他の項目を変えるだけで起きる〉
    # たびに標高補正が積み重なってc_secが際限なくずれていく不具合があった。
    # 基準値そのものに織り込むことで、以降の量子化・基準値との差分計算〈短い/長いの
    # 判定・味推測・説明文〉もすべてこの標高込みの基準を中心にでき、標高由来の
    # 自動シフト分をユーザーの意図的な調整と誤って数えなくなる)。
    bases = _effective_abc_bases(roast_level, phase_bases)
    bases = {**bases, "c_sec": bases["c_sec"] + _ABC_ALT_C_DELTA.get(altitude_bucket, 0)}
    if a_sec is None:
        a_sec = bases["a_sec"]
    if b_sec is None:
        b_sec = bases["b_sec"]
    if c_sec is None:
        c_sec = bases["c_sec"]
    if d_sec is None:
        d_sec = bases["d_sec"]
    if fc <= cc + 10:
        raise ValueError("1ハゼ温度はカラーチェンジ温度より10℃以上高い必要があります")
    if cc <= _ABC_DIP_TEMP + 10:
        raise ValueError(f"カラーチェンジ温度({cc}℃)が低すぎます(ターニングポイント{_ABC_DIP_TEMP}℃より十分高い必要があります)")

    with_d = roast_level in ABC_LEVELS_WITH_D
    sc = None
    if with_d:
        sc = gt.get("secondCrack") or ABC_SECOND_CRACK_TEMP
        if sc <= fc + 5:
            raise ValueError(f"2ハゼ温度({sc}℃)は1ハゼ温度({fc}℃)より十分高い必要があります")

    b_ror = int(_clamp(b_ror, -2, 2))
    a_sec = _quantize(a_sec, bases["a_sec"], ABC_UNITS["a_sec"], *ABC_LIMITS["a_sec"])
    b_sec = _quantize(b_sec, bases["b_sec"], ABC_UNITS["b_sec"], *ABC_LIMITS["b_sec"])
    c_sec = _quantize(c_sec, bases["c_sec"], ABC_UNITS["c_sec"], *ABC_LIMITS["c_sec"])
    d_sec = _quantize(d_sec, bases["d_sec"], ABC_UNITS["d_sec"], *ABC_LIMITS["d_sec"]) if with_d else 0

    # 投入温度の標高補正(プリセット実測ベース)。charge_temp指定時は、投入温度は
    # 取り込み元プロファイルの実測値を優先し、標高補正は掛けない(元の投入温度を
    # 変えないため)。c_secの標高補正は上のbasesに織り込み済み。
    charge = int(charge_temp) if charge_temp is not None else _ABC_CHARGE_BASE

    # Aはターニングポイントより十分後(量子化済みの下限90秒 > dip 59秒で通常は満たす)
    # Aはターニングポイントより十分後にする。ABC_LIMITSのA下限(90秒)がそのまま
    # 使えるよう、ボトム(60秒)+30秒とする(以前は+31でA=90秒が91秒に押し上げられていた)。
    a_sec = max(a_sec, _ABC_DIP_T + 30)

    # 全体がTHE ROAST EXPERT上限(900秒)を超える場合は、終盤(D→C→Bの順)から
    # 切り詰める。
    over = (a_sec + b_sec + c_sec + d_sec) - MAX_ROAST_SECONDS
    if over > 0 and with_d:
        cut_d = min(over, d_sec - ABC_LIMITS["d_sec"][0])
        d_sec -= cut_d
        over -= cut_d
    if over > 0:
        cut_c = min(over, c_sec - ABC_LIMITS["c_sec"][0])
        c_sec -= cut_c
        over -= cut_c
    if over > 0:
        b_sec = max(ABC_LIMITS["b_sec"][0], b_sec - over)

    # end_temp指定時は、ドロップ温度は取り込み元プロファイルの実測値を優先する
    # (焙煎度ごとの既定終了温度ABC_END_TEMPには置き換えない)。
    end_temp_target = int(end_temp) if end_temp is not None else ABC_END_TEMP[roast_level]
    floor_temp = (sc + 4) if with_d else (fc + 4)
    end_temp = int(_clamp(max(end_temp_target, floor_temp), floor_temp, MAX_TEMPERATURE))
    fc_t = a_sec + b_sec
    sc_t = fc_t + c_sec if with_d else None
    end_t = (sc_t + d_sec) if with_d else (fc_t + c_sec)

    # Bフェーズ内のRoR形状。Bを3分割し、RoRが線形に変化していく3つの区間
    # (等間隔の制御点2つ)で表現する(2分割・中点1つの旧仕様より滑らかな
    # RoRカーブになる)。
    # 注: analyze_abc_phases()とプリセット統計(-8.4℃/分)は「前半/後半の
    # 2分割」でRoR差を測る。3分割で端区間のRoR差をΔとすると、この測り方での
    # 測定値は(2/3)Δになるため、Δ=1.5×目標値とすれば測定値=目標値となり、
    # 生成→再解析の往復とプリセット統計との整合が保たれる。
    b_min = b_sec / 60.0
    d = ABC_ROR_DIFF_TARGET[b_ror]
    r_avg = (fc - cc) / b_min          # B全体の平均RoR(℃/分)
    delta = 1.5 * d                    # 3分割の端区間同士のRoR差
    b1_temp = _clamp(cc + (r_avg - delta / 2) * b_min / 3, cc + 1, fc - 3)
    b2_temp = _clamp(cc + (2 * r_avg - delta / 2) * b_min / 3, b1_temp + 1, fc - 1)

    points = [
        [0, charge],
        [_ABC_DIP_T, _ABC_DIP_TEMP],
        [a_sec, cc],
        [round(a_sec + b_sec / 3), round(b1_temp)],
        [round(a_sec + 2 * b_sec / 3), round(b2_temp)],
        [fc_t, fc],
    ]
    if with_d:
        points.append([sc_t, sc])
    points.append([end_t, end_temp])
    cleaned: list[list[float]] = []
    for x, y in sorted(points, key=lambda p: p[0]):
        if cleaned and x <= cleaned[-1][0]:
            x = cleaned[-1][0] + 1
        cleaned.append([int(x), int(round(y))])

    # 風量カーブ: フェーズ境界(カラーチェンジ=a_sec・1ハゼ=fc_t)にアンカーし、
    # プリセット実測の形(投入50→ピーク80→なだらか減少、1ハゼで勾配が緩む、
    # 深いほど発達期・終盤を高め)を再現する。
    fan_cleaned = _abc_fan_curve(roast_level, a_sec, fc_t, cleaned[-1][0])
    cooldown = [cleaned[-1][0] + 120, 60]

    expected = _abc_expected_taste(roast_level, a_sec, b_sec, c_sec, b_ror, d_sec, bases=bases)
    explanations = _abc_explanations(roast_level, a_sec, b_sec, c_sec, b_ror, altitude_bucket, gt, end_temp, d_sec, bases=bases)

    # 標高は名前に含めない(産地・焙煎度のみを土台にする)。
    name_parts = [p for p in [country, roast_level] if p]
    label = " ".join(name_parts) if name_parts else roast_level
    name = f"{label} ABC A{a_sec // 60}:{a_sec % 60:02d} B{b_sec // 60}:{b_sec % 60:02d} C{c_sec // 60}:{c_sec % 60:02d}"
    if with_d:
        name += f" D{d_sec // 60}:{d_sec % 60:02d}"
    name += "(自動生成)"
    uuid_ascii = str(int(time.time() * 1000)).zfill(16)

    return {
        "name": name,
        "uuid": uuid_ascii,
        "roast": cleaned,
        "fan": fan_cleaned,
        "cooldown": cooldown,
        "expected_taste": expected,
        "explanations": explanations,
        "params": {
            "mode": "abc",
            "roast_level": roast_level, "altitude_bucket": altitude_bucket, "process": "",
            "country": country,
            "a_sec": a_sec, "b_sec": b_sec, "c_sec": c_sec, "d_sec": d_sec, "b_ror": b_ror,
            **expected,
        },
    }


# ============================================================
# 3軸モード(質の対立軸)による生成・逆推測 (v0.11.52)
# ------------------------------------------------------------
# 旧来の5軸(酸味・甘み・苦味・コク・アフターノート、強度1〜5)は、カーブ上の
# 同じ特徴を奪い合う縮退があった(酸味⇔甘み・コクは同じ特徴の逆向き、
# 苦味・コク・アフターは終了時間で縮退)。プリセット77豆・187件の
# テイスティングメモを語彙分析した結果もプロの記述は「強度」ではなく
# 「質の対比」が主体だった(「強い酸」という表現は0回。フルーツ系甘さ117回
# vs カラメル系68回、ボディ軽28/重54、余韻73回など)。
#
# そこで、Hu理論のレバーと1:1対応する3本の両極軸(-2〜+2、0=プリセット標準)
# +焙煎度に再設計する:
#   酸の質     まろやか(-) ⇔ 明るい(+)        → BフェーズRoR形状
#   甘さの系統 フルーティ(-) ⇔ チョコ・カラメル(+) → B時間⇔C時間の配分
#   ボディ     軽やか(-) ⇔ 濃厚(+)            → Aフェーズ(投入エネルギー・TP)
#   (甘さ重視トグル: B・C両方をやや長くして甘さの総量を底上げ)
# 苦味は焙煎度に統合(プリセットメモでも苦味の記述はほぼ焙煎度の説明)。
# 余韻(アフターノート)は「甘さの系統」のC配分が担うため軸から外し、
# 説明文で変化を伝える。
#
# 旧5軸の generate_profile()/infer_taste_profile() は互換のため残す。
# ============================================================

TASTE_AXES = {
    "acid_quality": {"label": "酸の質", "low": "まろやか", "high": "明るい"},
    "sweet_direction": {"label": "甘さの系統", "low": "フルーティ", "high": "チョコ・カラメル"},
    "body": {"label": "ボディ", "low": "軽やか", "high": "濃厚"},
}

# 各軸の1段あたりの効き方(生成と逆推測で共有する係数)
# --- ガイド温度なしのフォールバック経路(旧_BASE_TEMPLATESベース)用 ---
_AXIS_BODY_CHARGE = 3    # ボディ+1 → 投入温度+3℃
_AXIS_BODY_DIP = 2       # ボディ+1 → TP温度+2℃
_AXIS_BODY_DIP_T = 5     # ボディ+1 → TP時刻+5秒(Aフェーズを長く)
_AXIS_SWEET_FC_T = 15    # 甘さの系統+1(カラメル寄り) → 1ハゼ位置-15秒(B→Cへ配分)
_SWEET_BOOST_FC_T = 15   # 甘さ重視 → B+15秒
_SWEET_BOOST_END_T = 30  # 甘さ重視 → 全体+30秒(C+15秒相当)

# --- ガイド温度ありの本流(ABCモードと同じフェーズ時間の上に3軸を載せる)用 ---
# ボディ・酸の質・甘さの系統・甘さ重視を、A/B/C/D/RoRの調整として表現し、
# generate_profile_abc()に委譲する。これにより「味を推測」と「ABCモード」は
# 同じフェーズ時間モデルの上で完全に整合する(方針1)。生成→逆推測の往復が
# 厳密に成立するよう、いずれもフェーズ量子化単位の倍数に揃えている。
_AXES_BODY_A_SEC = 30    # ボディ±1 → Aフェーズ±30秒(Aの調整単位1つ分)
_AXES_SWEET_SHIFT = 15   # 甘さの系統±1 → Bを∓15秒・Cを±15秒(B⇔C配分。総時間は不変)
_AXES_SWEET_BOOST = 15   # 甘さ重視 → BもCも+15秒(両方を伸ばす=総時間+30秒)


def _clamp_axis(v) -> int:
    try:
        return int(_clamp(round(float(v)), -2, 2))
    except (TypeError, ValueError):
        return 0


def _axes_explanations(roast_level: str, aq: int, sd: int, bd: int, sweet_boost: bool,
                       guide_temps: Optional[dict]) -> list:
    """3軸の設定から、期待される味の変化の説明文を組み立てる。
    語彙はプリセットのテイスティングメモ頻出語とHu氏のセミナーコメントに基づく。"""
    notes = []
    if bd > 0:
        notes.append("ボディ「濃厚」寄り: 序盤(Aフェーズ)にじっくり熱を入れ、濃厚でふくよかな質感に。ざらつきも減る方向です(Hu氏)。")
    elif bd < 0:
        notes.append("ボディ「軽やか」寄り: 投入を控えめにして序盤を手早く通過し、軽やかですっきりした口当たりの方向です。")
    if aq > 0:
        notes.append("酸の質「明るい」寄り: BフェーズのRoRを増加形にし、明るくシャープな酸が立つ方向です。")
    elif aq < 0:
        notes.append("酸の質「まろやか」寄り: Bフェーズの前半に熱を入れる減少形で、酸がやわらかくまろやかになる方向です。")
    if sd > 0:
        notes.append("甘さの系統「チョコ・カラメル」寄り: Cフェーズへ時間を配分し、チョコやキャラメルのような甘さと長い余韻に。酸は少なくなります。")
    elif sd < 0:
        notes.append("甘さの系統「フルーティ」寄り: Bフェーズへ時間を配分し、ベリーや柑橘のようなフルーティな甘酸っぱさを引き出す方向です。あと口は軽めになります。")
    if sweet_boost:
        notes.append("甘さ重視: B・Cの両方をやや長くし、甘さの総量を底上げします(全体の焙煎時間が少し伸びます)。")
    if guide_temps and (guide_temps.get("colorChange") is None or guide_temps.get("firstCrack") is None):
        notes.append("※ 温度ガイド線(カラーチェンジ・1ハゼ)が未設定のため、酸の質(B内RoR形状)は反映されていません。")
    return notes


def _axes_profile_name(base_name, aq, sd, bd, sweet_boost,
                       base_aq, base_sd, base_bd, base_boost,
                       country, roast_level):
    """味を推測(3軸)で自動生成したプロファイルの名前を作る。
    元プロファイル名(base_name)を土台に、元プロファイルから推測された値
    (base_aq/base_sd/base_bd/base_boost)からの「相対変化」だけを
    「酸+1 甘+1 ボディ+1」の形で付す(変化0の軸は省略)。甘さ重視を新たに
    追加したときは🍯マークで明示する。標高は名前に含めない。
    base_name が無い(=ゼロから生成)場合は、産地・焙煎度から名前を組み立てる。"""
    daq, dsd, dbd = aq - base_aq, sd - base_sd, bd - base_bd
    axis_parts = []
    if daq:
        axis_parts.append(f"酸{daq:+d}")
    if dsd:
        axis_parts.append(f"甘{dsd:+d}")
    if dbd:
        axis_parts.append(f"ボディ{dbd:+d}")
    if sweet_boost and not base_boost:
        axis_parts.append("🍯甘さ重視")
    suffix = " ".join(axis_parts)

    base = (base_name or "").strip()
    if base:
        # 変化が無ければ元名のまま(=取り込んだカーブと同じ)。
        return f"{base} {suffix}" if suffix else base
    # 元プロファイル名が無い場合のフォールバック(標高は含めない)。
    head = " ".join([p for p in [country, roast_level] if p]) or roast_level
    return f"{head} {suffix}(自動生成)" if suffix else f"{head}(自動生成)"


def _generate_axes_via_abc(roast_level, aq, sd, bd, sweet_boost,
                           altitude_bucket, process, country, guide_temps, phase_bases,
                           base_name="", base_aq=0, base_sd=0, base_bd=0, base_boost=False):
    """3軸をABCモードのフェーズ時間(A/B/C/D)+RoRの調整に変換し、
    generate_profile_abc()に委譲してカーブを作る(方針1、ガイド温度あり時の本流)。
      ボディ    → Aフェーズ±30秒
      酸の質    → BフェーズRoR形状(-2..+2をそのままb_rorに)
      甘さの系統 → Bを∓15秒・Cを±15秒(B⇔C配分、総時間は不変)
      甘さ重視   → B・C両方を+15秒(=総時間+30秒)
    カーブはABC由来のものを使い、名前・params・説明文だけ3軸用に付け替える。
    (精製方法はABCモードにレバーが無いため、名前・絞り込み用のメタデータとしてのみ保持し、
     カーブには反映しない=ABCモードと挙動を揃える)。"""
    bases = _effective_abc_bases(roast_level, phase_bases)
    a_sec = bases["a_sec"] + bd * _AXES_BODY_A_SEC
    b_sec = bases["b_sec"] - sd * _AXES_SWEET_SHIFT + (_AXES_SWEET_BOOST if sweet_boost else 0)
    c_sec = bases["c_sec"] + sd * _AXES_SWEET_SHIFT + (_AXES_SWEET_BOOST if sweet_boost else 0)
    d_sec = bases["d_sec"]

    abc = generate_profile_abc(
        roast_level, a_sec=a_sec, b_sec=b_sec, c_sec=c_sec, b_ror=aq,
        guide_temps=guide_temps, altitude_bucket=altitude_bucket, country=country,
        d_sec=d_sec, phase_bases=phase_bases,
    )
    name = _axes_profile_name(base_name, aq, sd, bd, sweet_boost,
                              base_aq, base_sd, base_bd, base_boost,
                              country, roast_level)
    return {
        "name": name,
        "uuid": abc["uuid"],
        "roast": abc["roast"],
        "fan": abc["fan"],
        "cooldown": abc["cooldown"],
        "explanations": _axes_explanations(roast_level, aq, sd, bd, sweet_boost, guide_temps),
        "params": {
            "mode": "axes",
            "roast_level": roast_level, "altitude_bucket": altitude_bucket, "process": process,
            "country": country,
            "acid_quality": aq, "sweet_direction": sd, "body": bd, "sweet_boost": bool(sweet_boost),
        },
    }


def generate_profile_axes(
    roast_level: str,
    acid_quality: int = 0,
    sweet_direction: int = 0,
    body: int = 0,
    sweet_boost: bool = False,
    altitude_bucket: str = "",
    process: str = "",
    country: str = "",
    guide_temps: Optional[dict] = None,
    phase_bases: Optional[dict] = None,
    base_name: str = "",
    base_acid_quality: int = 0,
    base_sweet_direction: int = 0,
    base_body: int = 0,
    base_sweet_boost: bool = False,
) -> dict:
    """3本の質の対立軸(-2〜+2、0=プリセット標準)+焙煎度からプロファイルを生成する。

    acid_quality: 酸の質(まろやか⇔明るい) → BフェーズRoR形状
    sweet_direction: 甘さの系統(フルーティ⇔チョコ・カラメル) → B⇔C時間配分
    body: ボディ(軽やか⇔濃厚) → Aフェーズの長さ
    sweet_boost: 甘さ重視(B・C両方をやや長く)

    温度ガイド線(カラーチェンジ・1ハゼ)が設定されている場合は、ABCモードと同じ
    フェーズ時間モデル(phase_bases=プリセットをガイド温度で分割した実測基準)の上に
    3軸を「A/B/C/D/RoRの調整」として載せ、generate_profile_abc()に委譲する(方針1)。
    これにより味を推測とABCモードが完全に整合し、甘さ重視もB・C両方を確実に伸ばす。
    ガイド未設定時のみ、従来の_BASE_TEMPLATESベースの近似生成にフォールバックする。
    """
    if roast_level not in _BASE_TEMPLATES:
        roast_level = "中煎り"
    aq, sd, bd = _clamp_axis(acid_quality), _clamp_axis(sweet_direction), _clamp_axis(body)
    base_aq = _clamp_axis(base_acid_quality)
    base_sd = _clamp_axis(base_sweet_direction)
    base_bd = _clamp_axis(base_body)
    base_boost = bool(base_sweet_boost)

    has_guides = bool(guide_temps
                      and guide_temps.get("colorChange") is not None
                      and guide_temps.get("firstCrack") is not None)
    if has_guides:
        return _generate_axes_via_abc(
            roast_level, aq, sd, bd, sweet_boost,
            altitude_bucket, process, country, guide_temps, phase_bases,
            base_name=base_name, base_aq=base_aq, base_sd=base_sd,
            base_bd=base_bd, base_boost=base_boost,
        )

    t = dict(_BASE_TEMPLATES[roast_level])

    # ---- 3軸 → Huレバーへの反映 ----
    t["charge"] += bd * _AXIS_BODY_CHARGE
    t["dip"] += bd * _AXIS_BODY_DIP
    t["dip_t"] += bd * _AXIS_BODY_DIP_T
    # 甘さの系統(B⇔C配分)・甘さ重視のB延長は、ガイド温度がある場合は
    # 「1ハゼ交点そのものをずらす」方式(_apply_guide_shapeのfc_shift)で行う。
    # 中間アンカー(fc_t)をずらすだけでは、カラーチェンジ・1ハゼの両交点が
    # 比例して動くだけでB⇔Cの配分が変わらないため(実測で確認済み)。
    # ガイド温度が無い場合のみ、近似としてfc_tアンカーを直接ずらす。
    has_guides = bool(guide_temps
                      and guide_temps.get("colorChange") is not None
                      and guide_temps.get("firstCrack") is not None)
    fc_shift = 0.0
    if has_guides:
        fc_shift = -sd * _AXIS_SWEET_FC_T + (_SWEET_BOOST_FC_T if sweet_boost else 0)
    else:
        t["fc_t"] -= sd * _AXIS_SWEET_FC_T
        if sweet_boost:
            t["fc_t"] += _SWEET_BOOST_FC_T
    if sweet_boost:
        t["end_t"] += _SWEET_BOOST_END_T

    # ---- 産地(標高)・精製方法(旧生成器と同じ実測ベースの補正) ----
    charge_delta, alt_dip_delta, alt_dip_t_delta, fc_mult, end_mult = _ALTITUDE_ADJUST.get(
        altitude_bucket, (0, 0, 0, 1.0, 1.0)
    )
    t["charge"] += charge_delta
    t["dip"] += alt_dip_delta
    t["dip_t"] += alt_dip_t_delta
    t["fc_t"] *= fc_mult
    t["end_t"] *= end_mult
    proc_charge_delta, dip_delta, proc_dip_t_delta, end_temp_delta, end_t_mult, fc_t_mult = _PROCESS_ADJUST.get(
        process, (0, 0, 0, 0, 1.0, 1.0)
    )
    t["charge"] += proc_charge_delta
    t["dip"] += dip_delta
    t["dip_t"] += proc_dip_t_delta
    t["end_temp"] += end_temp_delta
    t["end_t"] *= end_t_mult
    t["fc_t"] *= fc_t_mult

    # ---- Hu単位への量子化と、焙煎度らしい範囲へのクランプ(旧生成器と同一) ----
    base_tmpl = _BASE_TEMPLATES[roast_level]
    t["fc_t"] = base_tmpl["fc_t"] + round((t["fc_t"] - base_tmpl["fc_t"]) / 15) * 15
    t["end_t"] = base_tmpl["end_t"] + round((t["end_t"] - base_tmpl["end_t"]) / 5) * 5
    t["charge"] = _clamp(t["charge"], 145, 210)
    t["dip"] = _clamp(t["dip"], 70, 120)
    t["dip"] = min(t["dip"], t["charge"] - 50)
    t["dip_t"] = _clamp(t["dip_t"], 25, 95)
    t["end_temp"] = _clamp(t["end_temp"], _END_TEMP_FLOOR[roast_level], min(_END_TEMP_CEILING[roast_level], MAX_TEMPERATURE))
    t["end_t"] = _clamp(
        t["end_t"],
        max(t["dip_t"] + 180, _END_T_FLOOR[roast_level]),
        min(_END_T_CEILING[roast_level], MAX_ROAST_SECONDS),
    )
    t["fc_t"] = _clamp(t["fc_t"], t["dip_t"] + 60, t["end_t"] - 90)
    t["fc_temp"] = _clamp(t["fc_temp"], t["dip"] + 40, t["end_temp"] - 10)

    charge, dip, dip_t = round(t["charge"]), round(t["dip"]), round(t["dip_t"])
    fc_t, fc_temp = round(t["fc_t"]), round(t["fc_temp"])
    end_t, end_temp = round(t["end_t"]), round(t["end_temp"])

    t120 = 120 if 120 > dip_t and 120 < fc_t else round((dip_t + fc_t) / 2)
    ratio_120 = (t120 - dip_t) / max(1, (fc_t - dip_t))
    temp_120 = round(dip + (fc_temp - dip) * min(0.9, max(0.1, ratio_120 * 0.75)))

    # 1ハゼ後の展開中間点は中立(0.5)固定。3軸は苦味・アフターの強度指定を
    # 持たないため、旧生成器のdev_ratio調整(苦味0.35/酸味0.65)は行わない。
    dev_t = round(fc_t + (end_t - fc_t) * 0.5)
    dev_temp = round(fc_temp + (end_temp - fc_temp) * 0.5)

    points = [(0, charge), (dip_t, dip), (t120, temp_120), (fc_t, fc_temp)]
    if roast_level == "深煎り":
        second_crack_temp = round(min(232, end_temp - 6))
        second_crack_t = round(fc_t + (end_t - fc_t) * 0.55)
        if second_crack_t <= dev_t:
            second_crack_t = dev_t + 20
        points.append((dev_t, dev_temp))
        points.append((second_crack_t, second_crack_temp))
    else:
        points.append((dev_t, dev_temp))
    points.append((end_t, end_temp))

    points = sorted(points, key=lambda p: p[0])
    cleaned: list[list[float]] = []
    for x, y in points:
        if cleaned and x <= cleaned[-1][0]:
            x = cleaned[-1][0] + 1
        cleaned.append([x, round(y)])

    # 酸の質 → BフェーズRoR形状(ABCモードと同じ5段階の目標値を共有)
    if guide_temps:
        cleaned = _apply_guide_shape(cleaned, guide_temps, ABC_ROR_DIFF_TARGET[aq], fc_shift=fc_shift)

    # 風量: 明るい酸寄りはやや高め(クリーンに)、濃厚寄りはやや低め(熱をこもらせる)
    fan_end = _clamp(56 + aq * 3 - bd * 3, MIN_FAN, MAX_FAN)
    fan_hold_t = min(6, max(2, dip_t // 10))
    fan = [[0, MIN_FAN], [1, 80], [fan_hold_t, 78], [cleaned[-1][0], round(fan_end)]]
    fan_cleaned: list[list[float]] = []
    for x, y in fan:
        if fan_cleaned and x <= fan_cleaned[-1][0]:
            x = fan_cleaned[-1][0] + 1
        fan_cleaned.append([x, round(y)])

    cooldown = [cleaned[-1][0] + 120, 60]

    name = _axes_profile_name(base_name, aq, sd, bd, sweet_boost,
                              base_aq, base_sd, base_bd, base_boost,
                              country, roast_level)
    uuid_ascii = str(int(time.time() * 1000)).zfill(16)

    return {
        "name": name,
        "uuid": uuid_ascii,
        "roast": cleaned,
        "fan": fan_cleaned,
        "cooldown": cooldown,
        "explanations": _axes_explanations(roast_level, aq, sd, bd, sweet_boost, guide_temps),
        "params": {
            "mode": "axes",
            "roast_level": roast_level, "altitude_bucket": altitude_bucket, "process": process,
            "country": country,
            "acid_quality": aq, "sweet_direction": sd, "body": bd, "sweet_boost": bool(sweet_boost),
        },
    }


def infer_taste_axes(roast_points: list, guide_temps: Optional[dict] = None,
                     phase_bases: Optional[dict] = None) -> dict:
    """カーブから3軸(酸の質・甘さの系統・ボディ)と焙煎度を逆推測する。

    generate_profile_axes()の順方向マッピングを逆に解く。ガイド温度がある場合は
    ABCモードと同じフェーズ時間モデル(phase_bases)を基準にするため、生成→逆推測が
    厳密に往復する(方針1):
      ボディ    = (A時間 − 基準A) / 30
      酸の質    = BフェーズRoRレベル
      甘さの系統 = ((C−B) − (基準C−基準B)) / 30   ※甘さ重視の+15はC−Bで相殺され影響しない
    ガイド未設定時のみ、旧_BASE_TEMPLATESベース(投入温度から推定)にフォールバックする。
    ボディ軸は標高・精製方法の補正と重なるため、それらを使ったプロファイルでは目安になる。
    """
    pts = sorted((list(p) for p in roast_points), key=lambda p: p[0])
    if len(pts) < 2:
        raise ValueError("roast_pointsが不足しています(2点以上必要)")

    end_t, end_temp = pts[-1]
    roast_level = infer_roast_level(end_temp, end_t)

    abc = analyze_abc_phases(pts, guide_temps) if guide_temps else None
    if abc:
        # ガイドあり: ABCフェーズ時間の基準(=中立の3軸生成が使うのと同じ値)から逆算。
        bases = _effective_abc_bases(roast_level, phase_bases)
        body = _clamp_axis((abc["a_sec"] - bases["a_sec"]) / _AXES_BODY_A_SEC)
        acid_quality = abc["b_ror_level"]
        with_d = roast_level in ABC_LEVELS_WITH_D
        c_measured = abc["c_sec_before_d"] if (with_d and abc["c_sec_before_d"] is not None) else abc["c_sec"]
        neutral_diff = bases["c_sec"] - bases["b_sec"]
        sweet_direction = _clamp_axis(
            ((c_measured - abc["b_sec"]) - neutral_diff) / (2 * _AXES_SWEET_SHIFT)
        )
    else:
        # ガイドなし: 従来の投入温度・ターニングポイントベースの推定にフォールバック。
        neutral = generate_profile_axes(roast_level, guide_temps=guide_temps, phase_bases=phase_bases)
        n_pts = neutral["roast"]
        n_charge = n_pts[0][1]
        n_dip_t = min(n_pts, key=lambda p: p[1])[0]
        charge = pts[0][1]
        early = [p for p in pts if p[0] <= 150] or pts[:2]
        dip_t = min(early, key=lambda p: p[1])[0]
        body = _clamp_axis(
            ((charge - n_charge) / _AXIS_BODY_CHARGE + (dip_t - n_dip_t) / _AXIS_BODY_DIP_T) / 2
        )
        acid_quality = 0
        sweet_direction = 0

    return {
        "roast_level": roast_level,
        "acid_quality": acid_quality,
        "sweet_direction": sweet_direction,
        "body": body,
        "abc": abc,
        "note": (
            "焙煎度と、Hu理論のレバー(A=ボディ/B内RoR=酸の質/B⇔C配分=甘さの系統)に"
            "1:1対応させた3軸での目安です。酸の質・甘さの系統は温度ガイド線が設定されて"
            "いる場合のみ判定できます。ボディは標高・精製方法の補正と区別できないため、"
            "それらを使ったプロファイルでは実際よりずれることがあります。"
        ),
    }
