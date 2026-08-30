# ============================================================
# Roast Studio
# tests/test_ble_session.py
# ------------------------------------------------------------
# RoasterSession の接続安定性まわり(接続リトライ・焙煎中の自動再接続・
# ハートビート間引き)を、bleakをモックして実機なしで検証する。
# 実際のBLE通信プロトコルの正しさ(codec等)は別テスト・実機で担保する。
# ============================================================
import asyncio
import sys
import types

import pytest

from roastlib.ble import session as S
from roastlib.ble.session import RoasterSession


# ------------------------------------------------------------
# bleak のフェイク実装(BleakScanner / BleakClient)
# ------------------------------------------------------------
class FakeDevice:
    def __init__(self, address="AA:BB:CC:DD:EE:FF", name="Smart Roaster AE-NR01"):
        self.address = address
        self.name = name


class FakeAdv:
    service_uuids = [S.MAIN_SERVICE_UUID]


class FakeClient:
    """connect/start_notify/write/disconnect を模したフェイク。
    fail_connect_times: 最初の数回の connect() を失敗させる。"""

    instances = []

    def __init__(self, target, disconnected_callback=None):
        self.target = target
        self.disconnected_callback = disconnected_callback
        self._connected = False
        self.notify_started = False
        self.writes = []
        FakeClient.instances.append(self)

    async def connect(self):
        if FakeClient.fail_connect_times > 0:
            FakeClient.fail_connect_times -= 1
            raise RuntimeError("simulated connect failure")
        self._connected = True

    @property
    def is_connected(self):
        return self._connected

    async def start_notify(self, uuid, cb):
        self.notify_started = True
        self._notify_cb = cb

    async def stop_notify(self, uuid):
        pass

    async def write_gatt_char(self, uuid, data, response=True):
        self.writes.append(bytes(data))

    async def disconnect(self):
        self._connected = False


def _install_fake_bleak(monkeypatch, scanner_returns=True):
    async def fake_discover(timeout=10.0, return_adv=True):
        if not scanner_returns:
            return {}
        return {"k": (FakeDevice(), FakeAdv())}

    fake = types.ModuleType("bleak")
    fake.BleakClient = FakeClient
    fake.BleakScanner = types.SimpleNamespace(discover=staticmethod(fake_discover))
    monkeypatch.setitem(sys.modules, "bleak", fake)


@pytest.fixture(autouse=True)
def _reset():
    FakeClient.instances = []
    FakeClient.fail_connect_times = 0
    yield


# ------------------------------------------------------------
# 接続リトライ
# ------------------------------------------------------------
def test_connect_retries_and_succeeds(monkeypatch):
    _install_fake_bleak(monkeypatch)
    monkeypatch.setattr(S, "CONNECT_RETRY_DELAY", 0)  # テストを速くする
    FakeClient.fail_connect_times = 2  # 最初の2回失敗、3回目で成功

    states = []
    sess = RoasterSession(on_state=states.append)
    ok = asyncio.run(sess.connect(timeout=0.01))
    assert ok is True
    assert sess.is_connected is True
    assert S.STATE_PAIRED in states


def test_connect_gives_up_after_max_attempts(monkeypatch):
    _install_fake_bleak(monkeypatch)
    monkeypatch.setattr(S, "CONNECT_RETRY_DELAY", 0)
    FakeClient.fail_connect_times = 99  # 常に失敗

    states = []
    sess = RoasterSession(on_state=states.append)
    ok = asyncio.run(sess.connect(timeout=0.01))
    assert ok is False
    assert states[-1] == S.STATE_DISCONNECTED


def test_connect_fails_when_device_not_found(monkeypatch):
    _install_fake_bleak(monkeypatch, scanner_returns=False)
    monkeypatch.setattr(S, "CONNECT_RETRY_DELAY", 0)
    sess = RoasterSession()
    ok = asyncio.run(sess.connect(timeout=0.01))
    assert ok is False


# ------------------------------------------------------------
# 焙煎中の自動再接続
# ------------------------------------------------------------
def test_unexpected_disconnect_while_roasting_triggers_reconnect(monkeypatch):
    _install_fake_bleak(monkeypatch)
    monkeypatch.setattr(S, "CONNECT_RETRY_DELAY", 0)
    monkeypatch.setattr(S, "RECONNECT_DELAY", 0)

    async def scenario():
        states = []
        sess = RoasterSession(on_state=states.append)
        assert await sess.connect(timeout=0.01)
        # 焙煎中フェーズに進めておく
        sess._phase_estimator.phase = "roasting"
        # 切断イベント(機械/OS都合)を発生させる
        sess._on_disconnected(sess._client)
        # 再接続タスクが走る余地を与える
        await asyncio.sleep(0.05)
        return states, sess

    states, sess = asyncio.run(scenario())
    assert S.STATE_RECONNECTING in states
    assert sess.is_connected is True  # 再接続に成功している


def test_user_disconnect_does_not_reconnect(monkeypatch):
    _install_fake_bleak(monkeypatch)
    monkeypatch.setattr(S, "CONNECT_RETRY_DELAY", 0)

    async def scenario():
        states = []
        sess = RoasterSession(on_state=states.append)
        assert await sess.connect(timeout=0.01)
        sess._phase_estimator.phase = "roasting"
        await sess.disconnect()          # ユーザー都合の切断
        sess._on_disconnected(sess._client)  # bleakからの切断コールバック
        await asyncio.sleep(0.05)
        return states

    states = asyncio.run(scenario())
    assert S.STATE_RECONNECTING not in states  # 自動再接続しない


def test_roast_done_disconnect_is_expected_no_reconnect(monkeypatch):
    _install_fake_bleak(monkeypatch)

    async def scenario():
        states = []
        sess = RoasterSession(on_state=states.append)
        assert await sess.connect(timeout=0.01)
        sess._phase_estimator.phase = "roast_done"  # 排出モード移行=想定内の切断
        # 冷却予定時間(猶予)をとっくに過ぎている状態を模す(=容器交換タイミングとして妥当)。
        sess._phase_estimator.phase_start_t = -10_000
        sess._on_disconnected(sess._client)
        await asyncio.sleep(0.05)
        return states

    states = asyncio.run(scenario())
    assert S.STATE_RECONNECTING not in states
    assert states[-1] == S.STATE_DISCONNECTED


def test_roast_done_disconnect_too_early_triggers_reconnect(monkeypatch):
    # 2026-07: 実機ログで、「焙煎完了・冷却中」に入った直後(容器交換が物理的に
    # 起こり得ないタイミング)に切断される事例を確認した。冷却予定時間内であれば、
    # 容器交換にしては早すぎるとみなし、焙煎中と同様に自動再接続を試みるべき。
    _install_fake_bleak(monkeypatch)
    monkeypatch.setattr(S, "CONNECT_RETRY_DELAY", 0)
    monkeypatch.setattr(S, "RECONNECT_DELAY", 0)

    async def scenario():
        states = []
        sess = RoasterSession(on_state=states.append)
        assert await sess.connect(timeout=0.01)
        sess._phase_estimator.phase = "roast_done"
        sess._phase_estimator.phase_start_t = None  # ちょうど遷移した直後(経過0秒)を模す
        sess._planned_cooldown_sec = 550.0  # プロファイルのcooldownPointから算出した想定値
        sess._on_disconnected(sess._client)
        await asyncio.sleep(0.05)
        return states, sess

    states, sess = asyncio.run(scenario())
    assert S.STATE_RECONNECTING in states
    assert sess.is_connected is True


# ------------------------------------------------------------
# ハートビート間引き
# ------------------------------------------------------------
def test_heartbeat_is_throttled_within_burst(monkeypatch):
    _install_fake_bleak(monkeypatch)
    monkeypatch.setattr(S, "HEARTBEAT_MIN_INTERVAL", 0.28)

    async def scenario():
        sess = RoasterSession()
        assert await sess.connect(timeout=0.01)
        sess._acked_uuid_echo = True  # ハンドシェイク済みとみなす
        client = sess._client
        client.writes.clear()
        # バースト(短時間に5フラグメント)を模して、10バイトのNotifyを連続投入
        for _ in range(5):
            await sess._handle_notify(bytes([0x00] * 10))
        return client.writes

    writes = asyncio.run(scenario())
    # ハートビート応答は header+tail の2書き込み。間引きが効いていれば、
    # バースト内では1回分(2書き込み)だけになるはず(5回分=10書き込みではない)。
    assert len(writes) == 2, f"想定: 2書き込み(1ハートビート), 実際: {len(writes)}"


def test_heartbeat_not_throttled_when_interval_zero(monkeypatch):
    _install_fake_bleak(monkeypatch)
    monkeypatch.setattr(S, "HEARTBEAT_MIN_INTERVAL", 0.0)  # 従来どおり全応答

    async def scenario():
        sess = RoasterSession()
        assert await sess.connect(timeout=0.01)
        sess._acked_uuid_echo = True
        client = sess._client
        client.writes.clear()
        for _ in range(5):
            await sess._handle_notify(bytes([0x00] * 10))
        return client.writes

    writes = asyncio.run(scenario())
    # 5フラグメント × 2書き込み = 10
    assert len(writes) == 10


# ------------------------------------------------------------
# 実機エラーログ(2026-07、ガラス容器/豆投入容器を意図的に外す・開ける再現テスト)から
# 得られた実バイト列を使った回帰テスト。トークンはログ実測値と同じデフォルト値
# (a6ab282f4108003225d944e5500b2090)を使うので、ログの生hexをそのまま貼れる。
# ------------------------------------------------------------
def test_container_error_detected_after_uuid_ack_without_bogus_telemetry(monkeypatch):
    """実機ログ: 予熱中(UUID確認後)にガラス容器を外した際の実バイト列
    (01 55 30 32 32 30 21 00 00 b2 = "U0220")。以前はUUID確認前しか見ておらず、
    このケースは無視されて誤った流入空気温度(data[3:5]由来)としてグラフに描画されて
    いた。修正後は、容器エラーとして検出され、bt_value(誤温度)は出さない。"""
    _install_fake_bleak(monkeypatch)

    async def scenario():
        states = []
        telemetry = []
        sess = RoasterSession(on_state=states.append, on_telemetry=telemetry.append)
        assert await sess.connect(timeout=0.01)
        sess._acked_uuid_echo = True  # UUID確認済み(予熱中)を再現
        await sess._handle_notify(bytes.fromhex("015530323230210000b2"))
        return states, telemetry

    states, telemetry = asyncio.run(scenario())
    assert S.STATE_GLASS_CONTAINER_ERROR in states
    assert len(telemetry) == 1
    assert telemetry[0].bt is None  # 誤った温度(0x3232=12850)が出ていないこと


def test_container_error_codes_map_to_specific_messages(monkeypatch):
    """2026-07、実機で2回ずつ再現し一貫していた対応(コード"0220"=ガラス容器、
    "9120"=豆投入容器)を、具体的な案内文言に反映していること。未知のコードは
    汎用の文言にフォールバックすること。"""
    _install_fake_bleak(monkeypatch)

    async def scenario():
        states = []
        sess = RoasterSession(on_state=states.append)
        assert await sess.connect(timeout=0.01)
        sess._acked_uuid_echo = True
        await sess._handle_notify(bytes.fromhex("015530323230210000b2"))  # U0220
        await sess._handle_notify(bytes.fromhex("015539313230210000ba"))  # U9120
        await sess._handle_notify(bytes.fromhex("015539393939210000ba"))  # 未知のコード U9999
        return states

    states = asyncio.run(scenario())
    assert S.STATE_GLASS_CONTAINER_ERROR in states
    assert S.STATE_BEAN_CHARGE_ERROR in states
    assert S.STATE_CONTAINER_ERROR in states  # 未知コードは汎用文言


def test_discharge_prompt_code_is_not_treated_as_error(monkeypatch):
    """実機ログ(2026-07、強制冷却完了後にガラス容器を外して豆を回収するよう促す
    場面)から得た実バイト列(01 55 31 32 30 30 27 00 00 bd = "U1200")。見た目は
    他の容器エラー通知と同じ形だが、実際には正常な操作案内であり機械の異常では
    ないため、他の容器エラー(0220/9120)とは違いSTATE_CONTAINER_ERROR系には
    分類されず、_error_active(温度上昇での復帰待ち)も立たないこと。"""
    _install_fake_bleak(monkeypatch)

    async def scenario():
        states = []
        sess = RoasterSession(on_state=states.append)
        assert await sess.connect(timeout=0.01)
        sess._acked_uuid_echo = True
        await sess._handle_notify(bytes.fromhex("015531323030270000bd"))  # U1200
        return states, sess

    states, sess = asyncio.run(scenario())
    assert S.STATE_DISCHARGE_PROMPT in states
    assert S.STATE_CONTAINER_ERROR not in states
    assert S.STATE_GLASS_CONTAINER_ERROR not in states
    assert S.STATE_BEAN_CHARGE_ERROR not in states
    assert sess._error_active is False


def test_phase_code_0x27_maps_to_discharge_prompt(monkeypatch):
    """フェーズコード0x27(20byte通知)も、上と同じ「排出待ち」状態に対応すること
    (実機ログで、強制冷却完了直後にU1200通知と同じタイミングで出現した)。"""
    _install_fake_bleak(monkeypatch)

    async def scenario():
        states = []
        sess = RoasterSession(on_state=states.append)
        assert await sess.connect(timeout=0.01)
        sess._acked_uuid_echo = True
        await sess._handle_notify(bytes.fromhex("1c00a6ab282f4108003225d944e5500b2090e527"))
        return states

    states = asyncio.run(scenario())
    assert S.STATE_DISCHARGE_PROMPT in states


def test_byte6_equal_2_during_normal_high_fan_does_not_report_forced_cooling(monkeypatch):
    """2026-07訂正の回帰確認: 当初、テレメトリの7バイト目(index6)が2になった
    瞬間をSTATE_FORCED_COOLINGの先行検知に使っていたが、これは強制冷却
    スイッチを押した時専用ではなく、プロファイルの風量指定が100%近くに
    達した通常運転時にも(スイッチを押していなくても)同様に現れることが、
    別の実機ログ(強制冷却スイッチを一切押していない、容器エラーのみの
    テスト)との比較で判明した。実バイト列
    2d 00 23 78 00 2e 02 30 00 a0(byte6=2だが、フェーズコードは0x23=焙煎中の
    まま)を投入しても、STATE_FORCED_COOLINGを誤って通知しないこと。"""
    _install_fake_bleak(monkeypatch)

    async def scenario():
        states = []
        sess = RoasterSession(on_state=states.append)
        assert await sess.connect(timeout=0.01)
        sess._acked_uuid_echo = True
        await sess._handle_notify(bytes.fromhex("2d002378002e023000a0"))
        return states

    states = asyncio.run(scenario())
    assert S.STATE_FORCED_COOLING not in states


def test_telemetry_extracts_fan_percent_from_byte5_and_6(monkeypatch):
    """実機ログ(2026-07、519秒の実焙煎・風量70%→55%へ一直線に変化するプロファイル)
    から、通知パケットの6・7バイト目(0始まりでindex5・6、流入空気温度の直後)を
    合わせたLE16bit値(index5が下位バイト)が実測風量であることを突き止めた
    (このログではindex6は終始1で一定だったため、index5だけの回帰でも同じ式が
    導けていたが、90%超の範囲ではindex6が繰り上がるため両方必要)。実際のログの
    1パケットで検算する(hex=bc0023cd007b012001c1、焙煎開始から約288秒、
    その時点のプロファイル予定風量は約61.8%)。"""
    _install_fake_bleak(monkeypatch)

    async def scenario():
        telemetry = []
        sess = RoasterSession(on_telemetry=telemetry.append)
        assert await sess.connect(timeout=0.01)
        sess._acked_uuid_echo = True
        await sess._handle_notify(bytes.fromhex("bc0023cd007b012001c1"))
        return telemetry

    telemetry = asyncio.run(scenario())
    assert len(telemetry) == 1
    sample = telemetry[0]
    assert sample.bt == 0x00cd  # 4-5バイト目(LE)=205、温度の抽出自体は既存仕様のまま
    assert sample.fan == pytest.approx(61.0, abs=1.5)  # プロファイル予定(約61.8%)に近いこと


def test_fan_at_90_percent_uses_combined_16bit_value(monkeypatch):
    """実機ログ(2026-07、50/80/90/100%を段階的に確認したテスト)から: 風量を
    90%に指定した区間では、index5(下位バイト)だけを見ると1バイトの上限255に
    張り付いて見えるが、index6(上位バイト)と合わせた16bit値(index5+256×index6)
    で見ると校正式にきれいに乗る。実バイト列(hex=5900237000ff016900cd、
    90%区間・bt=112、index5=0xff・index6=0x01→16bit値0x01ff=511)で、
    fanが約90%と算出されること。"""
    _install_fake_bleak(monkeypatch)

    async def scenario():
        telemetry = []
        sess = RoasterSession(on_telemetry=telemetry.append)
        assert await sess.connect(timeout=0.01)
        sess._acked_uuid_echo = True
        await sess._handle_notify(bytes.fromhex("5900237000ff016900cd"))
        return telemetry

    telemetry = asyncio.run(scenario())
    assert len(telemetry) == 1
    sample = telemetry[0]
    assert sample.bt == 0x0070  # 112、この区間で温度が通常通り上昇していることの確認
    assert sample.fan == pytest.approx(90.0, abs=2.0)


def test_telemetry_extracts_elapsed_seconds_from_byte7_8(monkeypatch):
    """実機ログ(2026-07、519秒の実焙煎テスト)から: 8・9バイト目(0始まりで
    index7:8)のLE16bit値が、焙煎開始からの経過秒数と1秒の誤差もなく一致する
    ことを確認した(実バイト列hex=5900237000ff016900cd、焙煎開始から
    ちょうど105秒後に届いたサンプル、index7:8=0x0069=105)。"""
    _install_fake_bleak(monkeypatch)

    async def scenario():
        telemetry = []
        sess = RoasterSession(on_telemetry=telemetry.append)
        assert await sess.connect(timeout=0.01)
        sess._acked_uuid_echo = True
        await sess._handle_notify(bytes.fromhex("5900237000ff016900cd"))
        return telemetry

    telemetry = asyncio.run(scenario())
    assert len(telemetry) == 1
    assert telemetry[0].elapsed_sec == 105


def test_fan_at_100_percent_crosses_16bit_byte_boundary(monkeypatch):
    """同じログから: 風量をちょうど100%に指定した区間では、index5だけを見ると
    45前後に「急落」したように見えるが、これは16bit値がindex5=255の上限を
    超えてindex6が1→2へ繰り上がった(0x01ff→0x0200)結果に過ぎない。
    実バイト列(hex=6e002380002d0293004b、100%区間・bt=128、index5=0x2d・
    index6=0x02→16bit値0x022d=557)で、fanが約100%と算出されること。"""
    _install_fake_bleak(monkeypatch)

    async def scenario():
        telemetry = []
        sess = RoasterSession(on_telemetry=telemetry.append)
        assert await sess.connect(timeout=0.01)
        sess._acked_uuid_echo = True
        await sess._handle_notify(bytes.fromhex("6e002380002d0293004b"))
        return telemetry

    telemetry = asyncio.run(scenario())
    assert len(telemetry) == 1
    sample = telemetry[0]
    assert sample.bt == 0x0080  # 128
    assert sample.fan == pytest.approx(100.0, abs=2.0)


def test_container_error_pre_ack_fallback_still_works(monkeypatch):
    """UUID確認前の、10byte+0x55ちょうどには一致しない旧来の短い通知パターンでも、
    従来通りフォールバックで容器エラーとして検出できること(回帰確認)。"""
    _install_fake_bleak(monkeypatch)

    async def scenario():
        states = []
        sess = RoasterSession(on_state=states.append)
        assert await sess.connect(timeout=0.01)
        # UUID確認前(acked_uuid_echo=False)、旧パターン(01始まり、4byte)
        await sess._handle_notify(bytes.fromhex("0155303001"))
        return states

    states = asyncio.run(scenario())
    assert S.STATE_CONTAINER_ERROR in states


def test_phase_code_0x20_standby_now_recognized(monkeypatch):
    """実機ログ: 接続直後・UUID確認前に出現した0x20(未対応だった)が、
    「待機中」として認識されること。"""
    _install_fake_bleak(monkeypatch)

    async def scenario():
        states = []
        sess = RoasterSession(on_state=states.append)
        assert await sess.connect(timeout=0.01)
        await sess._handle_notify(bytes.fromhex("2400a6ab282f4108003225d944e5500b2090e020"))
        return states

    states = asyncio.run(scenario())
    assert S.STATE_STANDBY in states


def test_phase_code_0x25_maps_to_forced_cooling_and_advances_phase(monkeypatch):
    """実機ログ: 強制冷却スイッチを押した瞬間に出現した0x25(未対応だった)が、
    0x26と同じ「強制冷却中」として認識され、内部フェーズも"discharging"へ進むこと。
    (これにより、0x26が来る前に切断されても「焙煎中の異常切断」と誤判定されず、
    無駄な自動再接続を防げる)"""
    _install_fake_bleak(monkeypatch)

    async def scenario():
        states = []
        sess = RoasterSession(on_state=states.append)
        assert await sess.connect(timeout=0.01)
        sess._phase_estimator.phase = "preheating"
        await sess._handle_notify(bytes.fromhex("2400a6ab282f4108003225d944e5500b2090e025"))
        return states, sess

    states, sess = asyncio.run(scenario())
    assert S.STATE_FORCED_COOLING in states
    assert sess._phase_estimator.phase == "discharging"


def _telemetry_packet(bt: int) -> bytes:
    """テスト用: data[3:5](LE16bit)がbtになる10byteの温度通知を組み立てる。"""
    b = bytearray(10)
    b[3] = bt & 0xFF
    b[4] = (bt >> 8) & 0xFF
    return bytes(b)


def test_error_recovery_detected_via_rising_temperature(monkeypatch):
    """容器エラーからの復帰: 専用の信号が無いため、焙煎中の温度がエラー中の
    最低値から一定以上(ERROR_RECOVERY_RISE)再上昇したことをもって復帰とみなす
    (ユーザー様のご提案)。閾値未満の上昇では復帰しないことも確認する。"""
    _install_fake_bleak(monkeypatch)

    async def scenario():
        states = []
        sess = RoasterSession(on_state=states.append)
        assert await sess.connect(timeout=0.01)
        sess._acked_uuid_echo = True
        # 焙煎中に確定させる(実機ログと同じバイト列)
        await sess._handle_notify(bytes.fromhex("2400a6ab282f4108003225d944e5500b2090e023"))
        assert sess._phase_estimator.phase == "roasting"
        states.clear()

        # 容器エラー発生(実機ログと同じU0220のバイト列)
        await sess._handle_notify(bytes.fromhex("015530323230230000b6"))
        assert sess._error_active is True

        # エラー中、温度が下がっていく
        await sess._handle_notify(_telemetry_packet(200))
        await sess._handle_notify(_telemetry_packet(195))
        await sess._handle_notify(_telemetry_packet(190))
        assert sess._error_active is True  # まだ復帰していない
        assert sess._error_min_bt == 190

        # 閾値(ERROR_RECOVERY_RISE=2.0)未満の上昇ではまだ復帰しない
        await sess._handle_notify(_telemetry_packet(191))
        assert sess._error_active is True

        # 閾値以上の上昇で復帰したとみなす
        await sess._handle_notify(_telemetry_packet(193))
        return states, sess

    states, sess = asyncio.run(scenario())
    assert sess._error_active is False
    assert S.STATE_ROASTING in states  # 復帰時に焙煎中の表示に戻る


def test_error_recovery_not_triggered_during_preheat(monkeypatch):
    """予熱中は温度が自然に停滞・微減するだけで誤判定しやすいため、
    復帰判定(温度の再上昇)は焙煎中のみに限定していること。"""
    _install_fake_bleak(monkeypatch)

    async def scenario():
        sess = RoasterSession()
        assert await sess.connect(timeout=0.01)
        sess._acked_uuid_echo = True
        sess._phase_estimator.phase = "preheating"
        await sess._handle_notify(bytes.fromhex("015530323230230000b6"))  # U0220
        assert sess._error_active is True
        await sess._handle_notify(_telemetry_packet(100))
        await sess._handle_notify(_telemetry_packet(90))
        await sess._handle_notify(_telemetry_packet(120))  # 大きく上昇しても...
        return sess

    sess = asyncio.run(scenario())
    assert sess._error_active is True  # 予熱中は復帰判定の対象外なので、まだエラーのまま


def test_suffix_0xe5_error_overlay_recognized_as_phase_code(monkeypatch):
    """実機ログ: ガラス容器を外した際に、通常のsuffix(0xe0/0xe4)ではなく0xe5で
    同じフェーズコード(0x21=予熱中)が届いた。以前は0xe0/0xe4しか見ておらず、
    このsuffixのフェーズコードは検出されなかった。"""
    _install_fake_bleak(monkeypatch)

    async def scenario():
        states = []
        sess = RoasterSession(on_state=states.append)
        assert await sess.connect(timeout=0.01)
        await sess._handle_notify(bytes.fromhex("1c00a6ab282f4108003225d944e5500b2090e521"))
        return states, sess

    states, sess = asyncio.run(scenario())
    assert S.STATE_PREHEATING in states
    assert sess._last_phase_code == 0x21


# ------------------------------------------------------------
# 送信バイト列(build_write_sequence)
# ------------------------------------------------------------
# 校正用プロファイルを送っても焙煎機が動かなかった件の再発防止。
# 原因は2つあった:
#   1. 校正用プロファイルのUUIDが空で、そもそも組み立てられなかった
#   2. 終端バイトをUUIDだけで上書きしていたため、既知3件のプロファイルを
#      編集して送ると、中身と合わない終端バイトが付いていた
def _known_profiles():
    """KNOWN_TERMINATORS の3件を、プリセットDBから取れた分だけ返す。"""
    from pathlib import Path

    import app.server as server

    if not Path(server.DB_PATH).exists():
        return []
    db = server.get_db()
    out = []
    for _, row in db.profile.iterrows():
        p = server.ModelFactory.from_series(row)
        try:
            uuid_ascii = bytes.fromhex(p.raw.get("UUID", "") or "").decode("ascii")
        except Exception:  # noqa: BLE001
            continue
        if uuid_ascii in S.KNOWN_TERMINATORS:
            out.append((uuid_ascii, p))
    return out


def test_終端バイトの計算式が実測と一致する():
    """KNOWN_TERMINATORS は計算式の裏づけとして残してある。

    ここが落ちたら、計算式(またはトークン)が変わったということなので、
    上書きをやめた判断も見直す必要がある。
    """
    from roastlib.ble.codec import encode_profile_payload

    known = _known_profiles()
    if not known:
        pytest.skip("プリセットDBが無いため確認できません")
    for uuid_ascii, p in known:
        q = S.profile_from_points(p.name, p.roast.points, p.fan.points,
                                  tuple(p.cooldown.points[0]), uuid_ascii)
        payload = bytes(encode_profile_payload(q))
        pairs = len(q.roast.x) + len(q.fan.x)
        assert S._guess_terminator(payload[:-1], pairs) == S.KNOWN_TERMINATORS[uuid_ascii], uuid_ascii


def test_中身を変えると終端バイトも変わる():
    """UUIDだけで終端バイトを決めると、編集したカーブに古い値が付いてしまう。

    以前はそれで、機械がプロファイルを破棄し「送信は終わるのに予熱に進まない」
    状態になっていた。
    """
    uuid_ascii = next(iter(S.KNOWN_TERMINATORS))
    base = S.profile_from_points("t", [(0, 180), (60, 100), (300, 200), (400, 230)],
                                 [(0, 60), (100, 80), (400, 70)], (500, 60), uuid_ascii)
    edited = S.profile_from_points("t", [(0, 180), (60, 100), (300, 200), (400, 235)],
                                   [(0, 60), (100, 80), (400, 70)], (500, 60), uuid_ascii)
    token = bytes(range(16))
    a, b = S.build_write_sequence(base, token), S.build_write_sequence(edited, token)
    assert a != b, "カーブを変えたのに送信内容が同じです"


def test_校正用プロファイルは送信できる形で返る():
    """UUIDが空だと組み立てられず、焙煎機は何もしない(実際に起きた)。"""
    import json

    import app.server as server

    d = json.loads(server.get_calibration_profile().body)
    assert len(d["uuid"]) == 16 and d["uuid"].isdigit()
    q = S.profile_from_points(d["name"], [tuple(p) for p in d["roast"]],
                              [tuple(p) for p in d["fan"]], tuple(d["cooldown"]), d["uuid"])
    seq = S.build_write_sequence(q, bytes(range(16)))
    assert len(seq) >= 4
    # header2 の先頭バイトは総ペア数から決まる(実機で確認済みの式)
    assert seq[1][0] == (8 * (len(q.roast.x) + len(q.fan.x)) + 45) & 0xFF


def test_校正用のUUIDは毎回同じ():
    """送るたびに違うUUIDになると、焙煎機側にどう溜まるか分からない。"""
    import json

    import app.server as server

    a = json.loads(server.get_calibration_profile().body)["uuid"]
    b = json.loads(server.get_calibration_profile().body)["uuid"]
    assert a == b == server.beancal.CALIBRATION_UUID


def test_校正用のUUIDは生成プロファイルと衝突しない():
    """「味を推測」等が作るUUIDは str(int(time.time()*1000)).zfill(16)。

    このアプリが動く時刻より前の値にしてあれば、生成分と重ならない。
    """
    import time

    from roastlib.calibration import CALIBRATION_UUID

    assert len(CALIBRATION_UUID) == 16 and CALIBRATION_UUID.isdigit()
    assert int(CALIBRATION_UUID) < int(time.time() * 1000)
    # 生成側は「作った瞬間の時刻」なので、過去の固定値と一致することはない
    assert CALIBRATION_UUID != str(int(time.time() * 1000)).zfill(16)
