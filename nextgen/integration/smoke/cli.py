"""三进程模拟命令行支持。"""

import json
from typing import Any, Dict

from nextgen.integration.smoke.process_simulator import ThreeProcessFindObjectSimulator


def run_simulation_case(case_id: str) -> Dict[str, Any]:
    """执行单个标准模拟场景。

    参数：
    - case_id：标准测试场景编号

    返回值：
    - 包含场景编号、三端描述和运行结果的字典
    """

    simulator = ThreeProcessFindObjectSimulator()
    return simulator.run_case(case_id)


def format_simulation_report(case_id: str) -> str:
    """将模拟结果格式化为 JSON 文本。

    参数：
    - case_id：标准测试场景编号

    返回值：
    - 便于终端打印和日志记录的 JSON 字符串
    """

    report = run_simulation_case(case_id)
    return json.dumps(report, ensure_ascii=False, indent=2)
