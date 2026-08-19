# ============================================================
# Roast Studio
# tests/test_ble_codec.py
# ============================================================
"""
実機BLEキャプチャ(2026-07)から得られた生バイト列を使った回帰テスト。
2つの異なるプロファイル(roastPointsのペア数が異なる)で検証している。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from roastlib.ble.codec import decode_profile_payload, encode_profile_payload  # noqa: E402
from roastlib.models import RoastCurve, RoastProfile  # noqa: E402


def _profile(roast_n: int, fan_n: int) -> RoastProfile:
    roast_x = list(range(0, roast_n * 10, 10))
    roast_y = [100 + i for i in range(roast_n)]
    fan_x = list(range(0, fan_n * 10, 10))
    fan_y = [50 + i for i in range(fan_n)]
    return RoastProfile(
        id=-1, name="test",
        roast=RoastCurve(roast_x, roast_y),
        fan=RoastCurve(fan_x, fan_y),
        cooldown=RoastCurve([roast_n * 10 + 100], [60]),
        raw={"UUID": "1234567890123456"},
    )


def test_encode_accepts_up_to_official_point_limits():
    """2026-07訂正: パナソニック公式サイトの仕様(温度は最大20ポイント・風量は
    最大10ポイント)に基づき、この境界ちょうどのポイント数は拒否されないこと。
    以前はDB内で確認できた実例数(11/9ペア)を上限にしてしまっており、公式仕様
    どおりのポイント数の有効なプロファイルが誤って拒否される不具合があった。"""
    encode_profile_payload(_profile(roast_n=20, fan_n=10))  # 例外が出ないこと


def test_encode_rejects_beyond_official_point_limits():
    """20ポイント(温度)・10ポイント(風量)を1つでも超えたら、それぞれ拒否すること。"""
    with pytest.raises(ValueError):
        encode_profile_payload(_profile(roast_n=21, fan_n=5))
    with pytest.raises(ValueError):
        encode_profile_payload(_profile(roast_n=5, fan_n=11))


def test_decode_profile_49_ethiopia():
    """id=49 '1002_エチオピア_イルガチャフィ No.1 001' (roastPoints 7ペア)"""
    frags = [
        "3130303231393037303331313430303107303030",
        "3030393130303630303539303030323130353431",
        "3030383130303831303130333030313230303633",
        "3030323230303734303133323005303030303035",
        "3030313030303038303035303030353730303037",
        "343036353030303735303136303053",
    ]
    raw = b"".join(bytes.fromhex(f) for f in frags)
    result = decode_profile_payload(raw)

    assert result["uuid"] == "1002190703114001"
    assert result["roast"] == [
        (0, 190), (60, 95), (120, 145), (180, 180), (301, 210), (360, 220), (470, 231)
    ]
    assert result["fan"] == [(0, 50), (1, 80), (5, 75), (470, 56)]
    assert result["cooldown"] == (570, 61)


def test_decode_profile_29_yemen():
    """id=29 '3033_イエメン_イスマイリ No.1' (roastPoints 6ペア)"""
    raw = (
        b'3033190703114901\x06000008100600011002105410081028100030012092405220'
        b'\x050000050010000800600077009240750092507500\xd8'
    )
    result = decode_profile_payload(raw)

    assert result["uuid"] == "3033190703114901"
    assert result["roast"] == [
        (0, 180), (60, 110), (120, 145), (180, 182), (300, 210), (429, 225)
    ]
    assert result["fan"] == [(0, 50), (1, 80), (6, 77), (429, 57)]
    assert result["cooldown"] == (529, 57)
