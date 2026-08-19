# ============================================================
# Roast Studio
# ble/codec.py : プロファイル転送データのエンコード/デコード
# ============================================================
"""
command_write (0x3FB74DB9-...) に書き込まれる「プロファイル転送データ」の
エンコード方式。当初はDB上のroastPoints/fanPoints/cooldownPointとの
1バイト単位の突き合わせ(PacketLogger、2プロファイル)のみで検証していたが、
2026-07以降、実機(THE ROAST EXPERT)への実送信を多数回行い、プリセット・
GUI編集・ABC自動生成など様々な構成のプロファイルで正常に焙煎完了まで
到達することを確認済み(roastlib/ble/session.pyのbuild_write_sequence()
経由)。

## 確認済みのワイヤーフォーマット

    [UUID: 16文字のASCII数字]  (= profile.UUIDカラムの値をそのままASCII送信)
    [1バイト: roastPointsのペア数]
    [roastPoints全値]           各値を4桁ゼロ埋め→桁を逆順にした文字列を連結
    [1バイト: fanPointsのペア数+1]  (cooldownPointの分を含む、実機検証済み)
    [fanPoints全値]             同上
    [cooldownPoint 2値]         同上（区切りマーカーなし、常に1ペア固定のため）
    [終端1バイト]                既知プロファイルはKNOWN_TERMINATORSの実測値、
                                 未知のプロファイルはチェックサムベースの
                                 推定式(_guess_terminator、session.py参照)で
                                 実機に正しく受理されることを確認済み。

「値」は roastPoints/fanPoints は (time, value) の順で列挙(時間,値,時間,値,...)。
cooldownPointは常に1ペア(2値)固定。

## ポイント数の上限

パナソニック公式サイトの仕様に基づき、roastPointsは最大20ポイント(1℃単位・
255℃まで)、fanPointsは最大10ポイント(1%単位・50〜100%)。以前はDB内で
確認できた実例数(11/9ペア)を上限にしていたが、これは仕様上の上限より
厳しく、公式仕様どおりのポイント数の有効なプロファイルが誤って拒否される
不具合があったため訂正した(2026-07)。

## 未解明のまま残っている点
- command_writeへの最初の2回の書き込み(header_1/header_2、session.py参照)の
  先頭・末尾バイトの意味は、値そのものは式で再現できているが、本来何を
  表すフィールドなのか(コマンド種別?)は未確定。
- 終端1バイトの計算式(_guess_terminator)が、実機側の本来のチェックサム
  アルゴリズムと一致しているのか、たまたま実測データにフィットした近似式に
  過ぎないのかは未確定。
"""

from __future__ import annotations

from typing import List, Tuple

from ..models import RoastProfile

SEP_ROAST_START = 0x07
SEP_FAN_START = 0x05
TERMINATOR = b"S"


def _encode_value(v: float) -> str:
    """1つの数値を4桁ゼロ埋け→逆順文字列に変換する。"""
    iv = int(round(v))
    s = f"{iv:04d}"
    if len(s) != 4:
        raise ValueError(f"4桁を超える値は現在のエンコード方式では扱えません: {v}")
    return s[::-1]


def _decode_value(chunk: str) -> int:
    """4桁の逆順文字列を数値に戻す。"""
    if len(chunk) != 4:
        raise ValueError(f"4桁ではないチャンク: {chunk!r}")
    return int(chunk[::-1])


def encode_profile_payload(profile: RoastProfile) -> bytes:
    """RoastProfileから、command_writeに送信するペイロード(生バイト列)を組み立てる。

    未検証: 終端バイトの意味が不明なため、ここでは便宜上 "S" を使う。
    実機への書き込みはまだ行っていない。
    """
    uuid_ascii = (profile.raw.get("UUID") or "").strip()
    if len(uuid_ascii) != 16 or not uuid_ascii.isdigit():
        raise ValueError(f"profile.UUIDが16桁の数字ではありません: {uuid_ascii!r}")

    roast_pairs = list(zip(profile.roast.x, profile.roast.y))
    if len(roast_pairs) > 20:
        # 2026-07訂正: パナソニック公式サイトの仕様(温度は最大20ポイントまで追加可能・
        # 1℃単位/255℃まで)に基づく上限。以前はDB内の実例(最大11ペア)を上限として
        # いたが、これは「これまで確認できた最大値」であって仕様上の上限ではなかった
        # (実際、この制限のせいで有効なプロファイルが誤って拒否される不具合があった)。
        raise ValueError("roastPointsは20ポイント(パナソニック公式仕様)までです")

    fan_pairs = list(zip(profile.fan.x, profile.fan.y))
    fan_marker = len(fan_pairs) + 1  # 実機検証済み: fanPointsのペア数+1(cooldownPointの分を含む)
    if len(fan_pairs) > 10:
        # 2026-07訂正: パナソニック公式サイトの仕様(風量は最大10ポイント・1%単位/
        # 50%〜100%)に基づく上限。以前はDB内の実例(最大9ペア)を上限としていたが、
        # これも同様に「これまで確認できた最大値」に過ぎず、10ポイントちょうどの
        # 有効なプロファイルが誤って拒否される不具合があった。
        raise ValueError("fanPointsは10ポイント(パナソニック公式仕様)までです")

    parts = [uuid_ascii.encode("ascii")]
    parts.append(bytes([len(roast_pairs)]))  # roastPointsのペア数マーカー

    for t, v in roast_pairs:
        parts.append(_encode_value(t).encode("ascii"))
        parts.append(_encode_value(v).encode("ascii"))

    parts.append(bytes([fan_marker]))  # fanPointsのペア数+1(実機キャプチャ2件で確認済み)
    for t, v in fan_pairs:
        parts.append(_encode_value(t).encode("ascii"))
        parts.append(_encode_value(v).encode("ascii"))

    if len(profile.cooldown.x) != 1:
        raise ValueError("cooldownPointは1ペア固定という前提が崩れています")
    parts.append(_encode_value(profile.cooldown.x[0]).encode("ascii"))
    parts.append(_encode_value(profile.cooldown.y[0]).encode("ascii"))

    parts.append(TERMINATOR)  # 未確定: 意味不明の終端バイト。暫定的に"S"を使用(要検証、下記参照)
    return b"".join(parts)


def decode_profile_payload(raw: bytes) -> dict:
    """キャプチャした生バイト列(全フラグメント連結済み)を、
    roastPoints / fanPoints / cooldownPoint の数値列に戻す。

    戻り値: {"uuid": str, "roast": [(t,v),...], "fan": [(t,v),...], "cooldown": (t,v)}
    """
    if len(raw) < 17:
        raise ValueError("データが短すぎます")

    uuid_ascii = raw[:16].decode("ascii")
    roast_pair_count = raw[16]  # 実機検証済み: roastPointsのペア数がそのままバイト値になる
    rest = raw[17:]

    roast_digit_len = roast_pair_count * 8  # 1ペア=時間4桁+値4桁=8桁
    if len(rest) < roast_digit_len + 1:
        raise ValueError("roastPoints部分のデータが不足しています")

    roast_digits = rest[:roast_digit_len].decode("ascii")
    after_roast = rest[roast_digit_len:]

    if not after_roast:
        raise ValueError("fanPoints開始マーカーが見つかりません(データが短すぎます)")
    fan_marker = after_roast[0]  # 実機検証済み: fanPointsのペア数+1がそのままバイト値になる
    remainder = after_roast[1:]

    if not remainder.endswith(TERMINATOR):
        # 終端バイトの意味は未確定なので、警告扱いにせず末尾1バイトを切り落とすだけに留める
        pass
    remainder = remainder[:-1].decode("ascii")

    if len(roast_digits) % 4 != 0:
        raise ValueError("roastPoints部分が4桁単位で割り切れません")
    if len(remainder) % 4 != 0:
        raise ValueError("fanPoints+cooldown部分が4桁単位で割り切れません")

    roast_vals = [_decode_value(roast_digits[i:i+4]) for i in range(0, len(roast_digits), 4)]
    fan_and_cd_vals = [_decode_value(remainder[i:i+4]) for i in range(0, len(remainder), 4)]

    # cooldownPointは末尾2値で固定
    cd_vals = fan_and_cd_vals[-2:]
    fan_vals = fan_and_cd_vals[:-2]

    expected_marker = len(fan_vals) // 2 + 1
    if fan_marker != expected_marker:
        raise ValueError(
            f"fanPointsマーカーの値が不整合です(期待値0x{expected_marker:02x}, 実際0x{fan_marker:02x})"
        )

    def pair_up(vals: List[int]) -> List[Tuple[int, int]]:
        return list(zip(vals[0::2], vals[1::2]))

    return {
        "uuid": uuid_ascii,
        "roast": pair_up(roast_vals),
        "fan": pair_up(fan_vals),
        "cooldown": (cd_vals[0], cd_vals[1]),
    }
