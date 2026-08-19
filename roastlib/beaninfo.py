# ============================================================
# Roast Studio
# beaninfo.py : 焙煎機純正アプリの豆情報CSV(THE_ROAST_Extract/beaninfo/)を
#               profile.name の焙煎プロファイルに紐づけるためのモジュール。
# ------------------------------------------------------------
# CSVファイル名は "GB" + 14桁の数字 + "_beaninfo.csv" という構造で、
# 先頭4桁がprofile.nameに埋め込まれた番号(例: "1001_ブラジル_サントアントニオ"の
# "1001")と一致することを実データで確認済み(2026-07)。この4桁を bean_code として
# 紐付けキーに使う。
#
# CSVの列構成(0始まり、28列、実データから特定・2026-07確認済み):
#   0  国(JP)\n産地,商品名                 8  焙煎レベル1ラベル(Light Roast等)
#   1  国名(EN, 大文字)                     9  焙煎レベル2ラベル
#   2  おすすめ焙煎レベル("no use"あり)     10  焙煎レベル3ラベル(無い場合は空)
#   3  緯度                                11  豆の紹介文(JP)
#   4  経度                                12  生産国(JP, 正式名)
#   5  画像注記(固定文言)                  13  地区
#   6  国名(EN, 6と重複)                   14  生産者
#   7  産地・商品名(EN)                    15  品種
#   16 標高                                22  クレジット("Profiles by ...")
#   17 精製方法                            23-27 ハッシュタグ
#   18 "Beans Story"(固定ラベル)
#   19 焙煎レベル1のテイスティングメモ
#   20 焙煎レベル2のテイスティングメモ
#   21 焙煎レベル3のテイスティングメモ(無い場合はダッシュ/全角スペースのプレースホルダ)
# ============================================================
from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from roastlib.profile_generator import _BASE_TEMPLATES

BEANINFO_DIR = Path(__file__).resolve().parent.parent / "THE_ROAST_Extract" / "beaninfo"

_FILENAME_RE = re.compile(r"^GB(\d{4})(\d{4})(\d{6})_beaninfo\.csv$")

# CSVの空欄が"---\n---"や全角スペースの繰り返しで埋められているケースがあるため、
# 実質空とみなす判定に使う。
_PLACEHOLDER_RE = re.compile(r"^[\s　\-]*$")

_UNSPECIFIED_VARIETIES = {"指定なし", "指定無し", "不特定"}

# CSVの焙煎レベルラベルは英語表記("Medium Dark Roast"等)のため、表示用に和訳する。
_ROAST_LABEL_JA = {
    "Light Roast": "浅煎り",
    "Medium Roast": "中煎り",
    "Medium Dark Roast": "中深煎り",
    "Dark Roast": "深煎り",
}


def _clean(s: str) -> str:
    return s.replace("\n", " ").strip() if s else ""


def _is_placeholder(s: str) -> bool:
    return not s or bool(_PLACEHOLDER_RE.match(s))


def _parse_varieties(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw or raw in _UNSPECIFIED_VARIETIES:
        return []
    # 区切りは"、"","の他、半角・全角スペースにも対応する
    tokens = re.split(r"[、,\s　]+", raw)
    result = []
    for tok in tokens:
        tok = tok.strip()
        tok = re.sub(r"(など多数|など|等)$", "", tok).strip()
        if tok and tok not in _UNSPECIFIED_VARIETIES:
            result.append(tok)
    return result


def _parse_altitude_bucket(raw: str) -> str:
    nums = [int(n.replace(",", "")) for n in re.findall(r"[\d,]+", raw)]
    if not nums:
        return "指定なし"
    value = sum([min(nums), max(nums)]) / 2
    if value < 1000:
        return "1000m未満"
    if value < 1500:
        return "1000-1500m"
    if value < 2000:
        return "1500-2000m"
    return "2000m以上"


def _parse_one_csv(path: Path) -> Optional[dict]:
    m = _FILENAME_RE.match(path.name)
    if not m:
        return None
    bean_code, _mid, _date = m.groups()

    with path.open(encoding="utf-8", errors="replace") as fh:
        row = next(csv.reader(fh))
    if len(row) < 28:
        return None

    name_lines = row[0].split("\n")
    country_ja = _clean(name_lines[0]) if name_lines else ""
    rest = name_lines[1] if len(name_lines) > 1 else ""
    bean_ja, _, product_ja = rest.partition(",")
    bean_ja = bean_ja.strip()
    product_ja = product_ja.strip()

    labels_raw = [row[8], row[9], row[10]]
    memos_raw = [row[19], row[20], row[21]]
    roast_levels = []
    for label, memo in zip(labels_raw, memos_raw):
        label = _clean(label)
        if not label:
            continue
        roast_levels.append({
            "label": _ROAST_LABEL_JA.get(label, label),
            "memo": "" if _is_placeholder(memo) else _clean(memo),
        })

    altitude_raw = _clean(row[16])
    variety_raw = _clean(row[15])

    return {
        "bean_code": bean_code,
        "country": country_ja,
        "bean": bean_ja,
        "product": product_ja,
        "country_full": _clean(row[12]),
        "region": _clean(row[13]),
        "farm": _clean(row[14]),
        "variety_raw": variety_raw,
        "variety": _parse_varieties(variety_raw),
        "altitude_raw": altitude_raw,
        "altitude_bucket": _parse_altitude_bucket(altitude_raw),
        "process": _clean(row[17]),
        "description": _clean(row[11]),
        "roast_levels": roast_levels,
        "credit": _clean(row[22]),
        "source_file": path.name,
    }


@lru_cache(maxsize=1)
def load_beaninfo() -> dict[str, dict]:
    """bean_code(4桁の文字列) -> 豆情報dict のマップを返す(初回のみCSVを読み込みキャッシュ)。"""
    result: dict[str, dict] = {}
    if not BEANINFO_DIR.is_dir():
        return result
    for path in sorted(BEANINFO_DIR.glob("*.csv")):
        info = _parse_one_csv(path)
        if info:
            result[info["bean_code"]] = info
    return result


def get_beaninfo(bean_code: str) -> Optional[dict]:
    if not bean_code:
        return None
    return load_beaninfo().get(bean_code)


# ============================================================
# 焙煎度調整テーブル(roastLevelAdjustConf.csv)
#   THE ROAST純正アプリの「浅め/深めに調整」機能が使う公式の調整値。
#   実データを確認したところ、55件中3パターンしかなく、実質的には
#   「その豆で調整機能自体が使えるか(焙煎可否)」と、固定の調整量
#   (温度・時間・追加ポイント時間)という単純な構造だった(2026-07確認)。
# ============================================================
ROAST_ADJUST_DIR = Path(__file__).resolve().parent.parent / "THE_ROAST_Extract" / "roast_level_adjust"
_ADJUST_FILENAME_RE = re.compile(r"^GB(\d{4})\d{4}\d{6}_roastLevelAdjustConf\.csv$")


def _parse_one_adjust_csv(path: Path) -> Optional[dict]:
    m = _ADJUST_FILENAME_RE.match(path.name)
    if not m:
        return None
    bean_code = m.group(1)

    rows: dict[str, str] = {}
    with path.open(encoding="utf-8", errors="replace") as fh:
        for row in csv.reader(fh):
            if len(row) >= 2:
                rows[row[0].strip()] = row[1].strip()

    def num(key: str) -> Optional[float]:
        try:
            return float(rows[key])
        except (KeyError, ValueError):
            return None

    adjustable = rows.get("焙煎可否") == "1"
    # レベル1/2/3で値が同じ場合がほとんどのため、レベル1の値を代表値として使う
    # (実データではレベル間の差は無かった。将来レベルごとに異なる値が
    #  見つかった場合はここを拡張する)。
    lighter = {
        "delta_temp": num("焙煎度1：浅め：温度調整値(ΔT)"),
        "delta_time": num("焙煎度1：浅め：時間調整値(Δt)"),
        "extra_point_time": num("焙煎度1：浅め：追加ポイント時間(ΔP)"),
    }
    deeper = {
        "delta_temp": num("焙煎度1：深め：温度調整値(ΔT)"),
        "delta_time": num("焙煎度1：深め：時間調整値(Δt)"),
        "extra_point_time": num("焙煎度1：深め：追加ポイント時間(ΔP)"),
    }
    if any(v is None for v in lighter.values()) or any(v is None for v in deeper.values()):
        return None

    return {"bean_code": bean_code, "adjustable": adjustable, "lighter": lighter, "deeper": deeper}


@lru_cache(maxsize=1)
def load_roast_level_adjust() -> dict[str, dict]:
    """bean_code -> 焙煎度調整テーブル のマップを返す(初回のみCSVを読み込みキャッシュ)。"""
    result: dict[str, dict] = {}
    if not ROAST_ADJUST_DIR.is_dir():
        return result
    for path in sorted(ROAST_ADJUST_DIR.glob("*.csv")):
        info = _parse_one_adjust_csv(path)
        if info:
            result[info["bean_code"]] = info
    return result


def get_roast_level_adjust(bean_code: str) -> Optional[dict]:
    if not bean_code:
        return None
    return load_roast_level_adjust().get(bean_code)


_NUM_RE = re.compile(r"\d{4}")
_NO_N_RE = re.compile(r"No\.(\d+)")
# 豆コードを取り除いた残りの末尾に付いた通し番号(例: "3040タンザニア ンポジ01" の "01")。
# 「No.」を伴わない書き方の実データがあるため("3040タンザニア ンポジ01" と
# "3040タンザニアンポジNo.2" "3040タンザニアンポジNo.3" が同じ豆の3段階)。
_TRAILING_NUM_RE = re.compile(r"(\d{1,2})\s*$")


def _variant_number(name: str) -> Optional[int]:
    """プロファイル名から「同じ豆の中で何番目(=何段階目の焙煎度)か」を取り出す。

    基本は "No.1" 形式。ただし実データには "No." を伴わず末尾に "01" と書かれた
    ものがあり(タンザニア・ンポジ 3040)、番号が取れないと _label_group() の
    並び替えで最後に回され、焙煎度ラベルが1つずつずれてしまう。そのため、
    豆コードを除いた残りの末尾に数字があればそれを番号として扱う。

    "Ada farm No3 発酵ナチュラル 浅煎り" の "No3"(ドット無し)は、同じ豆の
    2つのプロファイル両方に付いている生豆ロットの番号であって段階の番号では
    ないため、意図的に拾わない(_NO_N_RE がドットを必須にしているのはこのため)。
    """
    m = _NO_N_RE.search(name)
    if m:
        return int(m.group(1))
    rest = name
    code = extract_bean_code(name) or ""
    if code and rest.startswith(code):
        rest = rest[len(code):]
    m = _TRAILING_NUM_RE.search(rest)
    if m:
        n = int(m.group(1))
        # 焙煎度の段階数は実データでは最大4。それを超える数字は別の意味
        # (ロット番号・年など)と考えて採用しない。
        if 1 <= n <= 4:
            return n
    return None

# グループの人数に応じた焙煎度ラベル(番号が若い順=浅煎り側、との実機仕様に基づく)。
_ROAST_LEVEL_LABELS = {
    1: [""],
    2: ["浅煎り", "深煎り"],
    3: ["浅煎り", "中煎り", "深煎り"],
}

# 名前に直接書かれた焙煎度キーワードから、浅い順の並び順を判定するための順位。
_ROAST_KEYWORD_RANK = {"浅煎り": 0, "中煎り": 1, "中深煎り": 2, "深煎り": 3}


def _assign_nearest_levels(remaining: list[dict], candidates: list[str]) -> Optional[dict[int, str]]:
    """CSVの宣言する焙煎度(candidates)の方がプロファイル数より多い場合に、
    各プロファイルの終了温度・終了時間(end_temp/end_t)から、profile_generator.
    infer_roast_level()と同じ距離計算で最も近いものを、重複しないよう距離が
    近い組み合わせから貪欲に割り当てる。end_temp/end_tが取れないメンバーが
    いれば判定できないためNoneを返す(呼び出し側で一般フォールバックに回す)。
    """
    if any(m.get("end_temp") is None or m.get("end_t") is None for m in remaining):
        return None
    triples = []
    for m in remaining:
        for lvl in candidates:
            tmpl = _BASE_TEMPLATES[lvl]
            dist = abs(m["end_temp"] - tmpl["end_temp"]) + abs(m["end_t"] - tmpl["end_t"]) * 0.1
            triples.append((dist, m["id"], lvl))
    triples.sort(key=lambda t: t[0])
    result: dict[int, str] = {}
    used_levels: set[str] = set()
    for _dist, mid, lvl in triples:
        if mid in result or lvl in used_levels:
            continue
        result[mid] = lvl
        used_levels.add(lvl)
        if len(result) == len(remaining):
            break
    return result if len(result) == len(remaining) else None


# 名前に4桁の番号が全く含まれておらず、機械的なID一致では紐付けられないプロファイル用の
# 手動マッピング。"Ada farm" は沖縄の農園名で、対応するbeaninfoは"6021"(日本/沖縄/AKATITI)。
# 他は、CSVの商品名(例:"ロングトーン")がプロファイル名にそのまま使われているケースで、
# beaninfo側の商品名と突き合わせて対応するbean_codeを特定した(2026-07 内容確認済み)。
_MANUAL_BEAN_CODE_OVERRIDES = {
    "Ada farm": "6021",
    "ロングトーン": "3013",      # コロンビア ウイラ ロングトーン
    "クイーンローザ": "3023",     # エチオピア イルガチャフィ クイーンローザ
    "レオロワイヤル": "3008",     # ケニア キリニャガ レオロワイヤル
    "ルーツフローラ": "6105",     # エチオピア グジ ルーツフローラ
    "カフェスプレムータ": "3011",  # コスタリカ タラス カフェスプレムータ
}


def extract_bean_code(name: str) -> Optional[str]:
    """profile.name から先頭付近の4桁の数字(beaninfoのbean_codeと対応するID)を取り出す。
    記号付き("●3024_...")や区切りなし("3039コロンビア...")の名前にも対応するため、
    NameParserのbean_code(先頭が厳密に"NNNN_"の場合のみ)より広く、文字列中で
    最初に現れる4桁の数字を採用する。数字を全く含まない名前は _MANUAL_BEAN_CODE_OVERRIDES
    を試す。CSVの豆情報を引くためだけに使うこと(焙煎度のグループ化には使わない。
    理由は_GROUPING_BEAN_CODE_OVERRIDESのコメントを参照)。
    """
    m = _NUM_RE.search(name)
    if m:
        return m.group(0)
    for keyword, bean_code in _MANUAL_BEAN_CODE_OVERRIDES.items():
        if keyword in name:
            return bean_code
    return None


# 焙煎度のグループ化専用の手動マッピング。extract_bean_code用の_MANUAL_BEAN_CODE_OVERRIDESとは
# 異なり、ここに載せてよいのは「そのbean_codeに他の既存プロファイルが存在しない」ものだけ
# (existing groupに紛れ込むと、本来3人組の浅/中/深グループが4人組になり、全員の焙煎度が
# 判定不能になってしまうため)。"ロングトーン"等はCSV検索(extract_bean_code)には使うが、
# こちらのグループ化には使わない。
_GROUPING_BEAN_CODE_OVERRIDES = {
    "Ada farm": "6021",
}


def _grouping_bean_code(name: str) -> Optional[str]:
    m = _NUM_RE.search(name)
    if m:
        return m.group(0)
    for keyword, bean_code in _GROUPING_BEAN_CODE_OVERRIDES.items():
        if keyword in name:
            return bean_code
    return None


def _group_key(p: dict) -> tuple[str, str]:
    num = _grouping_bean_code(p["name"]) or p["name"]
    return (num, p.get("roaster", ""))


def _build_roast_groups(profiles: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """同じ豆(bean_code)・同じ焙煎士のプロファイルを1グループにまとめる。"""
    groups: dict[tuple[str, str], list[dict]] = {}
    for p in profiles:
        groups.setdefault(_group_key(p), []).append({**p, "no_num": _variant_number(p["name"])})
    return groups


def _label_group(members: list[dict], group_num: str = "") -> dict[int, str]:
    """1グループ分のメンバーに焙煎度ラベルを割り当てる。

    まず、そのbean_codeのCSVが実際に宣言している焙煎レベルのラベル(浅煎り/中煎り/
    中深煎り/深煎りのいずれか)を使う。人数と過不足なく一致し、かつラベルが重複して
    いない場合は、これを最優先で採用する(例: タンザニア・タリメはCSV上「浅煎り/
    中深煎り/深煎り」の3段階であり、一律「浅煎り/中煎り/深煎り」を割り当てると
    中深煎りが中煎りとして誤って扱われてしまうため。2026-07 確認済み)。
    名前に"浅煎り"/"中煎り"/"中深煎り"/"深煎り"が直接書かれているプロファイル(例:
    "Ada farm No3 発酵ナチュラル 浅煎り")よりも、このCSV一致を優先する
    (2026-07: 日本・沖縄AdaFarmのNo3で、プロファイル名は「浅煎り/深煎り」の
    2値だが、CSV上は「浅煎り/中深煎り」であり、店舗独自の命名がCSVの実際の
    段階と食い違っていたケースを確認したため。名前の並び順(浅→深)自体は
    ヒントとして使い、CSVラベルをその順に割り当てる)。

    CSVの宣言数がグループの人数より多い場合(例: エチオピア・カフェインレスは
    CSV上「浅煎り/中煎り/中深煎り」の3段階を宣言しているが、実際のプロファイルは
    2つしか無い=一部の段階のプロファイルが作られていない)は、各プロファイルの
    終了温度・終了時間から、CSVが宣言する段階のうち最も近いものを推測する
    (profile_generator.infer_roast_level()と同じ距離計算)。

    それ以外(CSVが無い/人数が少ない等で上記が使えない)場合は、No.Nの数字が
    若い順に浅煎り→深煎りとして並んでいるという実機の一般仕様(2026-07確認済み)に
    フォールバックする。No.Nが取れないプロファイル(記号付き・区切りなしの名前)
    でも、同グループ内の他メンバーで空いている順位が1つだけなら、そこに割り当てる。
    """
    size = len(members)
    info = get_beaninfo(group_num) if group_num else None
    csv_labels = [lv["label"] for lv in info["roast_levels"]] if info else []

    if len(csv_labels) == size and len(set(csv_labels)) == size:
        def sort_key(m):
            for kw, rank in _ROAST_KEYWORD_RANK.items():
                if kw in m["name"]:
                    return (0, rank)
            if m["no_num"] is not None:
                return (1, m["no_num"])
            return (2, 0)
        ordered = sorted(members, key=sort_key)
        return {m["id"]: csv_labels[i] for i, m in enumerate(ordered)}

    explicit: dict[int, str] = {}
    for m in members:
        for kw in ("中深煎り", "深煎り", "中煎り", "浅煎り"):
            if kw in m["name"]:
                explicit[m["id"]] = kw
                break

    result: dict[int, str] = dict(explicit)
    remaining = [m for m in members if m["id"] not in explicit]
    if not remaining:
        return result

    if len(csv_labels) > size:
        candidates = [lv for lv in csv_labels if lv not in explicit.values()]
        assigned = _assign_nearest_levels(remaining, candidates)
        if assigned is not None:
            result.update(assigned)
            return result
        # 終了温度・終了時間が取れない等で推測できなければ、下の一般フォールバックへ。

    labels = _ROAST_LEVEL_LABELS.get(size)
    if labels is None:
        # 実データでは1〜3人のグループしか確認していないが、
        # 想定外のグループサイズが来ても壊れないようにフォールバックする。
        for m in remaining:
            result[m["id"]] = ""
        return result

    used_slots = {labels.index(lv) + 1 for lv in explicit.values() if lv in labels}
    known = used_slots | {m["no_num"] for m in remaining if m["no_num"] is not None}
    missing_slots = sorted(set(range(1, size + 1)) - known)
    slot_i = 0
    for m in sorted(remaining, key=lambda m: (m["no_num"] is None, m["no_num"] or 0)):
        if m["no_num"] is not None and 1 <= m["no_num"] <= size and m["no_num"] not in used_slots:
            idx = m["no_num"] - 1
        elif missing_slots:
            idx = missing_slots[slot_i] - 1
            slot_i += 1
        else:
            idx = None
        result[m["id"]] = labels[idx] if idx is not None else ""
    return result


def compute_roast_levels(profiles: list[dict]) -> dict[int, str]:
    """[{"id":.., "name":.., "roaster":..}, ...] を受け取り、id -> 焙煎度ラベル を返す。"""
    result: dict[int, str] = {}
    for (num, _roaster), members in _build_roast_groups(profiles).items():
        result.update(_label_group(members, num))
    return result


def compute_roast_level_siblings(profiles: list[dict]) -> dict[int, dict[str, int]]:
    """id -> {焙煎度ラベル: 同じ豆・同じ焙煎士の別プロファイルid} を返す。

    「豆の情報」パネルで、選択中とは違う焙煎度のテイスティングメモをクリックした際に、
    そのプロファイルへジャンプできるようにするために使う(自分自身も含む)。
    """
    siblings: dict[int, dict[str, int]] = {}
    for (num, _roaster), members in _build_roast_groups(profiles).items():
        labels = _label_group(members, num)
        by_label = {lv: mid for mid, lv in labels.items() if lv}
        for m in members:
            siblings[m["id"]] = by_label
    return siblings


def pick_roast_level_detail(info: dict, level_label: str) -> Optional[dict]:
    """beaninfoのroast_levels(CSV記載の焙煎レベル一覧)から、プロファイル側の
    焙煎度ラベル(浅煎り/中煎り/中深煎り/深煎り)に対応するテイスティングメモを1件選ぶ。

    _label_group()がCSVの実際のラベルをそのままプロファイルの焙煎度として採用する
    ため(人数と過不足なく一致する場合)、ここは完全一致でよい。人数が合わず一般形
    (浅煎り/中煎り/深煎り)にフォールバックしたプロファイルの場合、CSV側に無い
    ラベル(例:一般形の"深煎り"に対しCSVが"中深煎り"のみを持つ)は一致せずNoneになる
    が、対応関係が不確かな組み合わせで誤って結びつけるよりは安全側の挙動である。
    """
    levels = info.get("roast_levels") or []
    matches = [lv for lv in levels if lv["label"] == level_label]
    return matches[-1] if matches else None
