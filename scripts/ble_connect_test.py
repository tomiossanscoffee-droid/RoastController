#!/usr/bin/env python3
# ============================================================
# Roast Studio
# scripts/ble_connect_test.py
# ------------------------------------------------------------
# Panasonic THE ROAST Basic (Smart Roaster AE-NR01) に
# iPhone純正アプリを使わず、Pythonから直接BLE接続するテストスクリプト。
#
# ★このスクリプトは Read(読み取り) と Notify(通知の購読) だけを行い、
#   Write(書き込み)は一切行いません。
#   → ヒーターやモーターが動作する可能性はゼロです。安全に実行できます。
#
# 実行前の準備:
#   pip install bleak
#
# 実行方法:
#   python3 ble_connect_test.py
#
# 動作の流れ:
#   1. 周辺のBLE機器をスキャンし、THE ROASTを見つける
#   2. 接続し、可能であればペアリング(Bonding)を試みる
#      (macOSのCoreBluetoothは明示的なpair()に対応していないため、
#       その場合は「ペアリング処理をスキップし、そのまま接続を継続」する)
#   3. Device Information (製造元名・型番・FW/HWバージョン) と、
#      fixed_value_1 を Read して表示
#   4. status_notify を購読(Subscribe)し、届いた通知を
#      タイムスタンプ付きでHex/ASCII表示し続ける
#      (Ctrl+C で終了)
#
# この間、iPhone純正アプリ側で焙煎機を操作すると、
# Python側にも同じ通知が届くことを確認できます。
# ============================================================

import asyncio
import sys
from datetime import datetime

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("bleak がインストールされていません。以下を実行してください:")
    print("    pip install bleak")
    sys.exit(1)

# ------------------------------------------------------------
# GATT UUID定義 (roastlib/ble/gatt.py と同じ内容)
# ------------------------------------------------------------
MAIN_SERVICE_UUID = "d0927d90-a443-4582-b431-962abe7e4be1"

CHAR_FIXED_VALUE_1 = "9154b564-deb1-4a5f-bd90-872c118bef78"   # Read
CHAR_STATUS_NOTIFY = "39350d46-fee7-436b-8aae-8b92855f5ba2"   # Notify, Read
CHAR_COMMAND_WRITE = "3fb74db9-6a2e-4e59-99a2-08cb2b8bd4a7"   # Read, Write (今回は未使用)

# 標準 Device Information Service (0x180A) のCharacteristic
CHAR_MANUFACTURER_NAME = "00002a29-0000-1000-8000-00805f9b34fb"
CHAR_MODEL_NUMBER = "00002a24-0000-1000-8000-00805f9b34fb"
CHAR_FIRMWARE_REV = "00002a26-0000-1000-8000-00805f9b34fb"
CHAR_HARDWARE_REV = "00002a27-0000-1000-8000-00805f9b34fb"

# 通知を受信し続ける時間(秒)。Ctrl+Cでも途中終了できます。
NOTIFY_DURATION_SEC = 120


def now_str() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def hexdump(data: bytes) -> str:
    return data.hex()


def ascii_repr(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


async def find_roaster(timeout: float = 10.0):
    """MAIN_SERVICE_UUIDを広告しているデバイスをスキャンして探す。"""
    print(f"[{now_str()}] BLEデバイスをスキャン中(最大{timeout:.0f}秒)...")
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)

    for device, adv in devices.values():
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        name = device.name or ""
        if MAIN_SERVICE_UUID in uuids or "ROAST" in name.upper() or "NR01" in name.upper():
            print(f"[{now_str()}] 発見: {device.name} ({device.address})")
            return device

    print(f"[{now_str()}] 見つかりませんでした。")
    print("  - 焙煎機がペアリングモード(広告中)になっているか確認してください")
    print("  - iPhone純正アプリが接続を掴んでいないか確認してください(アプリを終了してから再試行)")
    return None


async def try_pair(client: BleakClient):
    """可能であればペアリング(Bonding)を試みる。

    macOS(CoreBluetooth)のbleakバックエンドはpair()に対応していないため、
    その場合はNotImplementedErrorを捕捉してスキップする。
    """
    try:
        paired = await client.pair()
        print(f"[{now_str()}] ペアリング結果: {paired}")
    except NotImplementedError:
        print(f"[{now_str()}] このOS/バックエンドは明示的なpair()に対応していません。"
              f"ペアリング処理をスキップし、そのまま接続を継続します。")
    except Exception as e:  # noqa: BLE001
        print(f"[{now_str()}] ペアリング試行中にエラー(無視して続行): {e}")


async def read_device_info(client: BleakClient):
    print(f"\n[{now_str()}] === Device Information を読み取り ===")
    targets = [
        ("Manufacturer Name", CHAR_MANUFACTURER_NAME),
        ("Model Number", CHAR_MODEL_NUMBER),
        ("Firmware Revision", CHAR_FIRMWARE_REV),
        ("Hardware Revision", CHAR_HARDWARE_REV),
        ("fixed_value_1 (カスタム)", CHAR_FIXED_VALUE_1),
    ]
    for label, uuid in targets:
        try:
            value = await client.read_gatt_char(uuid)
            print(f"  {label:24s}: hex={hexdump(value):20s} ascii={ascii_repr(value)}")
        except Exception as e:  # noqa: BLE001
            print(f"  {label:24s}: 読み取り失敗 ({e})")


def make_notify_handler():
    count = 0

    def handler(sender, data: bytes):
        nonlocal count
        count += 1
        print(f"[{now_str()}] Notify#{count:04d} len={len(data):2d} "
              f"hex={hexdump(data)}  ascii={ascii_repr(data)}")

    return handler


async def subscribe_and_listen(client: BleakClient, duration: float):
    print(f"\n[{now_str()}] === status_notify を購読開始 (最大{duration:.0f}秒、Ctrl+Cで終了) ===")
    handler = make_notify_handler()
    await client.start_notify(CHAR_STATUS_NOTIFY, handler)

    try:
        await asyncio.sleep(duration)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            await client.stop_notify(CHAR_STATUS_NOTIFY)
        except Exception:  # noqa: BLE001
            pass
    print(f"[{now_str()}] 通知の購読を終了しました。")


async def main():
    device = await find_roaster()
    if device is None:
        return

    print(f"\n[{now_str()}] 接続中... ({device.address})")
    async with BleakClient(device) as client:
        print(f"[{now_str()}] 接続成功: is_connected={client.is_connected}")

        await try_pair(client)
        await read_device_info(client)
        await subscribe_and_listen(client, NOTIFY_DURATION_SEC)

    print(f"[{now_str()}] 切断しました。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n中断されました。")
