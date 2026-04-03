"""真实场景找物通讯 demo 测试。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_real_find_object_demo_can_run_end_to_end() -> None:
    """验证真实场景找物通讯 demo 可以完整跑通。"""

    completed = subprocess.run(
        [sys.executable, "scripts/run_real_find_object_demo.py"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=40,
        check=True,
    )

    report = json.loads(completed.stdout)
    assert report["created"]["ok"] is True
    assert report["orchestrated"]["ok"] is True
    assert report["frame_result"]["ok"] is True
    assert report["stopped"]["ok"] is True
    assert report["frame_result"]["hint"]["text"].startswith("已发现手机")

    tasks = report["health_snapshot"]["tasks"]
    assert len(tasks) == 1
    task = tasks[0]
    assert task["task_name"] == "find_object"
    assert task["status"] == "completed"
    assert task["phase"] == "peer_link_closed"
    assert task["link_status"]["status"] == "closed"
