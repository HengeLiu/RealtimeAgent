"""标准测试数据加载器。"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from nextgen.shared.models import BoundingBox, HandObservation, ObjectObservation


class StandardTestDataLoader:
    """标准测试数据加载器。

    主要功能：
    - 统一加载仓库中的标准测试数据
    - 将 JSON 数据转换为运行时可直接消费的对象

    主要方法：
    - `load_voice_cases`：加载语音测试数据
    - `load_find_object_cases`：加载寻找物体测试数据
    - `load_imu_cases`：加载 IMU 测试数据
    - `build_find_object_case`：将单个找物场景转换为运行时输入
    """

    def __init__(self, root_dir: Optional[Path] = None) -> None:
        """初始化加载器。

        参数：
        - root_dir：测试数据根目录。未传入时默认使用仓库根目录下的 `testdata/`
        """

        self.root_dir = root_dir or Path(__file__).resolve().parents[3] / "testdata"

    def load_voice_cases(self) -> List[Dict[str, Any]]:
        """加载语音测试数据。"""

        return self._load_json("voice/voice_cases.json")

    def load_find_object_cases(self) -> List[Dict[str, Any]]:
        """加载寻找物体测试数据。"""

        return self._load_json("find_object/find_object_cases.json")

    def load_imu_cases(self) -> List[Dict[str, Any]]:
        """加载 IMU 测试数据。"""

        return self._load_json("imu/imu_cases.json")

    def get_voice_case(self, case_id: str) -> Dict[str, Any]:
        """按编号获取语音测试 case。"""

        return self._get_case_by_id(self.load_voice_cases(), case_id)

    def get_find_object_case(self, case_id: str) -> Dict[str, Any]:
        """按编号获取寻找物体测试 case。"""

        return self._get_case_by_id(self.load_find_object_cases(), case_id)

    def get_imu_case(self, case_id: str) -> Dict[str, Any]:
        """按编号获取 IMU 测试 case。"""

        return self._get_case_by_id(self.load_imu_cases(), case_id)

    def build_find_object_case(self, case_id: str) -> Dict[str, Any]:
        """构造可直接喂给找物集成运行器的场景输入。

        主要逻辑：
        - 读取找物 case
        - 解析关联的语音 case
        - 将候选目标和手部观测转换为共享模型对象

        返回值：
        - 供 `FindObjectIntegrationRunner.run_find_object()` 直接使用的字典

        异常情况：
        - 当 case_id 不存在时抛出 `ValueError`
        """

        case = self.get_find_object_case(case_id)
        voice_case = self.get_voice_case(case["voice_case_id"])
        candidates = [self._build_object_observation(item) for item in case.get("candidates", [])]
        hand_observation = self._build_hand_observation(case.get("hand_observation"))

        return {
            "case_id": case["case_id"],
            "voice_case_id": voice_case["case_id"],
            "voice_text": voice_case["text"],
            "audio_ref": voice_case["audio_ref"],
            "vad_confidence": voice_case["vad_confidence"],
            "target_name": case["target_name"],
            "frame_width": case["frame_width"],
            "frame_height": case["frame_height"],
            "expected_hint_contains": case["expected_hint_contains"],
            "expected_final_status": case["expected_final_status"],
            "candidates": candidates,
            "hand_observation": hand_observation,
        }

    def _load_json(self, relative_path: str) -> List[Dict[str, Any]]:
        """读取 JSON 文件。"""

        path = self.root_dir / relative_path
        return json.loads(path.read_text(encoding="utf-8"))

    def _get_case_by_id(self, cases: List[Dict[str, Any]], case_id: str) -> Dict[str, Any]:
        """从 case 列表中按编号查找数据。"""

        for item in cases:
            if item["case_id"] == case_id:
                return item
        raise ValueError(f"未找到测试数据 case: {case_id}")

    def _build_object_observation(self, item: Dict[str, Any]) -> ObjectObservation:
        """将 JSON 中的目标候选数据转换为统一观测对象。"""

        polygon = [[float(point[0]), float(point[1])] for point in item["polygon"]]
        center_x = sum(point[0] for point in polygon) / len(polygon)
        center_y = sum(point[1] for point in polygon) / len(polygon)
        xs = [point[0] for point in polygon]
        ys = [point[1] for point in polygon]
        area = max(xs) - min(xs)
        area *= max(ys) - min(ys)
        return ObjectObservation(
            center_x=center_x,
            center_y=center_y,
            area=area,
            polygon=polygon,
            score=float(item.get("score", 0.0)),
        )

    def _build_hand_observation(self, item: Optional[Dict[str, Any]]) -> Optional[HandObservation]:
        """将 JSON 中的手部数据转换为统一观测对象。"""

        if item is None:
            return None

        bbox = item["bbox"]
        return HandObservation(
            center_x=float(item["center_x"]),
            center_y=float(item["center_y"]),
            area=float(item["area"]),
            bbox=BoundingBox(
                x1=int(bbox["x1"]),
                y1=int(bbox["y1"]),
                x2=int(bbox["x2"]),
                y2=int(bbox["y2"]),
            ),
            grasp_detected=bool(item.get("grasp_detected", False)),
            grasp_score=float(item.get("grasp_score", 0.0)),
        )
