# -*- coding: utf-8 -*-
"""テストが実データを触らない仕掛けそのものの検証。

環境変数の名前を1つ間違えるだけで、テストが利用者の焙煎記録に書き込む
(実際に起きた)。app/server.py 側で差し替え用の環境変数が増えたときに、
conftest.py への追記漏れをここで気づけるようにしておく。
"""
import re
from pathlib import Path

from conftest import SANDBOX_ENV_VARS

REPO = Path(__file__).resolve().parent.parent

# データの保存先ではないもの(読み取り専用・鍵ファイル)は差し替えなくてよい。
NOT_USER_DATA = {
    "ROAST_DB_PATH",          # プリセット(読み取り専用)
    "ROAST_IKAWA_PATH",       # IKAWAプロファイル(読み取り専用)
    "ROAST_BEAN_SHEETS_PATH",  # 生豆紹介シート(読み取り専用)
    "ROAST_VAPID_KEY_PATH",   # 通知用の鍵
    # 配布版だけにある同梱サンプル(読み取り専用。初回起動時に保存済みへ複製する)
    "ROAST_SAMPLE_PROFILES_PATH",
    # 保存先ではなく、先読みを裏で走らせるかどうかの切り替え(conftestが切る)
    "ROAST_NO_BACKGROUND_WARMUP",
}


def _env_vars_in_server():
    src = (REPO / "app" / "server.py").read_text(encoding="utf-8")
    return set(re.findall(r'os\.environ\.get\("(ROAST_[A-Z_]+)"', src))


def test_書き込み先はすべて差し替えられる():
    missing = _env_vars_in_server() - set(SANDBOX_ENV_VARS) - NOT_USER_DATA
    assert not missing, (
        f"conftest.py の SANDBOX_ENV_VARS に足りません: {sorted(missing)}。"
        "足さないと、テストが実データに書き込みます"
    )


def test_使わない名前を並べていない():
    """存在しない環境変数を並べていると、差し替えたつもりで効いていない。"""
    unknown = set(SANDBOX_ENV_VARS) - _env_vars_in_server()
    assert not unknown, f"app/server.py が読んでいない名前です: {sorted(unknown)}"


def test_砂場のサーバーは実ファイルを指さない(srv, tmp_path):
    assert str(srv.ROAST_RECORDS_PATH).startswith(str(tmp_path))
    assert not (REPO / "roast_records.json").samefile(
        srv.ROAST_RECORDS_PATH) if srv.ROAST_RECORDS_PATH.exists() else True
