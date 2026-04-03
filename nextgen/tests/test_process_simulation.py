"""三进程模拟测试。"""

from nextgen.integration.smoke.process_simulator import ThreeProcessFindObjectSimulator


def test_three_process_simulator_describes_three_runtimes() -> None:
    """验证三进程模拟器会暴露三端描述。"""

    simulator = ThreeProcessFindObjectSimulator()
    processes = simulator.describe_processes()

    assert set(processes.keys()) == {"glass", "phone", "server"}
    assert processes["glass"]["device_id"] == "glass-001"
    assert processes["phone"]["device_id"] == "phone-001"
    assert processes["server"]["device_id"] == "server-main"


def test_three_process_simulator_runs_completed_case() -> None:
    """验证三进程模拟器可以跑通完成态找物 case。"""

    simulator = ThreeProcessFindObjectSimulator()
    report = simulator.run_case("find_object_phone_center_001")

    assert report["result"]["final_status"] == "completed"
    assert report["expected_hint_contains"] in report["result"]["phone_hint"]["text"]


def test_three_process_simulator_runs_scanning_case() -> None:
    """验证三进程模拟器可以跑通持续扫描态找物 case。"""

    simulator = ThreeProcessFindObjectSimulator()
    report = simulator.run_case("find_object_wallet_missing_001")

    assert report["result"]["final_status"] == "running"
    assert report["expected_hint_contains"] in report["result"]["phone_hint"]["text"]
