# ============================================================
# Roast Studio
# tests/test_parser.py
# ============================================================
"""
nhm.sqlite の実データから採取した文字列を使った回帰テスト。
実行方法:
    pip install pytest
    pytest tests/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roastlib.parser import PointParser, NameParser  # noqa: E402


def test_parse_points_roast():
    # profile.id=1 "3025_インドネシア_セレベス No.1 001" の実データ
    s = "0.00,190.00,60.00,100.00,120.00,155.00,180.00,185.00,300.00,210.00,450.00,230.00"
    times, temps = PointParser.parse_points(s)
    assert times == [0.0, 60.0, 120.0, 180.0, 300.0, 450.0]
    assert temps == [190.0, 100.0, 155.0, 185.0, 210.0, 230.0]


def test_parse_points_fan():
    s = "0.00,50.00,1.00,80.00,6.00,76.00,450.00,56.00"
    times, values = PointParser.parse_points(s)
    assert times == [0.0, 1.0, 6.0, 450.0]
    assert values == [50.0, 80.0, 76.0, 56.0]


def test_parse_points_cooldown():
    s = "550.00,60.00"
    times, values = PointParser.parse_points(s)
    assert times == [550.0]
    assert values == [60.0]


def test_parse_points_empty():
    times, values = PointParser.parse_points("")
    assert times == []
    assert values == []


def test_parse_points_odd_length_is_truncated():
    # 奇数個の値が来た場合、最後の値は無視されペアの整合性を保つ
    times, values = PointParser.parse_points("0,100,60")
    assert times == [0.0]
    assert values == [100.0]


def test_name_parser_with_roaster():
    result = NameParser.parse("6056_タンザニア_キゴマ_後藤栄二郎さん No.1 001")
    assert result["bean_code"] == "6056"
    assert result["country"] == "タンザニア"
    assert result["bean"] == "キゴマ"
    assert result["roaster"] == "後藤栄二郎さん"
    assert result["profile_no"] == "No.1"


def test_name_parser_default_roaster():
    # 焙煎士名が明記されていないプロファイルは全て後藤直紀さんの作成
    result = NameParser.parse("3025_インドネシア_セレベス No.1 001")
    assert result["roaster"] == "後藤直紀さん"
    assert result["country"] == "インドネシア"
    assert result["bean"] == "セレベス"
