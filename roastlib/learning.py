"""焙煎ログから豆温度モデルの補正を学ぶ。

■ 2層に分ける
  芯(固定)   U0 / K_PYRO / K_DRY / CRACK_SPREAD / SC_DRY_FRAC
             校正用プロファイル5本の実測で決めた値。ここは動かさない。
             焙煎1本抜きの交差検証で、芯を動かすとどの自由度でも予測が悪化した
             (予測の残差合計 114.9 → 119〜552)。roastlib/energy.py の但し書き参照。
  補正(学習) その豆が芯からどれだけずれるか。標高・生産国・品種ごとに持つ。

芯は「この焙煎機はどう振る舞うか」、補正は「この豆はどう違うか」。混ぜない。

■ 1ハゼ時刻の意味
利用者が記録するのは「ハゼ音の2回目」。ごく一部の細胞が壊れ始めた時点であって、
爆ぜる勢いの山(分布の中心)はもう少し後になる。モデルの crack_start も
burst_fraction が5%に達した時点として置いてあり、同じ意味に揃えてある
(energy.py の burst_fraction の但し書き参照)。だから記録の時刻をそのまま
モデルの豆温度に当てて比べてよい。

「ハゼた!」ボタンを押さなかった場合、1ハゼ時刻はガイド温度への到達から自動で
入る(fc_time_inferred)。これで学習するとモデルが自分の予測を学び直す循環に
なるので、必ず除く。

■ 学習に使う記録の条件
  焙煎カーブ / 1ハゼ時刻(実測。推定値は不可) / 焙煎前重量 / 焙煎後重量
が揃っていること。測らずに走らせた焙煎で学習すると、モデルが自分の予測を
学び直す循環になる。
2ハゼ時刻は任意。2ハゼの手前で終わるプロファイルもあるので、無ければ
「2ハゼの分解割合」だけ学習に加えず、1ハゼと重量は使う。

■ 新しい生産国・品種が来ても壊れないようにする
項目ごとの補正は、件数で薄める(縮小推定):
    補正 = その項目の平均ずれ × n / (n + SHRINK)
n=0(未知の項目)なら0 = 全体の標準どおり。件数が増えるほどその項目自身の値へ
近づく。1件だけで大きく振れることがなく、項目が増えても勝手に育つ。
"""
from __future__ import annotations
from typing import Optional
import statistics

from . import energy as E

# 何件で「その項目自身の値」を半分信じるか。3件で半分、9件で3/4。
SHRINK = 3.0

# 学習する軸。記録から値を引く関数は呼び出し側が渡す。
# variety(品種)は「ブルボン、ティピカ」のように複数並ぶことがあるので、
# 区切って1件ずつに数える。1本の焙煎が複数の品種に効く。
AXES = ("altitude", "country", "variety", "process")
MULTI_AXES = ("variety",)
_SEPARATORS = ",、／/・&＋+と"


def split_axis(value: str) -> list:
    """軸の値を項目名の一覧にする。複数並ぶ場合(品種など)は分ける。"""
    v = (value or "").strip()
    if not v:
        return ["未分類"]
    out = []
    buf = ""
    for ch in v:
        if ch in _SEPARATORS:
            if buf.strip():
                out.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append(buf.strip())
    return out or ["未分類"]


# ■ 初期データ(2026-08〜09、ケニア ニエリ・標高1800m・生豆50g)
# 校正用プロファイルを実際に焼いて測った5本。学習の出発点であり、ログが1件も
# 無いうちはこれだけで補正が決まる。ログが増えれば、その中に混ぜて薄まっていく。
#
# 記録に残す理由: 芯の定数(energy.py)はこの5本から決めたが、そこには「この豆が
# どうだったか」という情報も畳み込まれている。学習を初期化したとき、何も無い
# 状態ではなくこの5本に戻れるようにしておく。
#
# 「豆温度学習データ初期化」を押すと、学習はこの5本だけの状態に戻る。
# 構造の見直し(roastlib/structure.py)に使う、較正5本の完全なデータ。
# プロファイルと実測値の組。要約値(SEED_ROASTS)と違い、定数を当てはめ直せる。
# 遅い昇温と発熱の検証はプロファイルを削除したが、実測は残す価値があるので
# ここに形ごと持っておく。
REFERENCE_ROASTS = [
    {"name": "校正用(深煎り)",
     "roast": [[0, 185], [60, 95], [240, 180], [440, 216], [600, 240], [680, 247]],
     "fan": [[0, 50], [1, 80], [300, 70], [680, 60]],
     "green_g": 50.0, "moisture": 0.11,
     "meas": {"fcStart": 442.0, "fcEnd": 508.0, "scStart": 622.0, "roastedG": 40.9,
              "aborts": [[180, 49.2], [240, 49.0], [300, 49.0], [390, 47.9]]}},
    {"name": "校正用(浅煎り)",
     "roast": [[0, 185], [60, 95], [240, 180], [440, 216], [560, 222]],
     "fan": [[0, 50], [1, 80], [300, 70], [560, 60]],
     "green_g": 50.0, "moisture": 0.11,
     "meas": {"fcStart": 432.0, "fcEnd": 542.0, "roastedG": 43.1}},
    {"name": "校正用(展開長め)",
     "roast": [[0, 185], [60, 95], [240, 180], [500, 210], [760, 240]],
     "fan": [[0, 50], [1, 80], [300, 70], [760, 60]],
     "green_g": 50.0, "moisture": 0.11,
     "meas": {"fcStart": 519.0, "fcEnd": 547.0, "scStart": 695.0, "roastedG": 41.7}},
    {"name": "発熱の検証用(プロファイルは削除済み)",
     "roast": [[0, 185], [60, 95], [240, 180], [440, 216], [600, 240],
               [650, 244], [670, 218], [760, 218]],
     "fan": [[0, 50], [1, 80], [300, 70], [760, 60]],
     "green_g": 50.0, "moisture": 0.11,
     "meas": {"scStart": 618.0}},
    {"name": "遅い昇温の検証用(プロファイルは削除済み)",
     "roast": [[0, 185], [60, 95], [300, 150], [480, 180], [720, 206], [900, 209]],
     "fan": [[0, 50], [1, 80], [300, 70], [900, 58]],
     "green_g": 50.0, "moisture": 0.11,
     "meas": {"fcStart": 770.0, "roastedG": 43.1}},
]


def record_to_roast(rec: dict, moisture: float) -> Optional[dict]:
    """焙煎記録を、構造の見直しに使える形(プロファイル+実測)に変える。

    プロファイルには実測の吸入温度カーブを使う。設計値ではなく実際に流れた
    空気の温度なので、こちらのほうが正確。
    """
    if not is_learnable(rec):
        return None
    curve = [[p[0], p[1]] for p in rec["roast_curve"]]
    # 重量とハゼは、それぞれ有るものだけを観測にする(is_learnable と同じ考え方)。
    # 片方しか無い記録も学習には使えるので、無いほうを必須にしてはいけない。
    meas = {}
    if has_weight(rec):
        meas["roastedG"] = float(rec["roasted_g"])
    if has_real_crack(rec):
        meas["fcStart"] = float(rec["fc_time"])
        if rec.get("sc_time"):
            meas["scStart"] = float(rec["sc_time"])
    if not meas:
        return None
    return {"name": rec.get("profile_name") or "焙煎ログ",
            "roast": curve,
            "fan": [[p[0], p[1]] for p in (rec.get("fan_curve") or [])] or None,
            # 重量が無ければ焙煎機の仕様どおり50gとして熱を計算する
            "green_g": float(rec["green_g"]) if has_weight(rec) else E.BEAN_G,
            "moisture": moisture, "meas": meas}


SEED_ROASTS = [
    {"name": "校正用(深煎り)", "fcBeanTemp": 196.9, "scDryFrac": 0.0668,
     "indexRatio": 1.000, "country": "ケニア", "variety": "", "altitude": "1500-2000m"},
    {"name": "校正用(浅煎り)", "fcBeanTemp": 195.4, "scDryFrac": None,
     "indexRatio": 0.978, "country": "ケニア", "variety": "", "altitude": "1500-2000m"},
    {"name": "発熱の検証用", "fcBeanTemp": 196.5, "scDryFrac": 0.0653,
     "indexRatio": 1.000, "country": "ケニア", "variety": "", "altitude": "1500-2000m"},
    {"name": "遅い昇温の検証用", "fcBeanTemp": 197.3, "scDryFrac": None,
     "indexRatio": 0.986, "country": "ケニア", "variety": "", "altitude": "1500-2000m"},
    {"name": "校正用(展開長め)", "fcBeanTemp": 198.0, "scDryFrac": 0.0689,
     "indexRatio": 0.985, "country": "ケニア", "variety": "", "altitude": "1500-2000m"},
]


def _bean_temp_at(series, t: float) -> Optional[float]:
    """その時刻の豆温度。範囲外ならNone。"""
    if t is None or not series or t < 0 or t > series[-1]["t"]:
        return None
    i = min(int(t), len(series) - 1)
    return series[i]["bean"]


def _dry_frac_at(series, t: float, moisture: float, chaff_g: float,
                 green_g: float) -> Optional[float]:
    """その時刻までに乾物の何割が分解したか。2ハゼの判定量にあたる。

    series の mass は「乾物+チャフ+水」の合計なので、乾物だけを取り出すには
    水とチャフの残りが要る。ここでは近似せず、質量の減りから水の寄与を引く。
    2ハゼの頃には水はほぼ抜けているので、この近似の誤差は小さい。
    """
    if t is None or not series or t < 0 or t > series[-1]["t"]:
        return None
    i = min(int(t), len(series) - 1)
    m0 = series[0]["mass"]
    water0 = m0 * moisture
    chaff0 = min(chaff_g / 1000.0 * (green_g / E.BEAN_G),
                 m0 * (1.0 - moisture) * 0.5)
    dry0 = m0 * (1.0 - moisture) - chaff0
    if dry0 <= 0:
        return None
    # 2ハゼの時点では水とチャフは抜けきっているとみなす
    lost = m0 - series[i]["mass"] - water0 - chaff0
    return max(lost, 0.0) / dry0


def has_weight(rec: dict) -> bool:
    """焙煎前後の重量が揃っているか。"""
    try:
        g = float(rec.get("green_g") or 0)
        r = float(rec.get("roasted_g") or 0)
    except (TypeError, ValueError):
        return False
    return g > 0 and 0 < r < g


def has_real_crack(rec: dict) -> bool:
    """1ハゼが「ハゼた!」ボタンの実測か。自動で入った値は使わない。"""
    return bool(rec.get("fc_time")) and not rec.get("fc_time_inferred")


def is_learnable(rec: dict) -> bool:
    """その記録が学習に使えるか。

    焙煎カーブがあり、そのうえで「重量」か「実測の1ハゼ時刻」の
    どちらかがあれば使える。両方あればどちらの補正にも効く。
      重量あり     → 質量の補正(indexRatio)
      1ハゼあり    → 1ハゼ豆温度の補正(fcBeanTemp)、2ハゼもあれば scDryFrac
    カーブは実測の吸入温度なので、熱の入力はどちらの場合も正確に分かっている。
    「ハゼた!」ボタンを押さず自動で入った1ハゼは、モデルが自分の予測を学び直す
    循環になるので数えない。
    """
    if len(rec.get("roast_curve") or []) < 2:
        return False
    return has_weight(rec) or has_real_crack(rec)


def observe(rec: dict, moisture: float, cal: Optional[dict] = None,
            chaff_g: float = E.CHAFF_G) -> Optional[dict]:
    """1本の記録から、芯からのずれを3つ取り出す。

    fcBeanTemp   1ハゼが起きた豆温度(モデル基準)
    scDryFrac    2ハゼが起きた時点の乾物の分解割合
    indexRatio   焙煎後の重量から出る焙煎指数 ÷ モデルの予想
    """
    if not is_learnable(rec):
        return None
    # 重量が無い記録もある。その場合は焙煎機の仕様どおり50gとして熱を計算する
    # (質量の補正には使わないので、この仮定は1ハゼ豆温度にしか影響しない)。
    green = float(rec["green_g"]) if has_weight(rec) else E.BEAN_G
    curve = [[p[0], p[1]] for p in rec["roast_curve"]]
    fan = [[p[0], p[1]] for p in (rec.get("fan_curve") or [])] or None
    c = dict(cal or {})
    c.setdefault("CHAFF_G", chaff_g)
    r = E.estimate(curve, fan, bean_g=green, moisture=moisture, cal=c)
    if not r:
        return None
    s = r["series"]
    # 1ハゼは任意。ボタンを押さなかった記録でも、重量だけは使う。
    fc_temp = None
    if has_real_crack(rec):
        fc_temp = _bean_temp_at(s, float(rec["fc_time"]))
    # 2ハゼも任意。2ハゼの手前で終わるプロファイルでは記録されない。
    # 1ハゼが無ければ2ハゼも信用しない(押していない可能性が高い)。
    sc_frac = None
    if fc_temp is not None and rec.get("sc_time"):
        sc_frac = _dry_frac_at(s, float(rec["sc_time"]), moisture, chaff_g, green)
    ratio = None
    if has_weight(rec):
        want = green / float(rec["roasted_g"])
        got = r["roast_index"]
        ratio = (want / got) if got > 0 else None
    if fc_temp is None and ratio is None:
        return None
    return {"fcBeanTemp": fc_temp, "scDryFrac": sc_frac, "indexRatio": ratio}


def _shrunk(values: list, base: float) -> float:
    """件数で薄めた平均。件数が少ないほど base に寄せる。"""
    if not values:
        return base
    n = len(values)
    m = statistics.median(values)
    w = n / (n + SHRINK)
    return base + (m - base) * w


def learn(records, moisture: float, cal: Optional[dict] = None,
          axis_of=None, chaff_g: float = E.CHAFF_G,
          use_seed: bool = True) -> dict:
    """記録の一覧から、全体と軸ごとの補正を作る。

    axis_of: (軸名, 記録) -> その記録の項目名(文字列)。空文字なら「未分類」。
    use_seed: 初期データ(SEED_ROASTS)を混ぜるか。既定は混ぜる。
    戻り値:
      {"n": 使えた件数, "skipped": 使えなかった件数,
       "global": {量: 値}, "axes": {軸: {項目: {量: 値, "n": 件数}}}}
    """
    obs = []
    skipped = 0
    if use_seed:
        for sd in SEED_ROASTS:
            obs.append({k: sd.get(k) for k in ("fcBeanTemp", "scDryFrac", "indexRatio")}
                       | {"_rec": {"_seed": sd}})
    for rec in records or []:
        o = observe(rec, moisture, cal, chaff_g)
        if o is None:
            skipped += 1
            continue
        o["_rec"] = rec
        obs.append(o)
    keys = ("fcBeanTemp", "scDryFrac", "indexRatio")
    base = {"fcBeanTemp": E.T_FC_BEAN, "scDryFrac": E.SC_DRY_FRAC,
            "indexRatio": 1.0}
    g = {}
    for k in keys:
        v = [o[k] for o in obs if o.get(k) is not None]
        g[k] = _shrunk(v, base[k])
    axes = {}
    if axis_of is not None:
        for ax in AXES:
            groups = {}
            for o in obs:
                rec = o["_rec"]
                raw = (rec["_seed"].get(ax) if "_seed" in rec else axis_of(ax, rec)) or ""
                names = split_axis(raw) if ax in MULTI_AXES else [
                    (raw.strip() or "未分類")]
                for name in names:
                    groups.setdefault(name, []).append(o)
            out = {}
            for name, items in groups.items():
                e = {"n": len(items)}
                for k in keys:
                    v = [i[k] for i in items if i.get(k) is not None]
                    # 軸ごとの補正は「全体の値」を base にして薄める
                    e[k] = _shrunk(v, g[k])
                out[name] = e
            if out:
                axes[ax] = out
    return {"n": len(obs), "seed": len(SEED_ROASTS) if use_seed else 0,
            "logs": len(obs) - (len(SEED_ROASTS) if use_seed else 0),
            "skipped": skipped, "global": g, "axes": axes}
