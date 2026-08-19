#!/usr/bin/env python3
# ============================================================
# Roast Studio
# scripts/ble_write_reactive_test.py
# ------------------------------------------------------------
# ble_write_and_heartbeat_test.py の改良版。
#
# 実機キャプチャを詳細に見直した結果、プロトコルは「一定間隔で
# 送り続けるハートビート」ではなく、「機械からのNotify(問いかけ)
# を受け取ってから、都度正しく応答する」リクエスト・レスポンス型
# であることが判明した。
#
# 具体的な流れ(実機キャプチャより):
#   1. プロファイル転送(11回の書き込み)
#   2. 機械が [ヘッダ+"e0 20"] → [01+UUID文字列+0x1b] の順でNotify
#      → これに対して [ヘッダ+"60 00"] → "4d" で応答する(1回だけ)
#   3. 以降、機械が [ヘッダ+"e4 21"] → [連番+センサー値] の順でNotify
#      → これに対して [ヘッダ+"64 00"] → "51" で応答する(繰り返し)
#
# 実行方法:
#   python3 ble_write_reactive_test.py
#
# 実行したら、"応答ループ開始" と表示されている間に、
# 焙煎機本体のスイッチを押してみてください。
# Ctrl+C で終了します。
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

MAIN_SERVICE_UUID = "d0927d90-a443-4582-b431-962abe7e4be1"
CHAR_STATUS_NOTIFY = "39350d46-fee7-436b-8aae-8b92855f5ba2"
CHAR_COMMAND_WRITE = "3fb74db9-6a2e-4e59-99a2-08cb2b8bd4a7"

# プロファイル転送部分(id=22 実機キャプチャより)
CAPTURED_WRITES = [
    bytes.fromhex("120040d8fd6fc34299e2e43ce5e6d38177204430"),
    bytes.fromhex("950040d8fd6fc34299e2e43ce5e6d38177202000"),
    bytes.fromhex("3631303332313033303331313432303108303030"),
    bytes.fromhex("3030373030313030303035313030363030303831"),
    bytes.fromhex("3030323130303931303038313030303230303432"),
    bytes.fromhex("3030313230303333303032323035303430353232"),
    bytes.fromhex("3006303030303037303030363030353730303038"),
    bytes.fromhex("3130353630303030333035353030353034303035"),
    bytes.fromhex("3030353235303536303072"),
]

PROFILE_UUID_ASCII = b"6103210303114201"

# 応答パターン(実機キャプチャより)
ACK_UUID_ECHO_HEADER = bytes.fromhex("130040d8fd6fc34299e2e43ce5e6d38177206000")
ACK_UUID_ECHO_TAIL = bytes.fromhex("4d")

ACK_HEARTBEAT_HEADER = bytes.fromhex("130040d8fd6fc34299e2e43ce5e6d38177206400")
ACK_HEARTBEAT_TAIL = bytes.fromhex("51")

WRITE_DELAY = 0.15


def now_str() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def hexdump(data: bytes) -> str:
    return data.hex()


def ascii_repr(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


async def find_roaster(timeout: float = 10.0):
    print(f"[{now_str()}] BLEデバイスをスキャン中(最大{timeout:.0f}秒)...")
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for device, adv in devices.values():
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        name = device.name or ""
        if MAIN_SERVICE_UUID in uuids or "ROAST" in name.upper() or "NR01" in name.upper():
            print(f"[{now_str()}] 発見: {device.name} ({device.address})")
            return device
    print(f"[{now_str()}] 見つかりませんでした。")
    return None


class ReactiveResponder:
    """受信したNotifyの内容を見て、適切な応答Writeを行うクラス。"""

    def __init__(self, client: BleakClient):
        self.client = client
        self.notify_count = 0
        self.acked_uuid_echo = False
        self.heartbeat_ack_count = 0
        self._lock = asyncio.Lock()

    def handler(self, sender, data: bytes):
        self.notify_count += 1
        print(f"[{now_str()}] Notify#{self.notify_count:04d} len={len(data):2d} "
              f"hex={hexdump(data)}  ascii={ascii_repr(data)}")

        # UUIDエコー確認への応答(1回だけ)
        if not self.acked_uuid_echo and PROFILE_UUID_ASCII in data:
            asyncio.create_task(self._ack_uuid_echo())
            return

        # 通常のセンサー値通知への応答(連番+値のパターン: 先頭2バイトが連番)
        if self.acked_uuid_echo and len(data) >= 8:
            asyncio.create_task(self._ack_heartbeat())

    async def _ack_uuid_echo(self):
        async with self._lock:
            if self.acked_uuid_echo:
                return
            self.acked_uuid_echo = True
        try:
            await self.client.write_gatt_char(CHAR_COMMAND_WRITE, ACK_UUID_ECHO_HEADER, response=True)
            await asyncio.sleep(0.05)
            await self.client.write_gatt_char(CHAR_COMMAND_WRITE, ACK_UUID_ECHO_TAIL, response=True)
            print(f"[{now_str()}] >>> UUID確認応答を送信しました <<<")
        except Exception as e:  # noqa: BLE001
            print(f"[{now_str()}] UUID確認応答の送信に失敗: {e}")

    async def _ack_heartbeat(self):
        async with self._lock:
            self.heartbeat_ack_count += 1
            n = self.heartbeat_ack_count
        try:
            await self.client.write_gatt_char(CHAR_COMMAND_WRITE, ACK_HEARTBEAT_HEADER, response=True)
            await asyncio.sleep(0.05)
            await self.client.write_gatt_char(CHAR_COMMAND_WRITE, ACK_HEARTBEAT_TAIL, response=True)
            print(f"[{now_str()}] >>> ハートビート応答#{n:04d}を送信しました <<<")
        except Exception as e:  # noqa: BLE001
            print(f"[{now_str()}] ハートビート応答の送信に失敗: {e}")


async def main():
    device = await find_roaster()
    if device is None:
        return

    print(f"\n[{now_str()}] 接続中... ({device.address})")
    async with BleakClient(device) as client:
        print(f"[{now_str()}] 接続成功: is_connected={client.is_connected}")

        responder = ReactiveResponder(client)
        await client.start_notify(CHAR_STATUS_NOTIFY, responder.handler)
        print(f"[{now_str()}] status_notify を購読開始")

        print(f"\n[{now_str()}] === プロファイルを送信 ===")
        for i, payload in enumerate(CAPTURED_WRITES):
            try:
                await client.write_gatt_char(CHAR_COMMAND_WRITE, payload, response=True)
                print(f"[{now_str()}] 書き込み#{i+1:02d}/{len(CAPTURED_WRITES)} -> OK")
            except Exception as e:  # noqa: BLE001
                print(f"[{now_str()}] 書き込み#{i+1:02d}/{len(CAPTURED_WRITES)} -> エラー: {e}")
            await asyncio.sleep(WRITE_DELAY)

        print(f"\n[{now_str()}] === 応答ループ開始(機械からのNotifyに対して自動応答します) ===")
        print(f"[{now_str()}] >>> この間に、焙煎機本体のスイッチを押してみてください <<<")
        print(f"[{now_str()}] (Ctrl+C で終了)")

        try:
            while True:
                await asyncio.sleep(1.0)
        except KeyboardInterrupt:
            print(f"\n[{now_str()}] 中断されました。切断します。")

        await client.stop_notify(CHAR_STATUS_NOTIFY)

    print(f"[{now_str()}] 切断しました。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n中断されました。")
