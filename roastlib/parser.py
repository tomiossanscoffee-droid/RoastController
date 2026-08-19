# ============================================================
# Roast Studio
# parser.py : Parser Library
# ============================================================
"""
profile テーブルの各カラムをパースするクラス群。

roastPoints / fanPoints / cooldownPoint は
"time1,value1,time2,value2,..." というカンマ区切りの交互配列で、
(経過時間[秒], 値) の制御点列を表す。実データで検証済み:

    roastPoints: 0.00,190.00,60.00,100.00,...
      -> (0秒,190℃) (60秒,100℃) ...

    cooldownPoint: 550.00,60.00
      -> (550秒,60℃) = 冷却が目標温度に到達する予定時刻
         （roastPoints最終点 = 焙煎終了 = 冷却開始、と実機動作で確認済み）
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

import pandas as pd


class BaseParser:
    @staticmethod
    def safe_str(value: Any, default: str = "") -> str:
        if value is None:
            return default
        try:
            return str(value).strip()
        except Exception:  # noqa: BLE001
            return default

    @staticmethod
    def safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value) if value is not None else default
        except Exception:  # noqa: BLE001
            return default


class PointParser(BaseParser):
    """profile テーブルの各種 *Points / *Point カラムをパースする。"""

    COLUMN_MAP = {
        "roast": "roastPoints",
        "fan": "fanPoints",
        "cooldown": "cooldownPoint",
        "haze": "hazePoint",
        "tracking": "trackingPoint",
        "roasting_tracking": "roastingTrackingPoint",
    }

    @staticmethod
    def parse_points(point_string: Any) -> Tuple[List[float], List[float]]:
        """"t1,v1,t2,v2,..." 形式の文字列を (times, values) に分解する。"""
        s = BaseParser.safe_str(point_string)
        if not s:
            return [], []

        values: List[float] = []
        for item in s.split(","):
            item = item.strip()
            if item:
                try:
                    values.append(float(item))
                except ValueError:
                    continue

        times = values[0::2]
        vals = values[1::2]
        min_len = min(len(times), len(vals))
        return times[:min_len], vals[:min_len]

    @classmethod
    def _get_curve(cls, profile: Any, key: str) -> Tuple[List[float], List[float]]:
        col = cls.COLUMN_MAP.get(key)
        if col is None:
            return [], []
        if isinstance(profile, pd.Series) and col in profile:
            return cls.parse_points(profile[col])
        if isinstance(profile, dict) and col in profile:
            return cls.parse_points(profile[col])
        if hasattr(profile, col):
            return cls.parse_points(getattr(profile, col))
        return [], []

    @classmethod
    def roast(cls, profile):
        return cls._get_curve(profile, "roast")

    @classmethod
    def fan(cls, profile):
        return cls._get_curve(profile, "fan")

    @classmethod
    def cooldown(cls, profile):
        return cls._get_curve(profile, "cooldown")

    @classmethod
    def haze(cls, profile):
        return cls._get_curve(profile, "haze")

    @classmethod
    def tracking(cls, profile):
        return cls._get_curve(profile, "tracking")

    @classmethod
    def roasting_tracking(cls, profile):
        return cls._get_curve(profile, "roasting_tracking")


class NameParser:
    """profile.name (例: "3025_インドネシア_セレベス No.1 001") をパースする。

    焙煎士名が含まれないプロファイルは、全て後藤直紀さんが作成したものであるため
    DEFAULT_ROASTER を "後藤直紀さん" としている（2026-07 確認済み・仕様）。
    """

    DEFAULT_ROASTER = "後藤直紀さん"
    KNOWN_ROASTERS = ["後藤栄二郎さん", "河合さん", "後藤直紀さん"]

    # 実機DB(nhm.sqlite, 174件)を実際に調査した結果、profile.nameの区切り文字が
    # "_"ではなく全角スペース・記号・区切りなし等でバラバラなものが混在しており、
    # 単純に"_"分割してparts[0]を国名とすると、区切りが崩れているプロファイルで
    # "●3024"や"・3041 エチオピア ゲイシャ"のような文字列がそのまま国名として
    # 表示されてしまっていた。実データに実在する国名を既知リストとして持ち、
    # 区切り文字によらず文字列全体から国名を検出することでこれを防ぐ。
    KNOWN_COUNTRIES = [
        "ブラジル", "グアテマラ", "エチオピア", "コロンビア", "インドネシア",
        "タンザニア", "ケニア", "メキシコ", "ルワンダ", "エクアドル",
        "ホンジュラス", "コスタリカ", "ニカラグア", "パナマ", "イエメン",
    ]

    # 農園名・国名の略称等、上記KNOWN_COUNTRIESの表記そのままでは国名として
    # 表示したくないプロファイル用の手動マッピング。
    # - "Ada farm" は沖縄県の農園名のため「日本」を割り当てる(2026-07 確認済み)。
    # - "PNG" はパプアニューギニアの略称のため、正式名称に統一する。
    MANUAL_COUNTRY_OVERRIDES = {
        "Ada farm": "日本",
        "PNG": "パプアニューギニア",
    }

    @staticmethod
    def parse(name: str) -> Dict[str, str]:
        result = {
            "bean_code": "", "country": "", "bean": "",
            "roaster": NameParser.DEFAULT_ROASTER,
            "profile_no": "", "revision": "",
        }
        if not name:
            return result

        m = re.match(r"^(\d{4})_(.+)", name)
        text = name
        if m:
            result["bean_code"] = m.group(1)
            text = m.group(2)

        no_match = re.search(r"(No\.\d+)", text)
        if no_match:
            result["profile_no"] = no_match.group(1)
            left = text[:no_match.start()].strip()
            right = text[no_match.end():].strip()
        else:
            left, right = text, ""

        result["revision"] = right

        for roaster in NameParser.KNOWN_ROASTERS:
            if roaster in left:
                result["roaster"] = roaster
                left = left.replace(roaster, "").strip("_ ")
                break

        # "_"だけでなく、区切りが崩れて空白(全角含む)になっているプロファイルにも
        # 対応するため、両方をまとめて区切り文字として分割する。
        parts = [p.strip() for p in re.split(r"[_\s　]+", left) if p.strip()]

        # まず既知の国名を文字列全体から検出する(区切り文字の崩れに強い)。
        # 見つからない場合のみ、従来通り"_"区切りの先頭要素にフォールバックする
        # ("_"で区切られていない=国名を含まない可能性が高いプロファイルまで
        # 無理に国名扱いしてしまわないため)。
        country = next((c for c in NameParser.KNOWN_COUNTRIES if c in left), "")
        if not country:
            for keyword, mapped in NameParser.MANUAL_COUNTRY_OVERRIDES.items():
                if keyword in left:
                    country = mapped
                    break

        if country:
            # 国名として使った要素と、番号・記号だけの要素("●3024"等)は
            # 豆名に含めない(位置(parts[0])ではなく内容で除外することで、
            # 記号付きの名前(●3024_コスタリカ_マイクロロット等)でも
            # 豆名に余計な文字列が混入しないようにする)。
            bean_parts = [
                p for p in parts
                if p != country and not re.fullmatch(r"[^\w]*\d+[^\w]*", p)
            ]
        elif len(parts) > 1:
            country = parts[0]
            bean_parts = parts[1:]
        else:
            bean_parts = []
        result["country"] = country

        if bean_parts:
            result["bean"] = "_".join(bean_parts)

        return result
