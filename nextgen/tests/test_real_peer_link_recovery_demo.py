"""真实场景任务级连接恢复 demo 测试。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_real_peer_link_recovery_demo_can_recover_once() -> None:
    """验证真实场景恢复 demo 可以完成一次断链恢复。"""

    completed = subprocess.run(
        [sys.executable, "scripts/run_real_peer_link_recovery_demo.py"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=40,
        check=True,
    )

    report = json.loads(completed.stdout)
    assert report["created"]["ok"] is True
    assert report["orchestrated"]["ok"] is True
    assert report["broken"]["ok"] is True
    recovered = report["broken"]["result"]["recovered"]
    assert recovered["link_state"]["status"] == "connected"
    assert recovered["link_state"]["connect_attempt_count"] >= 1
    assert report["stopped"]["ok"] is True
