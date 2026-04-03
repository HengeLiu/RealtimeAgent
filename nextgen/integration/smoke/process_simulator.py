"""三进程模拟联调运行器。"""

from dataclasses import dataclass
from typing import Any, Dict

from nextgen.integration.find_object.runner import FindObjectIntegrationRunner
from nextgen.integration.smoke.testdata_loader import StandardTestDataLoader


@dataclass
class ProcessRuntimeDescriptor:
    """模拟进程描述。

    主要功能：
    - 表示三进程模拟中的一个运行时实例
    - 固定名称、设备标识和日志标签
    """

    name: str
    runtime: str
    device_id: str


class ThreeProcessFindObjectSimulator:
    """寻找物体三进程模拟器。

    主要功能：
    - 使用标准测试数据驱动“寻找物体”黄金链路
    - 以眼镜、手机、服务器三个独立运行体的视角组织执行结果
    - 为后续容器模拟和混合联调提供统一入口

    主要方法：
    - `run_case`：执行单个标准 case
    - `describe_processes`：返回当前模拟的三端描述
    """

    def __init__(self) -> None:
        """初始化模拟器。"""

        self.loader = StandardTestDataLoader()
        self.runner = FindObjectIntegrationRunner()
        self.glass_process = ProcessRuntimeDescriptor(
            name="glass-runtime",
            runtime="glass",
            device_id="glass-001",
        )
        self.phone_process = ProcessRuntimeDescriptor(
            name="phone-runtime",
            runtime="phone",
            device_id="phone-001",
        )
        self.server_process = ProcessRuntimeDescriptor(
            name="server-runtime",
            runtime="server",
            device_id="server-main",
        )

    def describe_processes(self) -> Dict[str, Dict[str, str]]:
        """返回三端模拟进程描述。"""

        return {
            "glass": self.glass_process.__dict__.copy(),
            "phone": self.phone_process.__dict__.copy(),
            "server": self.server_process.__dict__.copy(),
        }

    def run_case(self, case_id: str) -> Dict[str, Any]:
        """执行一个标准寻找物体 case。

        主要逻辑：
        - 从 `testdata/` 读取标准场景
        - 启动三端运行时
        - 跑通一次黄金链路
        - 返回包含 case 信息和运行结果的结构化数据
        """

        case = self.loader.build_find_object_case(case_id)
        mark_completed = case["expected_final_status"] == "completed"

        self.runner.start()
        try:
            result = self.runner.run_find_object(
                voice_text=case["voice_text"],
                target_name=case["target_name"],
                candidates=case["candidates"],
                hand_observation=case["hand_observation"],
                mark_completed=mark_completed,
            )
        finally:
            self.runner.stop()

        result_dict = result.to_dict()
        return {
            "case_id": case["case_id"],
            "voice_case_id": case["voice_case_id"],
            "expected_hint_contains": case["expected_hint_contains"],
            "expected_final_status": case["expected_final_status"],
            "processes": self.describe_processes(),
            "result": result_dict,
        }
