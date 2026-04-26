"""盲人业务工程对 SDK 的兼容性回归测试。"""

from __future__ import annotations

import json
from pathlib import Path

from server.main import create_sdk
from openaiglasses.testing import ScenarioRunner


ROOT = Path(__file__).resolve().parents[3]
BLIND_APP_ROOT = ROOT / "openaiglass-for-blind"
COMPAT_ROOT = BLIND_APP_ROOT / "testdata/compat"


def _load_suite(name: str) -> dict:
    """读取兼容性套件定义。"""

    return json.loads((COMPAT_ROOT / name).read_text(encoding="utf-8"))


def test_find_object_compat_suite_validates_all_scenarios() -> None:
    """测试目标：验证盲人业务兼容性场景都能通过 manifest 校验。

    测试方法：
    1. 读取 `find_object_scenarios.json`。
    2. 逐个调用 `ScenarioRunner.validate(...)`。
    3. 收集失败场景。

    预期结果：
    1. 套件中全部场景都校验通过。
    """

    suite = _load_suite("find_object_scenarios.json")

    failures: list[dict[str, object]] = []
    for scenario in suite["scenarios"]:
        sdk = create_sdk()
        runner = ScenarioRunner(sdk, workspace_root=BLIND_APP_ROOT)
        validation = runner.validate(BLIND_APP_ROOT / str(scenario))
        if not validation.get("ok", False):
            failures.append(
                {
                    "scenario": scenario,
                    "errors": validation.get("errors", []),
                }
            )

    assert failures == []


def test_find_object_compat_suite_executes_all_scenarios() -> None:
    """测试目标：验证盲人业务兼容性场景在当前 SDK 中仍可执行。

    测试方法：
    1. 读取 `find_object_scenarios.json`。
    2. 逐个调用 `ScenarioRunner.run(...)`。
    3. 收集回放断言失败场景。

    预期结果：
    1. 套件中全部场景都能执行并满足各自 `expected` 断言。
    """

    suite = _load_suite("find_object_scenarios.json")

    failures: list[dict[str, object]] = []
    for scenario in suite["scenarios"]:
        sdk = create_sdk()
        runner = ScenarioRunner(sdk, workspace_root=BLIND_APP_ROOT)
        result = runner.run(BLIND_APP_ROOT / str(scenario))
        if not (result.get("assertions") or {}).get("passed", False):
            failures.append(
                {
                    "scenario": scenario,
                    "failures": (result.get("assertions") or {}).get("failures", []),
                }
            )

    assert failures == []
