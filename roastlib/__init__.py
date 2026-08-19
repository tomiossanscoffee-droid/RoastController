"""
Roast Studio - roastlib
==============================
Copyright (c) 2026 Ossan's Coffee / Tomi
Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
Panasonic THE ROAST (TheRoastBasic) の焙煎プロファイルSQLiteを解析するための
コアロジック集。Google Colabのノートブックからも、他のPythonスクリプトからも
`from roastlib import ...` の形でそのままimportして使えるように分割してある。

モジュール構成
--------------
db.py       : DatabaseManager - SQLiteの読込・テーブル保持
parser.py   : PointParser / NameParser - roastPoints等の文字列パースとプロファイル名パース
models.py   : RoastCurve / RoastProfile / ModelFactory - データモデル
analyzer.py : Analyzer / RoastAnalysis - 統計・診断ロジック
compare.py  : CompareEngine - 複数プロファイルの比較グラフ・サマリー
"""

from .db import DatabaseManager
from .parser import BaseParser, PointParser, NameParser
from .models import RoastCurve, RoastProfile, ModelFactory
from .fonts import setup_japanese_font
from .analyzer import (
    TemperatureStats,
    FanStats,
    ProfileSummary,
    TimelineEvent,
    RoastAnalysis,
    Analyzer,
)
from .compare import CompareEngine
from . import ble

__version__ = "1.0.0"

__all__ = [
    "DatabaseManager",
    "BaseParser",
    "PointParser",
    "NameParser",
    "RoastCurve",
    "RoastProfile",
    "ModelFactory",
    "setup_japanese_font",
    "TemperatureStats",
    "FanStats",
    "ProfileSummary",
    "TimelineEvent",
    "RoastAnalysis",
    "Analyzer",
    "CompareEngine",
    "ble",
]
