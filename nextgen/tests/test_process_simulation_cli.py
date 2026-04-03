"""三进程模拟命令行辅助测试。"""

import json

from nextgen.integration.smoke.cli import format_simulation_report, run_simulation_case


def test_run_simulation_case_returns_standard_report() -> None:
    """验证命令行辅助方法会返回标准结构化报告。"""

    report = run_simulation_case("find_object_phone_center_001")

    assert report["case_id"] == "find_object_phone_center_001"
    assert report["result"]["final_status"] == "completed"
    assert "processes" in report


def test_format_simulation_report_returns_json_text() -> None:
    """验证命令行辅助方法会返回合法 JSON 文本。"""

    text = format_simulation_report("find_object_wallet_missing_001")
    payload = json.loads(text)

    assert payload["case_id"] == "find_object_wallet_missing_001"
    assert payload["result"]["final_status"] == "running"
