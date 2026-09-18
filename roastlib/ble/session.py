# ============================================================
# Roast Studio
# roastlib/ble/session.py
# ------------------------------------------------------------
# 任意のプロファイル(DBのプリセットでも、GUIで編集したものでも)を
# 焙煎機に送信し、反応型プロトコル(UUID確認応答・ハートビート応答)を
# 継続しながら、温度テレメトリ・推定ステータスをコールバックで通知する
# セッション管理。
#
# 2026-07時点の実機検証状況:
#   本モジュールの送受信ロジックは、多数回の実機(THE ROAST EXPERT)への
#   実送信・実受信で繰り返し検証済み(プロファイル送信→予熱→投入→焙煎→
#   冷却→排出まで、複数の実プロファイルで正常完了を確認)。以前あった
#   「最初の2回の書き込みは実機キャプチャの値をそのまま流用しており
#   プロファイル内容に応じて変化するか未確認」という注意書きは、下記の
#   build_write_sequence()のheader_1/header_2で解消済み(header_2の
#   先頭バイトは総ペア数から計算する式が判明しており、単なる値の使い回しではない)。
#
# ⚠️ ステータス推定について:
#   フェーズコード(20byte通知、末尾1byte。0x20=待機中〜0x27=排出待ち、
#   PHASE_CODE_STATES参照)を受信できた場合は、それを最優先の判定材料として
#   使う(実機で明示的に確認済みの値のため)。フェーズコードを検出できない
#   区間(例: 起動直後の一瞬)に限り、温度テレメトリの傾向(上昇・停滞・下降)
#   と経過時間からのヒューリスティック推定にフォールバックする。
#
# ⚠️ 未解明のまま残っている点(2026-07時点):
#   - command_writeへの最初の2回の書き込み・閉じヘッダの16バイト「トークン」は
#     BLE接続のたびにこちらでランダム生成している値で、実機側の認証・ペアリング
#     とは無関係と考えられる(_token参照)。ただしOS(macOS)レベルのBluetooth
#     ペアリング・ボンディング自体は本モジュールの外側(bleak/CoreBluetooth)で
#     行われており、その手順自体は解析対象に含めていない。
#   - プロファイル転送データの終端1バイトは、既知プロファイルは実測値
#     (KNOWN_TERMINATORS)、未知のプロファイルはチェックサムベースの推定式
#     (_guess_terminator)で運用できているが、この式が意味する「本来何を
#     表す値なのか」(本物のチェックサムアルゴリズムなのか、たまたま実測に
#     フィットした近似式なのか)は未確定。
#   - 焙煎中に、送信済みのプロファイルを書き換える(温度・風量をリアルタイムに
#     手動調整する)ことが可能かは未検証(実験自体まだ行っていない)。
# ============================================================

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional, Sequence, Tuple

from .codec import encode_profile_payload
from ..models import RoastCurve, RoastProfile

import os

MAIN_SERVICE_UUID = "d0927d90-a443-4582-b431-962abe7e4be1"
CHAR_STATUS_NOTIFY = "39350d46-fee7-436b-8aae-8b92855f5ba2"
CHAR_COMMAND_WRITE = "3fb74db9-6a2e-4e59-99a2-08cb2b8bd4a7"

# ------------------------------------------------------------
# 2026-07、複数回の実機キャプチャを比較した結果、この16バイトの「トークン」は
# 固定値ではなく、BLE接続のたびに新しく生成されている(同一接続内では不変)
# ことが判明した。そのため、RoasterSession ごとに os.urandom(16) で
# 新規生成する。
#
# なお、ヘッダの前後バイト(下記 HEADER_1/HEADER_2 の prefix/suffix)は、
# 送信するプロファイルの中身によって変化することも判明しているが、
# その正確な計算式(チェックサム？)はまだ特定できていない。
# 現状は実機キャプチャで観測された値をそのまま暫定的に使っている。
# 一方、閉じヘッダ(HEADER_CLOSE, prefix=13 00 / suffix=41 00)は、
# 数週間空けた複数回のキャプチャで完全に同じ値だったため、
# 真の固定値である可能性が高い。
# ------------------------------------------------------------
CHUNK_SIZE = 20

# 推定ステータスの一覧(表示用の日本語ラベル)
STATE_DISCONNECTED = "未接続"
STATE_PAIRING = "ペアリング中"
STATE_PAIRED = "ペアリング完了"
STATE_RECONNECTING = "再接続中…"
STATE_SENDING = "プロファイル送信中"
STATE_RECEIVED = "プロファイル受信"
STATE_STANDBY = "待機中"
STATE_PREHEATING = "予熱中"
STATE_PREHEAT_DONE = "予熱完了(豆投入待ち)"
STATE_CHARGING = "豆投入操作中"
STATE_ROASTING = "焙煎中"
STATE_ROAST_DONE = "焙煎完了・冷却中"
STATE_COOLING_DONE = "冷却完了(容器交換待ち)"
STATE_FORCED_COOLING = "強制冷却中"
STATE_DISCHARGING = "排出中"
STATE_DISCHARGE_DONE = "排出完了"

# エラー系の状態は、フロントエンド側で先頭の"エラー:"を手がかりに文字色を変えて
# 区別できるよう、必ずこのプレフィックスを付けて emit すること。
STATE_CONTAINER_ERROR = "エラー: 容器が正しくセットされていない可能性があります"

# 容器エラー通知("U"+4桁のASCII数字コード)ごとの具体的な状態文言。
# 2026-07、実機で2回ずつ再現し、以下の対応で一貫していることを確認済み:
#   "0220" = ガラス容器を外した際に出現
#   "9120" = 豆投入容器(投入口)を開けた際に出現
# 対応表に無いコードは、汎用のSTATE_CONTAINER_ERRORにフォールバックする。
STATE_GLASS_CONTAINER_ERROR = "エラー: ガラス容器が外れているか、正しくセットされていません"
STATE_BEAN_CHARGE_ERROR = "エラー: 豆投入口(容器)が開いています。閉じてください"
CONTAINER_ERROR_CODE_STATES = {
    "0220": STATE_GLASS_CONTAINER_ERROR,
    "9120": STATE_BEAN_CHARGE_ERROR,
}

# 強制冷却完了後、未焙煎豆を回収するためガラス容器を外すよう促す状態(2026-07発見)。
# フェーズコード0x27と、専用のU通知コード"1200"が同じタイミングで届く。見た目は
# 容器エラー通知(Uコード)と同じ形だが、実際には「容器を外してください」という
# 正常な操作案内であり機械の異常ではないため、他の容器エラー(0220/9120)のような
# _error_active(温度上昇での復帰待ち)の対象にはしない扱いとする。
STATE_DISCHARGE_PROMPT = "排出待ち: ガラス容器を外して豆を回収してください"
DISCHARGE_PROMPT_CODE = "1200"

# フェーズコード(実機Notifyの末尾1バイト)と推定ステータスの対応表。
# 2026-07、実際に予熱→投入→焙煎→自然完了(冷却)まで到達したセッションで、
# 「13 00」または「24 00」+トークン+suffix(先頭が0xe0/0xe4)の末尾1バイトが
# 0x21→0x22→0x23→0x24 と遷移するのを確認。0x24は、焙煎開始から約7分後
# (プロファイルの予定焙煎時間に近いタイミング)に出現したため、自然な
# 「焙煎完了・冷却中」に対応すると推定。温度のしきい値より確実な手がかりのため、
# こちらを優先して状態判定に使う。
# 0x26は、別セッションで実際にスイッチを押して強制冷却に入れた瞬間に出現したため、
# 「強制冷却中」に対応すると推定。
# 2026-07追記(エラー再現ログより):
#   0x20は接続直後・UUID確認前に出現(0x21より前の段階と推定し「待機中」とする)。
#   0x25は、実際に強制冷却スイッチを押した瞬間に0x26と同じ文脈(suffix 0xE0)で
#   出現した。0x26(強制冷却中)と統合し、両方とも同じ扱いにする
#   (これにより、0x26が来る前に切断された場合でも「焙煎中の異常切断」と誤判定して
#   無駄な自動再接続をしてしまう不具合も解消される)。
#   0x27は、強制冷却完了後にガラス容器を外して豆を回収するよう促す状態で出現した
#   (STATE_DISCHARGE_PROMPT参照、専用のU通知コード"1200"と同じタイミング)。
PHASE_CODE_STATES = {
    0x20: STATE_STANDBY,
    0x21: STATE_PREHEATING,
    0x22: STATE_CHARGING,
    0x23: STATE_ROASTING,
    0x24: STATE_ROAST_DONE,
    0x25: STATE_FORCED_COOLING,
    0x26: STATE_FORCED_COOLING,
    0x27: STATE_DISCHARGE_PROMPT,
}

# ステータス推定のしきい値(2026-07、実際に予熱→投入→焙煎まで成功したセッションの
# 温度推移データを基に再調整。予熱完了直後、豆投入前の自然な微減(112→107程度、
# 10秒で5前後)を「投入によるチャージドロップ」と誤検知していたため、
# CHARGE_DROP_THRESHOLD / ROAST_DONE_DROP を大きく引き上げた)
PLATEAU_WINDOW = 3          # 直近何サンプルで停滞と判断するか
PLATEAU_TOLERANCE = 4       # 停滞(または緩やかな変化)とみなす変動幅
MIN_PREHEAT_RISE = 10       # 「予熱で十分上昇した」とみなす最低上昇幅(アイドル状態との誤判定防止)
CHARGE_DROP_THRESHOLD = 15  # 投入によるチャージドロップとみなす下落幅(自然な微減より十分大きく)
MIN_ROASTING_SECONDS = 30   # 焙煎中とみなしてから、完了判定を有効にするまでの最低秒数
ROAST_DONE_DROP = 15        # 焙煎完了(下降開始)とみなす下落幅
MIN_ROAST_DONE_SECONDS = 4  # 焙煎完了から、排出中判定を有効にするまでの最低秒数
DISCHARGE_DONE_BT = 40      # この値まで下がったら排出完了とみなす

# 温度の傾向だけでは判定できない場合のフォールバック(この秒数を超えたら強制的に次へ)
PHASE_TIMEOUT_SECONDS = 240

# 容器エラー(ガラス容器・豆投入口)からの復帰判定(2026-07)。
# エラー通知自体には「復帰した」という専用信号が無いため、焙煎中に温度が
# 再び上昇し始めたことをもって復帰とみなす(ユーザー様のご提案)。
# エラー中に記録された最低温度からこの幅以上上昇したら復帰と判定する。
ERROR_RECOVERY_RISE = 2.0

# ------------------------------------------------------------
# 接続の安定性まわりの設定(2026-07追加)
# ------------------------------------------------------------
# 接続(スキャン+connect)を最大何回試すか。1回だと、機械の広告が一瞬途切れた
# ・connectが一時的に失敗した、というだけで「接続できません」になり、手動で
# もう一度送信し直すと成功する、という症状が出ていた。数回リトライして拾う。
CONNECT_RETRY_ATTEMPTS = 3
CONNECT_RETRY_DELAY = 1.5      # リトライ間隔(秒)

# 焙煎中に接続が切れた場合の自動再接続。焙煎自体は機械側で自律的に進むため、
# 再接続してNotify購読をやり直せば、テレメトリ・ステータスの受信を再開できる。
#
# 回数で打ち切ってはいけない。2026-09の実機ログでは、1回目が接続の
# タイムアウト(30秒)で潰れ、残り5回はスキャン6秒+待ち3秒で消化して、
# 合計78秒で諦めていた。そのとき焙煎はまだ2ハゼ直後で4分以上残っており、
# 以降の記録が全部落ちた。焙煎が終わるまでは粘る。
RECONNECT_DELAY = 3.0            # 失敗直後の待ち
RECONNECT_DELAY_MAX = 10.0       # 何度も失敗したときの待ち(叩き続けない)
RECONNECT_BUDGET_MAX = 20 * 60.0  # どれだけ長くてもここで打ち切る
RECONNECT_BUDGET_MIN = 90.0      # プロファイルが分からないときでもこれだけは粘る
RECONNECT_MARGIN_SECONDS = 120.0  # 焙煎+冷却の予定に足す余裕
# 1回あたりの接続の待ち。既定(30秒前後)のままだと、1回の失敗で粘る時間の
# ほとんどを使い切ってしまう。
RECONNECT_CONNECT_TIMEOUT = 12.0

# 「焙煎完了・冷却中」の間に切断された場合のフォールバック猶予秒数。
# 2026-07: 実機ログで、冷却開始直後(容器交換が物理的に起こり得ないタイミング)に
# 切断される事例を確認した。プロファイルには冷却完了予定時刻(cooldownPoint)が
# 含まれているため、本来はその時刻までは「まだ冷却中で、容器交換ではあり得ない」
# と判断できる(_planned_cooldown_sec参照)。この値が不明な場合のみ、この定数を
# 保守的な下限として使う。
DEFAULT_COOLDOWN_GRACE_SECONDS = 60.0

# 冷却完了とみなす吸入温度(℃)と、温度が一度も取れなかった場合の代替の猶予秒数。
#
# 2026-09、ユーザー様のご指摘と実機ログで確定した: 焙煎機自身が「吸入温度60℃」を
# 閾値にして冷却完了を判定している。ログでは吸入温度が60℃に下がった3秒後に
# 冷却完了・容器交換要求の通知が届いた。
#
# この判定が要るのは、冷却完了の合図として使っている「ほぼ全ゼロの18〜19byte通知」が
# 冷却専用の信号ではないため。同じ形の通知が、焙煎中(186℃)にも冷却開始直後(247℃)にも
# 届いていることを実機ログで確認した(先頭1byteが通し番号になっており、何かの
# イベント一般を表しているらしい)。以前は「焙煎完了・冷却中に入って30秒以上」でしか
# ふるいに掛けていなかったため、まだ200℃近くある時点の通知を冷却完了と誤認して
# いた。温度で見れば、焙煎機自身の判定と同じ基準になる。
#
# プロファイルのcooldownPoint(冷却完了予定)の目標温度も60℃で揃っており、
# 機械側の閾値と一致している。
COOLING_DONE_INTAKE_TEMP = 60.0
# 温度が一度も取れていないときだけ使う代替条件(旧来の判定)。通常は使われない。
COOLING_DONE_MIN_SECONDS = 30.0

# 反応型ハートビートの最小送信間隔(秒)。実機のNotifyは約300msごとに5フラグメントの
# バーストで届くため、「1フラグメントごとに1回応答」だと1秒あたり十数回の書き込みが
# 走り、command_writeのキューが詰まって数十秒〜1分ハングし、最終的に切断される
# 一因になっていた(コード内の_ackの注記参照)。バーストの中では最初の1回だけ応答し、
# 実質「バーストごと(≒300ms)に1回」に間引いて書き込み負荷を大幅に下げる。
# 0にすると従来どおり全Notifyに応答する(機械側がフラグメント単位のackを要求する
# 仕様だった場合はこちらに戻す。実機で検証しやすいよう定数化している)。
HEARTBEAT_MIN_INTERVAL = 0.28


def _chunk(data: bytes, size: int = CHUNK_SIZE) -> List[bytes]:
    return [data[i:i + size] for i in range(0, len(data), size)]


# 実機キャプチャで確認できている「本当の終端バイト」。
#
# 2026-08修正: これを終端バイトの上書きに使うのをやめた。下記3件はいずれも
# _guess_terminator() の計算結果と完全に一致しており(tests/test_ble_codec.py で
# 毎回確かめている)、上書きしても結果は変わらない。一方でUUIDだけを鍵にして
# 上書きしていたため、この3件のプロファイルを「編集してから送る」と、中身が
# 変わっているのに実測時の終端バイトが当てられ、機械がプロファイルを破棄する
# (送信は完了するのに予熱に進まない)状態になっていた。
# 表は計算式が正しいことの裏づけとして残し、送信では使わない。
#
# 2026-07修正: 終端バイトはトークンに紐づく値のため、旧トークン期に実測した値を
# 現行トークンのまま使うと、機械側がプロファイルを不正データとして破棄し、
# 送信は完了するのに予熱に進まない(UUIDエコーが返ってこない)症状になる。
# 実際に下記3件でこの症状を確認したため、旧トークン期の実測値は削除し、
# 現行トークン用の計算式(_guess_terminator)に任せる。
#   1002190703114001 (id49 エチオピア)  実測0x53 / 現行式0xce
#   3033190703114901 (id29 イエメン)    実測0xd8 / 現行式0x53
#   6103210303114201 (id22 コロンビア)  実測0x72 / 現行式0xed
# 残した3件はいずれも現行トークン期の実測値で、計算式の結果と完全に一致することを
# 確認済み(=計算式が現行トークンに対して正しいことの裏付けでもある)。
KNOWN_TERMINATORS = {
    "6004190703115101": 0xa6,  # id65 グアテマラ
    "6002190703115001": 0x86,  # id43 エチオピア(河合さんバージョン No.2)
    "6005190703115101": 0xa5,  # id70 インドネシア_スマトラ(河合さん No.2)
}

# 2026-07: 終端バイトの計算式を突き止めた。
# 最初、同一トークン・同一構成(roastPoints 4ペア・fanPoints 3ペア)の3プロファイル
# (6004/6002/6005、内容は異なる)で「終端バイト = SUM(本体) - 0x26」が一致したが、
# 構成が違うプロファイル(roastPoints 6ペア・fanPoints 4ペア)では外れた。
# 総ペア数(roastPoints+fanPoints)ごとに定数を逆算したところ、
#   終端バイト = (SUM(本体) + 8 × 総ペア数 + 0xa2) % 256
# という、ペア数に依存しない真に一定の定数(0xa2、現行トークンでの値)が見つかり、
# 構成の異なる4プロファイル全てで完全一致した。
# (この0xa2は現行トークンa6ab282f...に紐づく値の可能性が高く、トークンが変われば
#  再検証が必要)
TERMINATOR_CHECKSUM_OFFSET = 0xA2
TERMINATOR_PER_PAIR_ADJUST = 8


def _guess_terminator(body_without_terminator: bytes, total_pairs: int) -> int:
    checksum = sum(body_without_terminator) & 0xFF
    return (checksum + TERMINATOR_PER_PAIR_ADJUST * total_pairs + TERMINATOR_CHECKSUM_OFFSET) & 0xFF



def build_write_sequence(profile: RoastProfile, token: bytes) -> List[bytes]:
    """プロファイルを、実機に送信するための書き込みバイト列のリストに変換する。

    実機キャプチャで観測された順序: ヘッダ2回 → データ本体(20byteずつ分割) → 終了ヘッダ

    token: このBLE接続セッション用にランダム生成された16バイト値
           (RoasterSession._token を渡す)。
    """
    total_pairs = len(profile.roast.x) + len(profile.fan.x)

    # 2026-07発見: header2の先頭バイトも、総ペア数(roastPoints+fanPoints)に応じて
    # 変化することが判明。(8 × 総ペア数 + 45) % 256 で、構成の異なる2プロファイル
    # (7ペア→0x65、10ペア→0x7d)で完全一致を確認済み(現行トークン限定)。
    header2_prefix_byte = (8 * total_pairs + 45) & 0xFF

    header_1 = b"\x12\x00" + token + b"\x44\xab"   # 現状、総ペア数によらず一定と確認
    header_2 = bytes([header2_prefix_byte, 0x00]) + token + b"\x20\x00"
    header_close = b"\x13\x00" + token + b"\x60\x00"
    close_tail = b"\xc8"  # 同一セッション内の複数回の完了した送信で一貫して確認

    payload = bytearray(encode_profile_payload(profile))

    # 終端バイトは中身から決まる(KNOWN_TERMINATORSの3件もこの式と一致する)。
    # UUIDで場合分けすると、編集して中身が変わったときに古い値を当ててしまう。
    payload[-1] = _guess_terminator(bytes(payload[:-1]), total_pairs)

    sequence = [header_1, header_2]
    sequence += _chunk(bytes(payload))
    sequence.append(header_close)
    sequence.append(close_tail)
    return sequence


def profile_from_points(
    name: str,
    roast_points: Sequence[Tuple[float, float]],
    fan_points: Sequence[Tuple[float, float]],
    cooldown_point: Tuple[float, float],
    uuid_ascii: str,
) -> RoastProfile:
    """GUIで編集した制御点から、送信用のRoastProfileを組み立てるヘルパー。

    uuid_asciiは16桁の数字文字列である必要がある(profile.UUIDカラムと同じ形式)。
    既存プリセットを元に編集した場合は、そのプリセットのUUIDをそのまま使うこと
    (新規プロファイル用のUUID発行ルールは未解明のため)。
    """
    if len(uuid_ascii) != 16 or not uuid_ascii.isdigit():
        raise ValueError("uuid_asciiは16桁の数字文字列である必要があります")

    rx = [p[0] for p in roast_points]
    ry = [p[1] for p in roast_points]
    fx = [p[0] for p in fan_points]
    fy = [p[1] for p in fan_points]

    return RoastProfile(
        id=-1,
        name=name,
        roast=RoastCurve(rx, ry),
        fan=RoastCurve(fx, fy),
        cooldown=RoastCurve([cooldown_point[0]], [cooldown_point[1]]),
        raw={"UUID": uuid_ascii},
    )


@dataclass
class TelemetrySample:
    t: float             # 受信からの経過秒(セッション基準、BLE受信時刻ベース)
    bt: Optional[float]  # 流入空気温度らしき値(未確定、単位は生の数値のまま)
    fan: Optional[float] = None  # 実測風量(%、0〜100。校正の詳細は_handle_notify参照)
    # 機械自身が数える「焙煎開始からの経過秒数」(8・9バイト目、0始まりでindex7:8の
    # LE16bit値。2026-07発見)。焙煎中(フェーズ0x23)に入るまでは常に0で、入った
    # 瞬間から1秒刻みで正確に増え続ける。容器エラー・強制冷却中も一切乱れないことを
    # 実機ログで確認済み。BLE受信タイミングのジッターの影響を受けない、機械側の
    # 正確な基準時刻として使える。
    elapsed_sec: Optional[int] = None
    raw_hex: str = ""


class RoastPhaseEstimator:
    """温度テレメトリの傾向から、大まかな進行状態を推定するヘルパー。

    確定した機械の状態コードではなく、あくまで推定であることに注意。
    各フェーズには最低滞在時間を設けており、下降開始時に複数の状態が
    一瞬で連鎖して切り替わってしまうことを防いでいる。
    """

    def __init__(self):
        self.phase = "awaiting_receive"  # awaiting_receive -> preheating -> preheat_done
                                          # -> roasting -> roast_done -> cooling_done
                                          # -> discharging -> done
        self.history: List[Tuple[float, float]] = []  # (t, bt)
        self.peak_bt = None
        self.phase_start_t: Optional[float] = None
        self._preheat_emitted = False

    def reset(self):
        self.__init__()

    def mark_received(self):
        self.phase = "preheating"
        self.phase_start_t = None
        self._preheat_emitted = False

    def mark_confirmed_phase(self, internal_phase: str, t: float) -> None:
        """フェーズコード等、温度以外の確実な手がかりで状態が判明した際に、
        温度ベースの推定(フォールバック)をこの時点から仕切り直す。"""
        self.phase = internal_phase
        self.phase_start_t = t
        self.peak_bt = None

    def _time_in_phase(self, t: float) -> float:
        if self.phase_start_t is None:
            self.phase_start_t = t
            return 0.0
        return t - self.phase_start_t

    def _advance(self, new_phase: str, t: float) -> None:
        self.phase = new_phase
        self.phase_start_t = t

    def update(self, t: float, bt: float) -> Optional[str]:
        """新しいテレメトリを取り込み、状態が変化したら新しいSTATE_*文字列を返す。"""
        self.history.append((t, bt))
        if len(self.history) > 200:
            self.history = self.history[-200:]
        if self.peak_bt is None or bt > self.peak_bt:
            self.peak_bt = bt

        dt_in_phase = self._time_in_phase(t)
        recent = [b for _, b in self.history[-PLATEAU_WINDOW:]]

        if self.phase == "preheating":
            # 2026-07: 以前はここで温度の停滞(プラトー)を検知して自動的に
            # 「予熱完了(豆投入待ち)」へ進めていたが、実機では投入操作の瞬間まで
            # 温度自体はほぼ横ばい〜微減が続くだけで、確実な合図にはならないと判明。
            # 現在はフェーズコード(0x22=投入操作中)による確実な遷移だけに任せ、
            # ここでは初回の「予熱中」通知だけ行い、あとは何もしない。
            if not self._preheat_emitted:
                self._preheat_emitted = True
                return STATE_PREHEATING
            return None

        if self.phase == "preheat_done":
            if self.peak_bt - bt >= CHARGE_DROP_THRESHOLD or dt_in_phase >= PHASE_TIMEOUT_SECONDS:
                self.peak_bt = bt
                self._advance("roasting", t)
                return STATE_ROASTING
            return None

        if self.phase == "roasting":
            # 2026-07: 以前はここで温度の下落幅(ROAST_DONE_DROP)から自動的に
            # 「焙煎完了」へ進めていたが、実機では焙煎開始直後の一時的な変動でも
            # 誤って早期発火し、まだ焙煎中(約7分続く)なのに「排出中」まで
            # 進んでしまう不具合があった。フェーズコード(0x24=焙煎完了・冷却中)
            # による確実な遷移のみに任せ、ここでは何もしない。
            return None

        if self.phase == "roast_done":
            # 2026-07訂正: 焙煎完了(0x24)後の温度低下は、実際にはまだ冷却モードの
            # 継続であり、「豆排出モード」ではないと判明した(ユーザー様の実機での
            # 確認による)。本当の排出モードは、ガラス容器交換後に始まり、開始と
            # 同時にBLE接続が切れる仕様のようで、温度の推移からは判定できない。
            # そのため、ここでの自動遷移は行わない(0x24の状態のまま維持する)。
            return None

        if self.phase == "cooling_done":
            # 排出完了は温度ではなく確認要求パターン(_handle_notify側)で検出するため、
            # ここでは温度ベースの自動遷移は行わない。
            return None

        if self.phase == "discharging":
            if bt <= DISCHARGE_DONE_BT:
                self._advance("done", t)
                return STATE_DISCHARGE_DONE
            return None

        return None


@dataclass
class RoasterSession:
    """1回の接続〜送信〜モニタリングを管理するクラス。

    使い方:
        session = RoasterSession(on_telemetry=cb1, on_status=cb2, on_state=cb3)
        await session.connect()
        await session.send_profile(profile)
        ... # on_telemetry / on_stateが呼ばれ続ける
        # 排出完了(STATE_DISCHARGE_DONE)になったら、接続を保ったまま
        # 再度 send_profile() を呼べば連続で次のプロファイルを焙煎できる
        await session.disconnect()
    """

    on_telemetry: Optional[Callable[[TelemetrySample], None]] = None
    on_status: Optional[Callable[[str], None]] = None
    on_state: Optional[Callable[[str], None]] = None

    _client: object = field(default=None, init=False, repr=False)
    # 容器エラー(ガラス容器・豆投入口)からの復帰検知用(2026-07)。
    # エラー通知には「復帰した」という専用信号が無いため、焙煎中の温度が
    # エラー中の最低値から一定以上再上昇したことをもって復帰とみなす。
    _error_active: bool = field(default=False, init=False)
    _error_min_bt: Optional[float] = field(default=None, init=False)
    # プロファイルの冷却予定時間(焙煎終了〜cooldownPointまでの秒数)。
    # send_profile()で算出し、_on_disconnected()の「焙煎完了・冷却中」判定で使う。
    _planned_cooldown_sec: Optional[float] = field(default=None, init=False)
    _planned_roast_sec: Optional[float] = field(default=None, init=False)   # プロファイル上の焙煎時間
    # 直近の吸入温度と、「冷却完了の温度まで下がった」ことの記憶(2026-09)。
    # 温度は1.5秒おきに届くが、冷却完了の通知と同時に届くとは限らないため、
    # 一度でも下がりきったら覚えておき、その後の通知を受け付ける。
    _last_bt: Optional[float] = field(default=None, init=False)
    _cooling_target_reached: bool = field(default=False, init=False)
    _acked_uuid_echo: bool = field(default=False, init=False)
    _profile_uuid_ascii: Optional[bytes] = field(default=None, init=False)
    _start_time: Optional[float] = field(default=None, init=False)
    _connected: bool = field(default=False, init=False)
    _phase_estimator: RoastPhaseEstimator = field(default_factory=RoastPhaseEstimator, init=False)
    _loop: object = field(default=None, init=False, repr=False)
    # 2026-07: ランダム生成を試したところ、以前より早く切断されるようになった
    # (=機械側がトークンの中身を検証している証拠)。そのため、直近の実機
    # キャプチャで確認できている値に戻す。この値がいつまで有効かは不明。
    _token: bytes = field(
        default_factory=lambda: bytes.fromhex("a6ab282f4108003225d944e5500b2090"),
        init=False,
    )
    _pending_echo_header: Optional[bytes] = field(default=None, init=False)
    _last_phase_code: Optional[int] = field(default=None, init=False)
    _write_lock: object = field(default=None, init=False, repr=False)
    # 接続安定性まわり(2026-07)
    _target_device: object = field(default=None, init=False, repr=False)  # 直近に接続した相手(再接続で優先的に使う)
    _user_initiated_disconnect: bool = field(default=False, init=False)   # disconnect()を自分で呼んだか
    _reconnecting: bool = field(default=False, init=False)                # 自動再接続の実行中フラグ(多重起動防止)
    _last_heartbeat_t: float = field(default=0.0, init=False)             # 直近にハートビート応答した時刻(間引き用)

    def _log(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}"
        print(line)
        if self.on_status:
            self.on_status(line)

    def _emit_state(self, state: str) -> None:
        if self.on_state:
            self.on_state(state)

    async def _find_target(self, timeout: float):
        """焙煎機をスキャンして最初に見つかった1台を返す(見つからなければNone)。"""
        from bleak import BleakScanner  # 遅延import
        devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
        for device, adv in devices.values():
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            name = device.name or ""
            if MAIN_SERVICE_UUID in uuids or "ROAST" in name.upper() or "NR01" in name.upper():
                return device
        return None

    async def _scan_and_connect(self, timeout: float) -> bool:
        """1回分の「スキャン→接続→Notify購読」。成功でTrue。
        connect()(初回)と自動再接続の両方から使う共通処理。"""
        from bleak import BleakClient  # 遅延import
        import time as _time

        # 前回つないだデバイスが分かっていれば、まずスキャン無しで直接つなぎ直す
        # (再接続を速くする。失敗したら通常のスキャンにフォールバック)。
        target = self._target_device
        if target is None:
            self._log("BLEデバイスをスキャン中...")
            target = await self._find_target(timeout)
        if target is None:
            self._log("焙煎機が見つかりませんでした。")
            return False

        self._log(f"発見: {getattr(target, 'address', target)} 接続中...")
        client = BleakClient(target, disconnected_callback=self._on_disconnected)
        try:
            # 上限を付ける。既定(30秒前後)のままだと、1回の失敗で再接続に
            # 使える時間のほとんどを消費してしまう(実機ログで確認)。
            await asyncio.wait_for(client.connect(), timeout=RECONNECT_CONNECT_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            # 直接再接続に失敗した場合は、次回はスキャンからやり直せるようにする。
            self._target_device = None
            self._log(f"接続に失敗しました: {e!r}")
            return False
        if not client.is_connected:
            self._log("接続後に is_connected=False でした。")
            return False

        await client.start_notify(CHAR_STATUS_NOTIFY, self._on_notify)
        self._client = client
        self._connected = True
        self._target_device = target
        if self._start_time is None:
            # 初回接続のときだけ経過時間の基準を置く(再接続では焙煎の時間軸を保つ)。
            self._start_time = _time.monotonic()
        self._log(f"接続成功: is_connected={self._connected}")
        return True

    async def connect(self, timeout: float = 10.0) -> bool:
        self._loop = asyncio.get_running_loop()
        self._write_lock = asyncio.Lock()
        self._user_initiated_disconnect = False
        self._target_device = None  # 新規接続なので毎回スキャンから

        self._emit_state(STATE_PAIRING)
        # 1回だと、広告が一瞬途切れた・connectが一時失敗しただけで諦めてしまい、
        # 手動で送信し直すと成功する症状が出ていたため、数回リトライする。
        for attempt in range(1, CONNECT_RETRY_ATTEMPTS + 1):
            if attempt > 1:
                self._log(f"接続を再試行します({attempt}/{CONNECT_RETRY_ATTEMPTS})...")
                await asyncio.sleep(CONNECT_RETRY_DELAY)
            if await self._scan_and_connect(timeout):
                self._emit_state(STATE_PAIRED)
                return True

        self._emit_state(STATE_DISCONNECTED)
        return False

    # 焙煎が進行中(この間の切断は「異常」なので再接続を試みる価値がある)とみなすフェーズ。
    # roast_done(焙煎完了・冷却中)は、冷却予定時間を過ぎてからの切断であれば容器交換・
    # 排出モード移行に伴う「想定内の切断」とみなし対象外とするが、予定時間内であれば
    # _on_disconnected()側で個別に再接続を試みる(下記参照)。
    _ACTIVE_ROAST_PHASES = frozenset({"preheating", "preheat_done", "roasting"})

    def _on_disconnected(self, client) -> None:
        """bleakが検知した切断イベント。macOS/焙煎機側から一方的に切られた場合、
        こちらのコードが disconnect() を呼んでいなくてもこれが呼ばれる。"""
        self._connected = False

        # 自分で disconnect() を呼んだ場合や、既に再接続処理中の場合は何もしない。
        if self._user_initiated_disconnect or self._reconnecting:
            return

        phase = self._phase_estimator.phase
        if phase == "roast_done":
            # 実機で確認済み: 焙煎完了・冷却中に、ガラス容器を交換して排出モードに
            # 入ると、その瞬間にBLE接続が切れる仕様のようだ。この場合の切断は
            # 異常ではなく、排出モードへの正常な移行を示している可能性が高い。
            #
            # 2026-07追記: ただし、実機ログで「焙煎完了・冷却中」に入った直後
            # (容器交換が物理的に起こり得ないタイミング)に切断される事例を確認した。
            # プロファイルの冷却予定時間(_planned_cooldown_sec、無ければ
            # DEFAULT_COOLDOWN_GRACE_SECONDS)がまだ経過していなければ、容器交換にしては
            # 早すぎるとみなし、焙煎中と同様に自動再接続を試みる。
            import time as _time
            t_now = _time.monotonic() - (self._start_time or 0)
            dt_in_phase = t_now - (self._phase_estimator.phase_start_t or t_now)
            grace = self._planned_cooldown_sec if (self._planned_cooldown_sec and self._planned_cooldown_sec > 0) else DEFAULT_COOLDOWN_GRACE_SECONDS
            if dt_in_phase < grace:
                self._log(
                    f"[イベント] 冷却予定時間内(経過{dt_in_phase:.0f}秒/予定{grace:.0f}秒)に切断されました。"
                    "容器交換にしては早すぎるため、自動で再接続を試みます。"
                )
                if self._loop is not None:
                    asyncio.run_coroutine_threadsafe(self._auto_reconnect(), self._loop)
                return
            self._log("[イベント] 切断されました。冷却完了後の容器交換・排出モード移行による、想定内の切断の可能性があります。")
            self._emit_state(STATE_DISCONNECTED)
            return

        if phase in self._ACTIVE_ROAST_PHASES:
            # 焙煎中(予熱〜焙煎中)の予期しない切断。焙煎自体は機械側で続くので、
            # 自動で再接続してテレメトリ・ステータスの受信再開を試みる。
            self._log("[イベント] 焙煎中に切断されました。自動で再接続を試みます。")
            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(self._auto_reconnect(), self._loop)
            return

        self._log("[イベント] 焙煎機側(またはOS)から切断されました(disconnected_callback)")
        self._emit_state(STATE_DISCONNECTED)

    async def _auto_reconnect(self) -> None:
        """焙煎中の予期しない切断からの自動再接続。焙煎の時間軸・推定フェーズ・
        トークン・UUID確認済みフラグは保持したまま、スキャン→接続→Notify購読を
        やり直す。成功すればテレメトリ・ステータスの受信が再開する(実機での挙動は
        要検証。機械が再接続クライアントへの通知送信を継続するかは未確認)。"""
        if self._reconnecting:
            return
        self._reconnecting = True
        self._emit_state(STATE_RECONNECTING)
        try:
            # 古いクライアントの後始末(通知購読の解除・切断)。失敗は無視。
            old = self._client
            self._client = None
            if old is not None:
                try:
                    await old.disconnect()
                except Exception:  # noqa: BLE001
                    pass

            import time as _time
            budget = self._reconnect_budget()
            deadline = _time.monotonic() + budget
            self._log(f"[再接続] 焙煎が終わるまで粘ります(最長 {budget / 60:.0f}分)。")
            attempt = 0
            while _time.monotonic() < deadline:
                if self._user_initiated_disconnect:
                    return
                attempt += 1
                left = deadline - _time.monotonic()
                self._log(f"[再接続] 試行 {attempt}(残り {left / 60:.1f}分) ...")
                try:
                    if await self._scan_and_connect(timeout=6.0):
                        self._log("[再接続] 成功しました。ステータス・テレメトリの受信を再開します。")
                        # 再接続直後は、直前に判明していた推定ステータスを再通知して
                        # UI側の接続表示を「接続済み」に戻す。
                        self._emit_state(STATE_PAIRED)
                        if self.on_state and self._phase_estimator.phase != "awaiting_receive":
                            # 進行中フェーズの表示を戻す(あくまで推定の再掲)。
                            self._emit_state(PHASE_CODE_STATES.get(self._last_phase_code, STATE_ROASTING))
                        return
                except Exception as e:  # noqa: BLE001
                    self._log(f"[再接続] 試行 {attempt} でエラー: {e!r}")
                # 何度も失敗するときは間隔を空ける(スキャン自体に6秒かかるので、
                # 短い間隔で叩き続けても回数が増えるだけで当たりやすくならない)。
                delay = min(RECONNECT_DELAY + (attempt // 4) * RECONNECT_DELAY,
                            RECONNECT_DELAY_MAX)
                await asyncio.sleep(delay)

            self._log(f"[再接続] {budget / 60:.0f}分試みましたが再接続できませんでした。")
            self._emit_state(STATE_DISCONNECTED)
        finally:
            self._reconnecting = False

    def _reconnect_budget(self) -> float:
        """焙煎中の再接続を、どれだけの間続けるか(秒)。

        焙煎は機械側で勝手に進むので、粘る意味があるのは「焙煎が終わって
        冷却も済むまで」。プロファイルが分かっていればその予定から見積もり、
        分からなければ最低限の時間だけ粘る。
        """
        if self._planned_roast_sec:
            cooldown = (self._planned_cooldown_sec
                        if self._planned_cooldown_sec and self._planned_cooldown_sec > 0
                        else DEFAULT_COOLDOWN_GRACE_SECONDS)
            budget = self._planned_roast_sec + cooldown + RECONNECT_MARGIN_SECONDS
        else:
            budget = RECONNECT_BUDGET_MIN
        return max(RECONNECT_BUDGET_MIN, min(budget, RECONNECT_BUDGET_MAX))

    async def disconnect(self) -> None:
        import traceback
        caller = "".join(traceback.format_stack(limit=4)[:-1]).replace("\n", " | ")
        self._log(f"[診断] disconnect()が呼び出されました。呼び出し元: {caller}")

        # ユーザー・アプリ都合の明示的な切断。以降 _on_disconnected で自動再接続しない。
        self._user_initiated_disconnect = True
        self._reconnecting = False
        self._target_device = None

        if self._client is not None:
            try:
                await self._client.stop_notify(CHAR_STATUS_NOTIFY)
            except Exception:  # noqa: BLE001
                pass
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._connected = False
        self._log("切断しました。")
        self._emit_state(STATE_DISCONNECTED)

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def send_profile(self, profile: RoastProfile) -> None:
        if self._client is None:
            raise RuntimeError("先にconnect()してください")

        self._acked_uuid_echo = False
        self._last_phase_code = None
        self._last_heartbeat_t = 0.0
        self._user_initiated_disconnect = False  # 新しい焙煎。以降の予期しない切断は再接続対象
        self._profile_uuid_ascii = (profile.raw.get("UUID") or "").encode("ascii")
        self._phase_estimator.reset()
        self._error_active = False
        self._error_min_bt = None
        self._last_bt = None
        self._cooling_target_reached = False
        # cooldownPointの時刻(roastPoints最終点からの経過秒)を、冷却予定時間として覚えておく。
        # _on_disconnected()で、「焙煎完了・冷却中」中の切断が容器交換にしては早すぎないかの
        # 判定に使う(README「現状分かっていること」参照: roastPoints最終点=焙煎終了・冷却開始、
        # cooldownPoint=冷却完了予定時刻)。
        self._planned_cooldown_sec = None
        if profile.cooldown.x and profile.roast.x:
            self._planned_cooldown_sec = profile.cooldown.x[0] - profile.roast.x[-1]
        # 切断からの再接続を、いつまで粘るかの見積もりに使う。
        self._planned_roast_sec = profile.roast.x[-1] if profile.roast.x else None

        sequence = build_write_sequence(profile, self._token)
        self._emit_state(STATE_SENDING)
        self._log(f"=== プロファイル送信開始 ({profile.name}) ===")
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        try:
            async with self._write_lock:
                for i, payload in enumerate(sequence):
                    await self._client.write_gatt_char(CHAR_COMMAND_WRITE, payload, response=True)
                    await asyncio.sleep(0.15)
        except Exception as e:  # noqa: BLE001
            self._connected = False
            self._log(f"プロファイル送信中にエラー(恐らく機械側から切断されました): {e!r}")
            self._emit_state(STATE_DISCONNECTED)
            return
        self._log("=== プロファイル送信完了。機械からの応答待ち ===")

        # 2026-07訂正: 以前はここで固定タイマー式のハートビート送信タスクを
        # 開始していたが、_handle_notify側の「通知を受け取るたびに反応する」
        # ハートビート応答と二重に書き込みが走ってしまい、通信の衝突を
        # 招いていた可能性が高い(送信完了後、数秒で切断される症状と一致)。
        # 旧トークン時代に実際に予熱まで成功した ble_write_reactive_test.py も
        # 固定タイマーは使わず、Notify受信への反応だけで動作していたため、
        # ここでのタイマー起動はやめ、_handle_notify側の反応だけに一本化する。

    def _on_notify(self, sender, data: bytes) -> None:
        """bleakから呼ばれる同期コールバック。

        呼び出されるスレッドがイベントループのスレッドと異なる場合があり、
        ここで直接 asyncio.create_task() を呼ぶと(uvicorn配下では特に)
        例外が握りつぶされて処理が一切実行されない、という不具合があった。
        そのため、必ず run_coroutine_threadsafe で明示的にloopへ橋渡しする。
        """
        try:
            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(self._handle_notify(data), self._loop)
            else:
                self._log("警告: イベントループが未設定のため通知を処理できません")
        except Exception as e:  # noqa: BLE001
            self._log(f"notify処理のスケジューリングに失敗: {e}")

    def _cooling_done_allowed(self, dt_in_roast_done: float) -> bool:
        """冷却完了の通知を受け付けてよいか(吸入温度が下がりきったか)。

        焙煎機は吸入温度60℃を閾値に冷却完了を判定している。温度は1.5秒おきに
        届くが、通知と同時に届くとは限らないため、一度でも下がりきっていれば
        (_cooling_target_reached)受け付ける。切断中に下がりきった場合に備えて、
        いまの温度も見る。

        温度が一度も取れていないときだけ、旧来の「冷却に入って30秒」で判断する。
        この経路に頼るのは通信が異常なときだけで、通常は温度で決まる。
        """
        if self._cooling_target_reached:
            return True
        if self._last_bt is not None:
            return self._last_bt <= COOLING_DONE_INTAKE_TEMP
        return dt_in_roast_done >= COOLING_DONE_MIN_SECONDS

    async def _handle_notify(self, data: bytes) -> None:
        import time as _time

        self._log(f"[受信] notify len={len(data)} hex={data.hex()}")

        try:
            # 「13 00」+トークンで始まる通知 = 物理スイッチ操作等の確認要求と推定。
            # 本物のアプリの通信をピンポイントで捉えたところ(2026-07)、受信内容の
            # suffix(e2 27等)やtail(71等)がどんな値でも、応答は常に
            # 「13 00 + トークン + 62 00」→「ca」という固定パターンだった。
            # (それ以前に試した「オウム返し」「60 00/4d(旧トークン成功パターン)」は
            #  いずれも不正解と判明している)
            header_prefix = b"\x13\x00" + self._token
            if data.startswith(header_prefix):
                self._pending_echo_header = data
                self._log("[受信] 確認要求ヘッダを検出。後続の1バイトを待ちます")
                return

            if self._pending_echo_header is not None and len(data) <= 2:
                self._pending_echo_header = None
                self._log("[応答] 確認要求に固定応答(62 00 / ca)を送信します...")
                fixed_ack_header = b"\x13\x00" + self._token + b"\x62\x00"
                await self._ack(fixed_ack_header, b"\xca")
                self._log("[応答] 確認要求への応答を送信しました")
                # 2026-07再訂正: 当初「この確認要求=排出完了」と推定していたが、
                # ユーザー様提供の実機ログで、冷却中にもこの同じパターンが約60秒間隔で
                # 周期的に届くことが判明した(排出完了とは無関係な誤検出)。
                # 冷却完了(STATE_COOLING_DONE、下の全ゼロ通知で検出)より前に届いた場合は
                # 排出完了ではないので無視し、冷却完了を確認済みの状態でのみ
                # 排出完了とみなす(容器交換→スイッチ操作は冷却完了後にしか行えないため)。
                if self._phase_estimator.phase in ("cooling_done", "discharging"):
                    t_now = _time.monotonic() - (self._start_time or 0)
                    self._emit_state(STATE_DISCHARGE_DONE)
                    self._phase_estimator.mark_confirmed_phase("done", t=t_now)
                else:
                    self._log("[受信] 確認要求(冷却完了前のため、排出完了ではなく周期通知とみなして無視します)")
                return

            # 「ほぼ全て0」の特殊な通知(18〜19byte、先頭1byteの連番と末尾1byte以外は
            # 全部0x00) = 冷却完了・容器交換要求のタイミングに対応すると、
            # ユーザー様の実機確認で判明した(2026-07)。「焙煎完了・冷却中」の間にだけ
            # 判定する(他のタイミングでの誤検出を避けるため)。
            #
            # 2026-09訂正: この通知は冷却専用の信号ではなかった。実機ログで、同じ形の
            # 通知が焙煎中(186℃)にも冷却開始直後(247℃)にも届いている。以前は
            # 「焙煎完了・冷却中に入って30秒以上」だけを条件にしていたため、まだ
            # 200℃近くある時点の通知で冷却完了としてしまい、通知が早く何度も出ていた。
            # 焙煎機自身は吸入温度60℃を閾値に冷却完了を判定している(COOLING_DONE_INTAKE_TEMP)。
            # それより前の「冷却完了に見える情報」は、すべて無視する。
            t_now_check = _time.monotonic() - (self._start_time or 0)
            dt_in_roast_done = t_now_check - (self._phase_estimator.phase_start_t or 0)
            if (
                self._phase_estimator.phase == "roast_done"
                and len(data) in (18, 19)
                and data[1:-1].count(0) >= len(data) - 3
            ):
                if not self._cooling_done_allowed(dt_in_roast_done):
                    self._log(
                        "[受信] 冷却完了に似た通知を検出しましたが、吸入温度が"
                        f"{self._last_bt if self._last_bt is not None else '不明'}℃で、"
                        f"焙煎機が冷却完了とする{COOLING_DONE_INTAKE_TEMP:.0f}℃まで"
                        "下がっていないため無視します"
                    )
                    return
                self._log("[受信] 冷却完了・容器交換要求の通知を検出")
                self._emit_state(STATE_COOLING_DONE)
                t_now = _time.monotonic() - (self._start_time or 0)
                self._phase_estimator.mark_confirmed_phase("cooling_done", t=t_now)
                return

            # フェーズコード検出: [prefix(2)] + token(16) + [suffix(2)] という20byte通知で、
            # suffixの先頭が0xe0/0xe4/0xe5のものは、末尾1バイトが状態遷移(0x20=待機中,
            # 0x21=予熱中, 0x22=投入操作中,0x23=焙煎中,...)を表している。温度のしきい値
            # より確実な手がかりなので、これを検出できた場合は温度ベースの推定より優先する。
            # 2026-07追記: 0xe5は、ガラス容器を外す・豆投入容器を開ける等でエラーが
            # 発生した際に確認した(末尾1バイトはエラー発生時点の現在フェーズと同じ値
            # だったため、「現在のフェーズ + エラー中」を重ねて表しているsuffixと推定)。
            if len(data) == 20 and data[2:18] == self._token and data[18] in (0xE0, 0xE4, 0xE5):
                phase_code = data[19]
                if phase_code != self._last_phase_code:
                    self._last_phase_code = phase_code
                    mapped_state = PHASE_CODE_STATES.get(phase_code)
                    if mapped_state:
                        self._emit_state(mapped_state)
                        self._log(f"[フェーズコード] 0x{phase_code:02x} -> {mapped_state}")
                        # 正常なフェーズ遷移が確定したので、古い容器エラー状態が
                        # 残っていればここで解消しておく(念のための保険)。
                        self._error_active = False
                        t_now = _time.monotonic() - (self._start_time or 0)
                        if mapped_state == STATE_ROASTING:
                            self._phase_estimator.mark_confirmed_phase("roasting", t=t_now)
                        elif mapped_state == STATE_PREHEATING:
                            self._phase_estimator.mark_confirmed_phase("preheating", t=t_now)
                        elif mapped_state == STATE_FORCED_COOLING:
                            # 温度ベースのフォールバック推定(焙煎完了→排出中)が誤って
                            # 割り込まないよう、ここで直接「排出中」相当に進めておく
                            self._phase_estimator.mark_confirmed_phase("discharging", t=t_now)
                        elif mapped_state == STATE_ROAST_DONE:
                            self._phase_estimator.mark_confirmed_phase("roast_done", t=t_now)
                    else:
                        self._log(f"[フェーズコード] 未対応のコードを検出: 0x{phase_code:02x}")

            # UUIDエコー確認への応答
            if not self._acked_uuid_echo and self._profile_uuid_ascii and self._profile_uuid_ascii in data:
                self._acked_uuid_echo = True
                self._emit_state(STATE_RECEIVED)
                self._phase_estimator.mark_received()
                self._log("[応答] UUID確認応答を送信します...")
                ack_header = b"\x13\x00" + self._token + b"\x60\x00"
                # 2026-07訂正: 旧トークン時代のtail("4d")のままだったが、
                # 現行トークンでの閉じ応答(close_tail)と同じ"c8"に統一する
                await self._ack(ack_header, b"\xc8")
                self._log("[応答] UUID確認応答を送信しました")
                return

            # 容器エラー通知: `01`または`00` + `U`(0x55) + 4桁のASCII数字コード +
            # 現在フェーズのコード + `00 00` + チェックサムらしき1byte、という形の
            # 10byte通知(例: "U5100"・"U0220"・"U9120")。当初は電源投入直後の名乗り出
            # 通知ではないかと推測し受信確認ACKを試験的に返していたが、ユーザー様の
            # 実機確認により、実際にはガラス容器・豆投入容器が正しくセットされていない
            # 場合に機械側が送ってくるエラー通知だったと判明した。これにACKを返すのは
            # 実際のエラーを握り潰してしまう誤った挙動のため、ACKは送らずログでの
            # 可視化・状態表示のみ行う。
            # 2026-07追記: 数字コードはトリガーした容器によって変わる。ガラス容器を
            # 外した際・豆投入容器を開けた際、それぞれ2回ずつ再現し"0220"/"9120"で
            # 一貫していることを確認できたため、CONTAINER_ERROR_CODE_STATESで
            # 具体的な文言に振り分ける(対応表に無いコードは汎用文言にフォールバック)。
            # また、以前はUUID確認前(`not self._acked_uuid_echo`)の間しか見ていなかったが、
            # 実際には予熱・焙煎中(UUID確認後)にも同じ形で届くことが判明した。
            # このケースを見逃すと、後段のテレメトリ抽出(9/10byte通知として扱われ、
            # data[3:5]が誤って流入空気温度と解釈される)に巻き込まれ、グラフに誤った
            # 値が描画されてしまうため、`_acked_uuid_echo`の状態に関わらず検出する。
            if len(data) == 10 and data[1] == 0x55:
                code = data[2:6].decode("ascii", errors="replace")
                if code == DISCHARGE_PROMPT_CODE:
                    self._log(f"[受信] 排出待ち通知を検出(コード: U{code}) -> {STATE_DISCHARGE_PROMPT}")
                    self._emit_state(STATE_DISCHARGE_PROMPT)
                else:
                    state = CONTAINER_ERROR_CODE_STATES.get(code, STATE_CONTAINER_ERROR)
                    self._log(f"[受信] 容器エラー通知を検出(コード: U{code}) -> {state}")
                    if not self._error_active:
                        # 新規のエラー発生。復帰判定用に、以降の最低温度を追いかけ直す。
                        self._error_active = True
                        self._error_min_bt = None
                    self._emit_state(state)
            elif not self._acked_uuid_echo and len(data) >= 4 and data[0] == 0x01:
                self._log(
                    f"[受信] 未確認の通知を検出(容器が正しくセットされていない場合に"
                    f"繰り返し送られてくることがあります。容器のセット状態をご確認ください): {data.hex()}"
                )
                if not self._error_active:
                    self._error_active = True
                    self._error_min_bt = None
                self._emit_state(STATE_CONTAINER_ERROR)

            # 通常のセンサー値通知への応答(本来のハートビート。固定タイマーではなく
            # Notifyを受け取るたびに反応する方式。旧トークン時代に実際に予熱成功した
            # ble_write_reactive_test.py と同じ設計)。
            # ただし、Notifyは約300msごとに5フラグメントのバーストで届くため、
            # 全フラグメントに応答すると書き込みが詰まって切断の一因になる。
            # HEARTBEAT_MIN_INTERVAL 未満の間隔での連投は間引く(バーストの最初の1回だけ応答)。
            if self._acked_uuid_echo and len(data) >= 8:
                now_mono = _time.monotonic()
                if now_mono - self._last_heartbeat_t >= HEARTBEAT_MIN_INTERVAL:
                    self._last_heartbeat_t = now_mono
                    heartbeat_header = b"\x13\x00" + self._token + b"\x64\x00"
                    # 2026-07訂正: 旧トークン時代のtail("51")のままだったが、
                    # 現行トークンでの正しい値"cc"に修正
                    await self._ack(heartbeat_header, b"\xcc")
                    self._log("[応答] ハートビート応答を送信しました")

            # テレメトリの抽出(実機解析で確認した「val1」= 4-5バイト目のLE16bit値)。
            # 容器エラー通知(10byte、data[1]==0x55)は形が同じ9/10byteに該当するが
            # 温度データではないため、誤って流入空気温度として抽出・描画しないよう除外する。
            bt_value = None
            fan_value = None
            elapsed_sec_value = None
            if (len(data) == 9 or len(data) == 10) and not (len(data) == 10 and data[1] == 0x55):
                try:
                    bt_value = int.from_bytes(data[3:5], "little")
                except Exception:  # noqa: BLE001
                    bt_value = None
                # 実測風量(2026-07発見・訂正): 6・7バイト目(0始まりでindex5・6、
                # 温度の直後)を合わせたLE16bit値(index5が下位バイト)が実測風量に
                # 相当する。当初はindex5(1バイト)のみを風量値、index6を別モードの
                # フラグと解釈していたが、50/80/90/100%を段階的に確認したところ、
                # 90%→100%の境界で index5=255,index6=1(0x01FF) → index5=0,index6=2
                # (0x0200)という、ごく普通の16bit値の繰り上がりが観測された。
                # index6は独立したフラグではなく、単にindex5だけでは表現しきれない
                # 範囲(風量90%超)に達した際の上位バイトだった。100%付近で温度上昇が
                # 鈍る現象も、特別な制御モードではなく「実際に風量が最大近くまで
                # 上がり熱が奪われる」という素直な物理現象と考えられる。
                # 実機ログ(519秒の実焙煎+50/80/90/100%の段階テスト、計326点の
                # 定常状態)で線形回帰し、raw16 ≈ 4.70×風量% + 89.0
                # (残差の標準偏差 約4.2)という式で高精度に一致することを確認した。
                # 起動直後・状態遷移直後の数百ms間はファンの物理的な追従遅れで
                # この式からずれることがある(モーターの立ち上がり遅れ)。
                try:
                    fan_raw16 = data[5] + 256 * data[6]
                    fan_value = max(0.0, min(100.0, (fan_raw16 - 89.0) / 4.70))
                except Exception:  # noqa: BLE001
                    fan_value = None
                # 焙煎開始からの経過秒数(2026-07発見): 8・9バイト目(0始まりで
                # index7:8)のLE16bit値。焙煎中(0x23)に入るまでは0のまま、入った
                # 瞬間から1秒刻みで正確に増える。TelemetrySample.elapsed_sec参照。
                try:
                    elapsed_sec_value = int.from_bytes(data[7:9], "little")
                except Exception:  # noqa: BLE001
                    elapsed_sec_value = None

            t = _time.monotonic() - (self._start_time or 0)

            if self.on_telemetry:
                self.on_telemetry(TelemetrySample(
                    t=t, bt=bt_value, fan=fan_value, elapsed_sec=elapsed_sec_value, raw_hex=data.hex(),
                ))

            if bt_value is not None:
                # 冷却完了の判定に使うので、確認応答の前後を問わず覚えておく。
                self._last_bt = bt_value
                if (self._phase_estimator.phase == "roast_done"
                        and bt_value <= COOLING_DONE_INTAKE_TEMP):
                    self._cooling_target_reached = True

            if bt_value is not None and self._acked_uuid_echo:
                # 容器エラーからの復帰判定(2026-07、ユーザー様のご提案): エラー通知
                # 自体には復帰の合図が無いため、焙煎中の温度がエラー中の最低値から
                # 一定以上再び上昇したことをもって復帰とみなす。予熱中は投入前の
                # 自然な停滞・微減があり誤判定しやすいため、焙煎中に限定する。
                if self._error_active and self._phase_estimator.phase == "roasting":
                    if self._error_min_bt is None or bt_value < self._error_min_bt:
                        self._error_min_bt = bt_value
                    elif bt_value >= self._error_min_bt + ERROR_RECOVERY_RISE:
                        self._error_active = False
                        recovered_state = PHASE_CODE_STATES.get(self._last_phase_code, STATE_ROASTING)
                        self._log(f"[受信] 温度が再上昇したため、エラーから復帰したとみなします({recovered_state})")
                        self._emit_state(recovered_state)

                new_state = self._phase_estimator.update(t, bt_value)
                if new_state:
                    self._emit_state(new_state)
                    self._log(f"(推定)状態が変化しました: {new_state}")
        except Exception as e:  # noqa: BLE001
            self._log(f"notify処理でエラー: {e!r}")

    async def _ack(self, header: bytes, tail: bytes) -> None:
        """command_writeへの書き込みは、複数のNotifyへの応答が同時多発すると
        競合してキューが詰まり、機械側の処理を混乱させる不具合が実機で確認された
        (大量の書き込み失敗が短時間に連続して発生していた)。
        ロックで排他制御し、必ず「header書き込み→tail書き込み」の対が
        割り込まれずに完了するようにする。

        2026-07追記: ロック導入後も、書き込みが数十秒〜1分以上ハングし、
        その間に溜まった大量の応答が、ハング解消の瞬間に一斉に失敗する事象が
        実機で確認された。1回の書き込みにタイムアウトを設けるだけでなく、
        ロックの取得自体にもタイムアウトを設け、既に長時間待たされている
        (=内容が古くなっている)応答は諦めて捨て、後続の新しい応答から
        詰まりを解消できるようにする。
        """
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        try:
            await asyncio.wait_for(self._write_lock.acquire(), timeout=5.0)
        except asyncio.TimeoutError:
            self._log("応答がロック待ちで詰まっていたため、この応答は諦めます。")
            return
        try:
            try:
                await asyncio.wait_for(
                    self._client.write_gatt_char(CHAR_COMMAND_WRITE, header, response=True),
                    timeout=3.0,
                )
                await asyncio.sleep(0.05)
                await asyncio.wait_for(
                    self._client.write_gatt_char(CHAR_COMMAND_WRITE, tail, response=True),
                    timeout=3.0,
                )
            except asyncio.TimeoutError:
                self._log("応答の送信がタイムアウトしました(3秒)。次の応答へ進みます。")
            except Exception as e:  # noqa: BLE001
                self._log(f"応答の送信に失敗: {e}")
        finally:
            self._write_lock.release()
