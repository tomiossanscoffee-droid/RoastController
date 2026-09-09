# -*- coding: utf-8 -*-
"""画面側(JavaScript)のテストを、pytestからまとめて走らせる。

index.html の中の関数を実際に動かして検証するものなので、Python側のテストと
一緒に流れるようにしておく。deno も node も無い環境ではskipする。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SUITES = ["test_point_drag.js", "test_point_drag_sync.js", "test_point_hit.js",
          "test_touch_hit.js", "test_bean_picker_sort.js"]


def _runner():
    if shutil.which("deno"):
        return ["deno", "run", "--allow-read"]
    if shutil.which("node"):
        return ["node"]
    return None


@pytest.mark.parametrize("suite", SUITES)
def test_javascript_suite(suite):
    cmd = _runner()
    if not cmd:
        pytest.skip("deno も node も無いため、JavaScript側のテストをとばします")
    out = subprocess.run(cmd + [str(TESTS / suite)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, f"{suite} が失敗しました\n{out.stdout}\n{out.stderr}"
    assert "全て通過" in out.stdout, out.stdout
