# ============================================================
# Roast Studio
# ble/gatt.py : GATT構造の定義（LightBlueでの実機調査結果）
# ============================================================
"""
Panasonic THE ROAST Basic の BLE GATT構造。

2026-07、LightBlue(Mac/iOS)を使って実機に接続し確認した内容。
値は Device Information Service (標準サービス 0x180A) から読み取った実測値。

Model Number String  : "Smart Roaster AE-NR01"
Manufacturer Name    : "Panasonic Corporation"
Firmware Revision    : "BAS02.00"
Hardware Revision    : "PA101.01"

推定される役割は未検証（Notify内容とプロファイル送信をキャプチャして裏付けが必要）。
今後、実際のパケットキャプチャで役割が確定次第、ROLEやコメントを更新すること。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CharRole(str, Enum):
    UNKNOWN = "unknown"
    NOTIFY_STATUS = "notify_status"       # 温度/進捗などの状態通知（推定）
    WRITE_COMMAND = "write_command"       # 制御コマンド送信（推定）
    READ_ONLY_FIXED = "read_only_fixed"   # 固定値read専用（推定）


@dataclass(frozen=True)
class CharacteristicInfo:
    uuid: str
    properties: tuple  # ("read",) / ("notify", "read") / ("read", "write", "write_without_response") など
    role: CharRole = CharRole.UNKNOWN
    note: str = ""


# ------------------------------------------------------------
# 標準 Device Information Service (0x180A)
# ------------------------------------------------------------
DEVICE_INFO_SERVICE_UUID = "180A"

DEVICE_INFO_CHARACTERISTICS = {
    "manufacturer_name": CharacteristicInfo(
        uuid="2A29", properties=("read",),
        note='実測値: "Panasonic Corporation"',
    ),
    "model_number": CharacteristicInfo(
        uuid="2A24", properties=("read",),
        note='実測値: "Smart Roaster AE-NR01"',
    ),
    "firmware_revision": CharacteristicInfo(
        uuid="2A26", properties=("read",),
        note='実測値: "BAS02.00"',
    ),
    "hardware_revision": CharacteristicInfo(
        uuid="2A27", properties=("read",),
        note='実測値: "PA101.01"',
    ),
}

# ------------------------------------------------------------
# カスタムService① : メインサービス（Advertisement DataのService UUIDと一致）
# ------------------------------------------------------------
MAIN_SERVICE_UUID = "D0927D90-A443-4582-B431-962ABE7E4BE1"

MAIN_SERVICE_CHARACTERISTICS = {
    "fixed_value_1": CharacteristicInfo(
        uuid="9154B564-DEB1-4A5F-BD90-872C118BEF78",
        properties=("read",),
        role=CharRole.READ_ONLY_FIXED,
        note="固定値と思われる。役割未確認。",
    ),
    "status_notify": CharacteristicInfo(
        uuid="39350D46-FEE7-436B-8AAE-8B92855F5BA2",
        properties=("notify", "read"),
        role=CharRole.NOTIFY_STATUS,
        note="状態通知（温度・進捗など）と推定。Notify購読して裏付け検証が必要。",
    ),
    "command_write": CharacteristicInfo(
        uuid="3FB74DB9-6A2E-4E59-99A2-08CB2B8BD4A7",
        properties=("read", "write", "write_without_response"),
        role=CharRole.WRITE_COMMAND,
        note="コマンド送信（プロファイル送信・焙煎開始等）と推定。",
    ),
}

# ------------------------------------------------------------
# カスタムService② : サブサービス（役割はService①と対になっている可能性）
# ------------------------------------------------------------
SUB_SERVICE_UUID = "F35B1B5F-0261-45E0-8BCB-089D225014A3"

SUB_SERVICE_CHARACTERISTICS = {
    "fixed_value_2": CharacteristicInfo(
        uuid="9DA45B66-6A12-46D2-A1B3-E160BBCFB919",
        properties=("read",),
        role=CharRole.READ_ONLY_FIXED,
        note="固定値と思われる。役割未確認。",
    ),
    "status_notify_2": CharacteristicInfo(
        uuid="B422AF47-E747-4B17-8373-D828BA10F48E",
        properties=("notify", "read"),
        role=CharRole.NOTIFY_STATUS,
        note="状態通知（別系統）と推定。",
    ),
    "command_write_2": CharacteristicInfo(
        uuid="B4358BBD-D8D4-4EFE-B219-40439E7C470E",
        properties=("write", "write_without_response"),
        role=CharRole.WRITE_COMMAND,
        note="コマンド送信（別系統、プロファイルデータ転送用の可能性）と推定。",
    ),
}


def all_characteristics():
    """全Characteristicをフラットな辞書として返す（UUID -> CharacteristicInfo）。"""
    merged = {}
    for group in (DEVICE_INFO_CHARACTERISTICS, MAIN_SERVICE_CHARACTERISTICS, SUB_SERVICE_CHARACTERISTICS):
        for info in group.values():
            merged[info.uuid.upper()] = info
    return merged


def describe(uuid: str) -> str:
    """UUIDから人間可読な説明を返す(デバッグ・ログ表示用)。"""
    info = all_characteristics().get(uuid.upper())
    if info is None:
        return f"未知のUUID: {uuid}"
    return f"{uuid} [{info.role.value}] {info.note}"
