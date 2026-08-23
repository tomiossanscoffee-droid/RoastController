# -*- coding: utf-8 -*-
"""Tailscaleの接続状態の判定(app/server.py の _tailscale_status)。

2026-08: MagicDNS名(Self.DNSName)はログイン済みでさえあれば切断中でも返ってくる。
それを「使える」と扱っていたため、Tailscaleがオフのままサーバーが起動し、
スマホから繋がらない(手でオフ→オンすると繋がる)事象が起きていた。
実際に通信できるのは BackendState=Running かつ Tailscale IP がある時だけ。
"""
import json
from unittest.mock import patch

import pytest

from app.server import _tailscale_host, _tailscale_status

RUNNING = {"BackendState": "Running",
           "Self": {"DNSName": "imac.tail0.ts.net.", "TailscaleIPs": ["100.1.2.3"]}}
STOPPED = {"BackendState": "Stopped",
           "Self": {"DNSName": "imac.tail0.ts.net.", "TailscaleIPs": ["100.1.2.3"]}}
STARTING = {"BackendState": "Starting",
            "Self": {"DNSName": "imac.tail0.ts.net.", "TailscaleIPs": []}}
NEEDS_LOGIN = {"BackendState": "NeedsLogin", "Self": {}}
RUNNING_NO_IP = {"BackendState": "Running",
                 "Self": {"DNSName": "imac.tail0.ts.net.", "TailscaleIPs": []}}


class _Res:
    def __init__(self, out, rc=0):
        self.stdout, self.returncode = out, rc


def _run_with(payload, rc=0):
    out = payload if isinstance(payload, str) else json.dumps(payload)
    return patch("subprocess.run", return_value=_Res(out, rc))


def test_接続中はホスト名を返す():
    with _run_with(RUNNING):
        st = _tailscale_status()
        assert st["connected"] is True
        assert st["host"] == "imac.tail0.ts.net"   # 末尾のドットは落とす
        assert st["state"] == "Running"
        assert _tailscale_host() == "imac.tail0.ts.net"


@pytest.mark.parametrize("payload,state", [
    (STOPPED, "Stopped"), (STARTING, "Starting"),
    (NEEDS_LOGIN, "NeedsLogin"), (RUNNING_NO_IP, "Running"),
])
def test_接続していなければホスト名を案内しない(payload, state):
    with _run_with(payload):
        st = _tailscale_status()
        assert st["connected"] is False, f"{state} を接続中と誤判定した"
        assert st["state"] == state
        # 画面での説明用に名前自体は保持するが、アドレスとしては出さない
        assert _tailscale_host() == ""


def test_切断中でも名前は分かる():
    """「オンにすれば使えます」と案内するために、名前だけは持っておく。"""
    with _run_with(STOPPED):
        assert _tailscale_status()["host"] == "imac.tail0.ts.net"


@pytest.mark.parametrize("payload,rc", [
    ("こわれたJSON", 0), ("", 0), (RUNNING, 1),
])
def test_取得できない場合は未接続扱い(payload, rc):
    with _run_with(payload, rc):
        st = _tailscale_status()
        assert st["connected"] is False and st["host"] == ""
        assert _tailscale_host() == ""


def test_コマンドが無い場合():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        assert _tailscale_status() == {"host": "", "state": "", "connected": False}
