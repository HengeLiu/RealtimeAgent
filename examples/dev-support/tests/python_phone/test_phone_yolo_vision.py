from __future__ import annotations

import asyncio
import builtins
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from audio_chat_python_phone_mock.vision.config import VisionConfig
from audio_chat_python_phone_mock.vision.models import VisionDependencyError
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


def test_yoloe_text_dependency_error_points_to_phone_requirements(monkeypatch) -> None:
    """测试目标：验证缺少 YOLOE 文本依赖时不会触发 Ultralytics 自动 pip 安装。

    测试方法：临时让 `import clip` 抛出 ModuleNotFoundError，直接调用依赖预检函数。
    预期结果：抛出 `VisionDependencyError`，错误信息指向 Python phone 自己的依赖文件。
    """

    import audio_chat_python_phone_mock.vision.find_object as find_object_module

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "clip":
            raise ModuleNotFoundError("No module named 'clip'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(VisionDependencyError, match="requirements\\.vision\\.txt"):
        find_object_module._ensure_yoloe_text_dependency()


def test_find_object_uses_predict_instead_of_tracker_dependency() -> None:
    """测试目标：验证找物推理不进入 Ultralytics tracker 依赖路径。

    测试方法：构造同时带 `predict()` 和 `track()` 的假模型，`track()` 被调用则失败。
    预期结果：`_run_segment()` 使用逐帧 predict，避免端侧额外依赖 `lap`。
    """

    import audio_chat_python_phone_mock.vision.find_object as find_object_module

    class FakeYoloeModel:
        predict_called = False

        def predict(self, *_args, **_kwargs):
            self.predict_called = True
            return []

        def track(self, *_args, **_kwargs):
            raise AssertionError("找物推理不应该调用 track()")

    model = FakeYoloeModel()
    frame = np.zeros((80, 120, 3), dtype=np.uint8)
    config = VisionConfig.from_mapping({"provider": "yolo"}).find_object

    result = find_object_module._run_segment(model, frame, config)

    assert model.predict_called is True
    assert result == {"masks": [], "boxes": [], "confidences": [], "ids": []}


def test_yoloe_text_weight_download_uses_phone_cache_dir(tmp_path, monkeypatch) -> None:
    """测试目标：验证 YOLOE 文本权重下载不会落到仓库根目录。

    测试方法：把当前目录切到临时目录，用假模型记录 `get_text_pe()` 执行时的工作目录。
    预期结果：工作目录位于 Python phone 的运行产物缓存目录。
    """

    import audio_chat_python_phone_mock.vision.find_object as find_object_module

    class FakeTextModel:
        called_cwd: Path | None = None

        def get_text_pe(self, texts):
            self.called_cwd = Path.cwd()
            return {"texts": texts}

    fake_model = FakeTextModel()
    monkeypatch.chdir(tmp_path)

    result = find_object_module._get_yoloe_text_pe(fake_model, ["眼镜"])

    assert result == {"texts": ["眼镜"]}
    assert fake_model.called_cwd == tmp_path / "runs/audio-chat/python-phone/vision-cache"
    assert Path.cwd() == tmp_path


def test_auto_device_avoids_mps_for_yoloe_float64_compatibility(monkeypatch) -> None:
    """测试目标：验证自动设备选择不会在 Mac 上默认使用 MPS。

    测试方法：注入一个 MPS 可用、CUDA 不可用的假 torch 模块，调用 `_select_device("auto")`。
    预期结果：返回 cpu，避免 YOLOE/MobileCLIP 中 float64 张量触发 MPS 不支持错误。
    """

    import audio_chat_python_phone_mock.vision.models as models_module

    fake_torch = SimpleNamespace(
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
        cuda=SimpleNamespace(is_available=lambda: False),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    assert models_module._select_device("auto") == "cpu"


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
