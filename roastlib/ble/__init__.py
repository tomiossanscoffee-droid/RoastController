"""
roastlib.ble
============
Panasonic THE ROAST Basic のBLE通信解析用パッケージ。

現状(2026-07)は GATT構造の定義(gatt.py)のみ。
パケットキャプチャの解析結果が集まり次第、以下を追加予定:
  - decoder.py : キャプチャした生バイト列 -> RoastProfileへのデコード
  - encoder.py : RoastProfile -> 送信バイト列へのエンコード
  - capture.py : PacketLogger(.pklg)の読み込み・ATT Writeパケット抽出補助
"""

from .gatt import (
    CharRole,
    CharacteristicInfo,
    DEVICE_INFO_SERVICE_UUID,
    DEVICE_INFO_CHARACTERISTICS,
    MAIN_SERVICE_UUID,
    MAIN_SERVICE_CHARACTERISTICS,
    SUB_SERVICE_UUID,
    SUB_SERVICE_CHARACTERISTICS,
    all_characteristics,
    describe,
)
from .codec import encode_profile_payload, decode_profile_payload
from .session import RoasterSession, TelemetrySample, build_write_sequence, profile_from_points

__all__ = [
    "CharRole",
    "CharacteristicInfo",
    "DEVICE_INFO_SERVICE_UUID",
    "DEVICE_INFO_CHARACTERISTICS",
    "MAIN_SERVICE_UUID",
    "MAIN_SERVICE_CHARACTERISTICS",
    "SUB_SERVICE_UUID",
    "SUB_SERVICE_CHARACTERISTICS",
    "all_characteristics",
    "describe",
    "encode_profile_payload",
    "decode_profile_payload",
    "RoasterSession",
    "TelemetrySample",
    "build_write_sequence",
    "profile_from_points",
]
