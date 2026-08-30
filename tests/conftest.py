# -*- coding: utf-8 -*-
"""サーバーのテストが、利用者の実データを触らないようにするための土台。

以前、環境変数の名前を間違えて並べていたため(ROAST_ROAST_RECORDS_PATH のように)
差し替えが効かず、テストが実際の roast_records.json に書き込んでしまった。
名前を各テストで手書きするのをやめ、app/server.py が実際に読んでいる名前を
一箇所に集めて、そこから差し替える。
"""
import importlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# app/server.py が os.environ.get() で読んでいる差し替え用の環境変数。
# ここに無いものは実ファイルを指したままになるので、増えたら足すこと
# (test_sandbox_paths.py が、書き漏れを見つけて落ちるようにしてある)。
SANDBOX_ENV_VARS = (
    "ROAST_APP_SETTINGS_PATH",
    "ROAST_BEAN_PURCHASES_PATH",
    "ROAST_CALIBRATION_PATH",
    "ROAST_CUSTOM_PATH",
    "ROAST_FAVORITES_PATH",
    "ROAST_GUIDE_TEMPS_PATH",
    "ROAST_LAST_SENT_PROFILE_PATH",
    "ROAST_MOBILE_HOST_PATH",
    "ROAST_PUSH_SUBSCRIPTIONS_PATH",
    "ROAST_RECORDS_PATH",
    "ROAST_SELECTED_BEAN_PATH",
    "ROAST_UNSAVED_COUNTS_PATH",
)

# 書き込み先がテスト用かどうかを見分けるための名前。tmp_path配下に作る。
def sandbox_path(tmp_path: Path, env_var: str) -> Path:
    return tmp_path / (env_var.removeprefix("ROAST_").removesuffix("_PATH").lower() + ".json")


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    """保存先をすべてtmp_pathに向けた app.server を返す。"""
    for env_var in SANDBOX_ENV_VARS:
        monkeypatch.setenv(env_var, str(sandbox_path(tmp_path, env_var)))
    import app.server as server
    importlib.reload(server)
    # 差し替えが効いていることを、実際のパスで確かめてから渡す。
    # (名前を間違えるとリポジトリ直下のファイルを指したままになる)
    for attr in ("APP_SETTINGS_PATH", "BEAN_PURCHASES_PATH", "CALIBRATION_PATH",
                 "CUSTOM_PATH", "FAVORITES_PATH", "GUIDE_TEMPS_PATH",
                 "LAST_SENT_PROFILE_PATH", "MOBILE_HOST_PATH",
                 "PUSH_SUBSCRIPTIONS_PATH", "ROAST_RECORDS_PATH",
                 "SELECTED_BEAN_PATH", "UNSAVED_ROAST_COUNTS_PATH"):
        p = getattr(server, attr)
        assert str(p).startswith(str(tmp_path)), \
            f"{attr} がテスト用の場所を指していません: {p}"
    return server
