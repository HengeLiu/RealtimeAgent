from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from audio_chat_python_phone_mock.vision.config import VisionConfig
from audio_chat_python_phone_mock.vision.processor import VisionProcessor
from audio_chat_python_phone_mock.vision.traffic_light import TrafficLightDetector


def test_vision_config_uses_modelscope_default_paths() -> None:
    """测试目标：验证 Python phone 默认视觉配置指向本机 modelscope 模型目录。

    测试方法：从空配置创建 `VisionConfig`，检查两个模型路径。
    预期结果：找物使用 `yoloe-11l-seg.pt`，红绿灯使用 `trafficlight.pt`。
    """

    config = VisionConfig.from_mapping({"provider": "yolo"})

    assert config.provider == "yolo"
    assert config.find_object.model_path.endswith("yoloe-11l-seg.pt")
    assert config.traffic_light.model_path.endswith("trafficlight.pt")


def test_vision_processor_mock_provider_keeps_existing_result_shape() -> None:
    """测试目标：验证新增 VisionProcessor 后 mock 模式仍保持旧结果结构。

    测试方法：创建 provider=mock 的处理器并处理一帧 JPEG-like 字节。
    预期结果：返回 source=mock 的找物结果，且不会要求真实模型依赖。
    """

    async def run() -> None:
        processor = VisionProcessor(VisionConfig.from_mapping({"provider": "mock"}))
        await processor.prepare_session(purpose="find_object", object_name="水杯")
        result = await processor.process_frame(b"\xff\xd8mock-frame\xff\xd9", frame_count=1)
        final = await processor.build_final_result(frame_count=1, last_detection=result.detection)

        assert result.detection["source"] == "mock"
        assert final["source"] == "mock"
        assert final["object_name"] == "水杯"

    asyncio.run(run())


def test_traffic_light_majority_vote_with_fake_model(monkeypatch) -> None:
    """测试目标：验证红绿灯检测保留旧实现的多数表决语义。

    测试方法：用假 YOLO 模型连续返回 go 类别，处理三帧图像。
    预期结果：第三帧达到多数阈值后输出 stable=true、state=green、can_cross=true。
    """

    import audio_chat_python_phone_mock.vision.traffic_light as traffic_module

    monkeypatch.setattr(traffic_module, "load_ultralytics_model", lambda **_kwargs: FakeTrafficModel())
    config = VisionConfig.from_mapping({"provider": "yolo"}).traffic_light
    detector = TrafficLightDetector(config=config, device="cpu")
    detector.prepare()

    frame = np.zeros((80, 120, 3), dtype=np.uint8)
    result = None
    for idx in range(1, 4):
        result = detector.process(frame, frame_count=idx)

    assert result is not None
    assert result.detection["stable"] is True
    assert result.detection["state"] == "green"
    assert result.detection["can_cross"] is True
    assert result.should_complete is True


def test_phone_preview_config_declares_real_yolo_models() -> None:
    """测试目标：验证默认 phone preview 配置已经切到真实 YOLO provider。

    测试方法：读取 YAML 文本，检查 provider 和模型文件名。
    预期结果：provider=yolo，并声明找物和红绿灯模型路径。
    """

    config_path = Path(__file__).resolve().parents[2] / "devices/python-phone/phone.preview.yaml"
    text = config_path.read_text(encoding="utf-8")

    assert "provider: yolo" in text
    assert "yoloe-11l-seg.pt" in text
    assert "trafficlight.pt" in text


class FakeTrafficModel:
    """返回稳定绿灯检测的假模型。"""

    names = {0: "go"}

    def __call__(self, *_args, **_kwargs):
        return [SimpleNamespace(boxes=FakeBoxes())]


class FakeBoxes:
    """模拟 ultralytics boxes 对象。"""

    def __len__(self) -> int:
        return 1

    def __iter__(self):
        yield SimpleNamespace(cls=[0], conf=[0.91], xyxy=[[10, 10, 40, 60]])
