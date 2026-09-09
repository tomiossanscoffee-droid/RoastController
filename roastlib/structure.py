"""モデルの構造(どの定数を当てはめるか)を、いま手元にある観測から選び直す。

■ なぜ手で押す仕組みにしているか
定数を増やせば必ず残差は下がる。増やしてよいかは観測の数で決まる。観測が
少ないうちに増やすと「当てはまるが、見せていない焙煎には効かない」状態になる。
実際このモデルでは、観測15点のとき定数を4→6個にすると残差は19.46まで下がるのに、
焙煎1本抜きの交差検証では予測が31.83→63.69と倍悪くなった。

一方で観測が増えれば話は変わる。定数を1個増やすのに必要な「残差の減り」は
    観測19点 17.9% / 30点 9.2% / 60点 3.9% / 100点 2.2%
と急速に緩む。60点あれば、いま却下した機構のいくつかは通る。

だから「いつ見直すか」はデータ次第で、勝手に変えるべきではない。構造が変わると
ハゼ時刻や焙煎指数の意味が変わり、焙煎中の判断に影響する。補正の学習
(roastlib/learning.py、連続的で小さい)とは性格が違うので、明示的な操作にする。

■ 判定
AICc(小標本補正つき情報量規準)で候補を並べ、勝った構造を焙煎1本抜きの
交差検証にかける。交差検証で今より悪ければ採らない。当てはまりだけで選ぶと
過剰当てはめを見抜けないため、独立な2つの基準を両方通す。
"""
from __future__ import annotations
import math
import os
import random
from concurrent.futures import ProcessPoolExecutor
from typing import Optional

from . import energy as E

# 当てはめる候補。左から順に「まず要る」もの。前向き段階選択でここから採る。
# 値は energy.py の既定値に対する倍率(T_FC_BEANだけは絶対値の℃)。
POOL = ("U0", "K_PYRO", "K_DRY", "CRACK_SPREAD", "T_FC_BEAN",
        "H_ENDO", "U_WET", "VENT_SPREAD")
ALWAYS = ("U0", "K_PYRO", "K_DRY")     # これだけは常に当てはめる

BOUNDS = {
    "U0": (0.4, 2.0), "K_PYRO": (0.2, 5.0), "K_DRY": (0.2, 5.0),
    "CRACK_SPREAD": (0.3, 3.0), "H_ENDO": (0.2, 3.0), "U_WET": (0.05, 1.0),
    "VENT_SPREAD": (0.3, 3.0), "T_FC_BEAN": (186.0, 208.0),
}
START = {"U0": 1.0, "K_PYRO": 1.0, "K_DRY": 1.0, "CRACK_SPREAD": 1.0,
         "H_ENDO": 1.0, "U_WET": 1.0, "VENT_SPREAD": 1.0, "T_FC_BEAN": E.T_FC_BEAN}

# 測定の精度。残差はこれで割ってから足す(単位の違う量を同じ土俵に載せる)。
PREC = {"fc": 8.0, "fe": 12.0, "sc": 10.0, "g": 0.25, "abort": 0.4}

# 探索中の内部刻み。既定(8)より粗くして時間を稼ぐ。実測では substeps=2 で
# 3.8倍速くなり、焙煎後重量の差は3mg(測定精度0.25gの1/80)。採否を決める
# 最終の当てはめと交差検証は既定に戻して確かめる。
SEARCH_SUBSTEPS = 2


def _cal_of(free, x, base=None):
    c = dict(base or {})
    for n, v in zip(free, x):
        if n == "T_FC_BEAN":
            c["T_FC_BEAN"] = v
        elif n == "K_DRY":
            c["K_SURFACE"] = E.K_SURFACE * v
            c["K_INNER"] = E.K_INNER * v
        elif n == "U_WET":
            # U_WETは倍率ではなく値そのもの(0〜1)
            c["U_WET"] = v * E.U_WET
        else:
            c[n] = getattr(E, n) * v
    return c


def residuals(free, x, roasts, base=None, substeps=None):
    """観測ごとの残差(精度で割った無次元)。1本でも計算できなければ None。"""
    c = _cal_of(free, x, base)
    out = []
    for ro in roasts:
        r = E.estimate(ro["roast"], ro.get("fan"), bean_g=ro.get("green_g", E.BEAN_G),
                       moisture=ro.get("moisture", E.MOISTURE), cal=c,
                       substeps=substeps)
        if not r:
            return None
        m = ro["meas"]
        if m.get("fcStart") is not None:
            if r["crack_start"] is None:
                return None
            out.append((r["crack_start"] - m["fcStart"]) / PREC["fc"])
        if m.get("fcEnd") is not None:
            if r["crack_end"] is None:
                return None
            out.append((r["crack_end"] - m["fcEnd"]) / PREC["fe"])
        if m.get("scStart") is not None:
            if r.get("second_crack") is None:
                return None
            out.append((r["second_crack"] - m["scStart"]) / PREC["sc"])
        if m.get("roastedG") is not None:
            out.append((r["roasted_g"] - m["roastedG"]) / PREC["g"])
        for t, g in (m.get("aborts") or []):
            s = r["series"]
            out.append((s[min(int(t), len(s) - 1)]["mass"] * 1000.0 - g) / PREC["abort"])
        # 豆が空気より熱いこと自体は有り得る(吸入温度を落とした後など)。
        # 有り得ないのは「空気より熱いのに、まだ温度が上がっている」状態。
        # 熱は空気から豆へしか流れないので、追い越したら必ず冷える側に向かう。
        ser = r["series"]
        if any(b["bean"] > b["air"] + 0.5 and b["bean"] > a["bean"] + 1e-9
               for a, b in zip(ser, ser[1:])):
            out.append(30.0)
    return out


def rss(free, x, roasts, base=None, substeps=None):
    o = residuals(free, x, roasts, base, substeps)
    return 1e9 if o is None else sum(v * v for v in o)


def n_obs(roasts):
    n = 0
    for ro in roasts:
        m = ro["meas"]
        n += sum(1 for k in ("fcStart", "fcEnd", "scStart", "roastedG")
                 if m.get(k) is not None)
        n += len(m.get("aborts") or [])
    return n


def fit(free, roasts, base=None, trials=3, seed=7, iters=150, substeps=None):
    """座標降下。

    trials=3 / iters=150 は実測で決めた。基準5本では trials=6 / iters=250 と
    同じ解に収束し、時間は半分(45秒→24秒)。焙煎が増えると1回の計算も増えるので、
    無駄な繰り返しを持たない。
    """
    rnd = random.Random(seed)
    best = (1e18, None)
    for t in range(trials):
        x = [START[n] for n in free] if t == 0 else [
            min(max(START[n] * math.exp(rnd.uniform(-0.5, 0.5)),
                    BOUNDS[n][0]), BOUNDS[n][1])
            if n != "T_FC_BEAN" else
            min(max(START[n] + rnd.uniform(-6, 6), BOUNDS[n][0]), BOUNDS[n][1])
            for n in free]
        step = [0.15] * len(x)
        cur = rss(free, x, roasts, base, substeps)
        for _ in range(iters):
            moved = False
            for i, n in enumerate(free):
                lo, hi = BOUNDS[n]
                for sg in (1, -1):
                    y = list(x)
                    y[i] = (y[i] + sg * step[i] * 12) if n == "T_FC_BEAN" \
                        else y[i] * (1 + sg * step[i])
                    y[i] = min(max(y[i], lo), hi)
                    v = rss(free, y, roasts, base, substeps)
                    if v < cur - 1e-10:
                        x, cur, moved = y, v, True
                        break
                if moved:
                    break
            if not moved:
                step = [s * 0.55 for s in step]
                if max(step) < 5e-4:
                    break
        if cur < best[0]:
            best = (cur, x)
    return best


def aicc(r, k, n):
    if n - k - 1 <= 0 or r <= 0:
        return float("inf")
    return n * math.log(r / n) + 2 * k + 2 * k * (k + 1) / (n - k - 1)


def _fit_one(args):
    """並列実行用。プロセス間で渡すので、モジュールの最上位に置く。"""
    free, roasts, base, substeps = args
    r, x = fit(free, roasts, base, substeps=substeps)
    return free, r, x


def select(roasts, base=None, progress=None, workers=None):
    """前向き段階選択でAICcが最も良い構造を選ぶ。

    progress: (段階, 全体, 説明) を受け取る関数(任意)。時間がかかるので進捗を出す。
    """
    n = n_obs(roasts)
    free = list(ALWAYS)
    r, x = fit(free, roasts, base, substeps=SEARCH_SUBSTEPS)
    cur = aicc(r, len(free), n)
    steps = [{"free": list(free), "rss": r, "aicc": cur}]
    rest = [p for p in POOL if p not in free]
    # 進捗は「試した候補の数」で数える。巡ごとに数えると、1巡が数分かかるあいだ
    # 表示が止まって見えて、固まったのか動いているのか分からない。
    done = 0
    todo = sum(range(1, len(rest) + 1))
    if workers is None:
        workers = max(1, min(len(rest), (os.cpu_count() or 2) - 1))
    pool = None
    if workers > 1:
        try:
            pool = ProcessPoolExecutor(max_workers=workers)
        except Exception:  # noqa: BLE001
            pool = None
    try:
        while rest:
            jobs = [(free + [p], roasts, base, SEARCH_SUBSTEPS) for p in rest]
            if pool is not None:
                try:
                    results = list(pool.map(_fit_one, jobs))
                except Exception:  # noqa: BLE001
                    pool = None
                    results = [_fit_one(j) for j in jobs]
            else:
                results = [_fit_one(j) for j in jobs]
            cands = []
            for (f2, r2, x2), p in zip(results, rest):
                done += 1
                if progress:
                    progress(done, todo, f"{p} を試しました")
                cands.append((aicc(r2, len(f2), n), r2, p, f2, x2))
            if not cands:
                break
            cands.sort(key=lambda z: z[0])
            a2, r2, p, f2, x2 = cands[0]
            steps.append({"tried": p, "rss": r2, "aicc": a2,
                          "adopted": a2 < cur - 1e-9})
            if a2 >= cur - 1e-9:
                break
            free, cur, r, x = f2, a2, r2, x2
            rest = [q for q in rest if q != p]
    finally:
        if pool is not None:
            pool.shutdown(wait=False)
    # 採否を決める前に、少し細かい刻みで当てはめ直して確かめる。
    # 既定(8)にすると焙煎15本で8分かかり、所要の3分の1を占めた。4なら半分の
    # 時間で、焙煎後重量の差は1mg(測定精度0.25gの1/250)しかない。
    if progress:
        progress(todo, todo, "細かい刻みで確かめています")
    r, x = fit(free, roasts, base, trials=2, substeps=4)
    return {"free": free, "x": x, "rss": r, "aicc": aicc(r, len(free), n),
            "n": n, "steps": steps}


def robust_outliers(roasts, free=None, x=None, base=None, k=3.0,
                    substeps=SEARCH_SUBSTEPS):
    """他と大きく外れている焙煎を見つける。捨てるのではなく、名前を返す。

    構造の見直しは残差の二乗和で選ぶので、記録ミス1件(ハゼボタンの押し遅れ、
    計量ミス)が全体を支配してしまう。補正の層(learning.py)は中央値なので
    もともと頑健だが、こちらは対策が要る。

    中央絶対偏差(MAD)で測る。標準偏差だと外れ値自身がばらつきを膨らませて
    自分を隠してしまうため。
    """
    free = list(free or ALWAYS)
    if x is None:
        _, x = fit(free, roasts, base, substeps=substeps)
    if x is None:
        return [], []
    per = []
    for ro in roasts:
        o = residuals(free, x, [ro], base, substeps)
        per.append(1e9 if o is None else sum(v * v for v in o) / max(len(o), 1))
    ok = [v for v in per if v < 1e8]
    if len(ok) < 4:
        return [], per          # 少なすぎると外れ値を語れない
    ok.sort()
    med = ok[len(ok) // 2]
    dev = sorted(abs(v - med) for v in ok)
    mad = dev[len(dev) // 2] or 1e-9
    bad = [i for i, v in enumerate(per) if v >= 1e8 or (v - med) / (1.4826 * mad) > k]
    return bad, per


def select_subset(roasts, cap, axis_of=None, axes=("altitude", "country",
                                                  "variety", "process")):
    """構造の見直しに使う焙煎を、上限capまで選ぶ。

    ■ なぜ間引くか
    構造の見直しは焙煎の本数に比例して時間が伸びる(実測 5本で9分、100本なら3時間)。
    一方、似た焙煎を何十本足しても判別力はほとんど増えない(同じプロファイルを
    5回焼いた記録では1ハゼのばらつきが6.3秒しかない)。

    ■ 何を守るか
    まず、標高・生産国・品種・精製方法のそれぞれについて、出てくる値を1つ残らず
    1本以上入れる。構造は機械の物理なので産地には依らないはずだが、ある条件だけ
    系統的に違う振る舞いをするなら、それを含まない部分集合で選んだ構造は
    取りこぼす。そのあと、1ハゼ時刻と焙煎後重量が散らばるように埋める。

    ■ AICcのnについて
    呼び出し側は、選ばれた部分集合の観測数を使うこと。全体の数を使うと、
    実際には見ていないデータで複雑さを正当化することになる。
    """
    if len(roasts) <= cap:
        return list(roasts), []
    picked, rest = [], list(range(len(roasts)))

    def take(i):
        picked.append(i)
        rest.remove(i)

    # 1) 各軸の各値から最低1本
    if axis_of is not None:
        for ax in axes:
            seen = set()
            for i in list(rest):
                v = (axis_of(ax, roasts[i]) or "未分類").strip() or "未分類"
                if v not in seen:
                    seen.add(v)
                    if len(picked) < cap:
                        take(i)
    # 2) 残りは1ハゼ時刻と焙煎後重量が散らばるように
    def key(i):
        m = roasts[i]["meas"]
        return (m.get("fcStart") or 0.0, m.get("roastedG") or 0.0)

    rest.sort(key=key)
    while rest and len(picked) < cap:
        # 一番離れているものから採る(端 → 中央 → その間、と埋めていく)
        step = max(len(rest) // max(cap - len(picked), 1), 1)
        take(rest[0] if step <= 1 else rest[min(step // 2, len(rest) - 1)])
    picked.sort()
    dropped = [i for i in range(len(roasts)) if i not in picked]
    return [roasts[i] for i in picked], dropped


# 交差検証の分割数の上限。焙煎の本数だけ当てはめ直すので、ここが一番重い。
# 実測では焙煎16本の1本抜きで1構造あたり1102秒かかった。束ねて8分割にすると
# 半分になり、さらに並列化で実用的な時間に収まる。
CV_MAX_FOLDS = 8


def _cv_one(args):
    """並列実行用。プロセス間で渡すのでモジュールの最上位に置く。"""
    free, train, held, base, substeps, trials = args
    _, x = fit(free, train, base, trials=trials, substeps=substeps)
    if x is None:
        return None
    return min(rss(free, x, held, base, substeps), 500.0)


def cross_validate(free, roasts, base=None, substeps=SEARCH_SUBSTEPS,
                   trials=2, progress=None, workers=None):
    """焙煎を分けて、片方で当てはめ、もう片方を予測する。

    3本以上ないと意味がないので、その場合は None を返す。
    本数が CV_MAX_FOLDS を超えたら束ねる(1本抜きにこだわらない)。比べる2つの
    構造に同じ分け方で効くので、優劣の判定は変わらない。
    """
    n = len(roasts)
    if n < 3:
        return None
    k = min(n, CV_MAX_FOLDS)
    folds = [[] for _ in range(k)]
    for i in range(n):
        folds[i % k].append(i)      # 順に配って、偏りを避ける
    jobs = []
    for f in folds:
        held = [roasts[i] for i in f]
        train = [r for j, r in enumerate(roasts) if j not in f]
        if len(train) < 2:
            return None
        jobs.append((list(free), train, held, base, substeps, trials))
    if workers is None:
        workers = max(1, min(k, (os.cpu_count() or 2) - 1))
    results = None
    if workers > 1:
        try:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(_cv_one, jobs))
        except Exception:  # noqa: BLE001
            results = None
    if results is None:
        results = []
        for i, j in enumerate(jobs):
            if progress:
                progress(i + 1, len(jobs))
            results.append(_cv_one(j))
    if any(v is None for v in results):
        return None
    if progress:
        progress(len(jobs), len(jobs))
    return sum(results)
