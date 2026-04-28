"""SDK 打包检查测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openaiglasses.cli import package_check


def test_ios_and_esp32_package_manifests_are_valid() -> None:
    """测试目标：确认端侧 SDK 包清单和源码文件齐全。

    测试方法：
    1. 直接调用 package-check 中的 iOS 和 ESP32 包形态检查函数。
    2. 校验返回结果包含包名、版本、能力列表和文件计数。

    预期结果：
    1. iOS 与 ESP32 检查均返回 `ok=True`。
    2. 清单中声明的运行时代码、测试代码和组件文件都能在仓库中找到。
    """

    package_check.configure_paths(type("Args", (), {"repo_root": "."})())

    ios_result = package_check._check_ios_package_shape()
    esp32_result = package_check._check_esp32_package_shape()

    assert ios_result["ok"] is True
    assert ios_result["name"] == "OpenAIGlassesPhoneSDK"
    assert ios_result["version"] == "sdk-v14"
    assert ios_result["runtime_files"] >= 8
    assert "phone_task_event_report" in ios_result["public_capabilities"]

    assert esp32_result["ok"] is True
    assert esp32_result["name"] == "openai_glasses_esp32_runtime"
    assert esp32_result["version"] == "sdk-v14"
    assert esp32_result["component_files"] >= 4
    assert "espressif/esp32-camera" in esp32_result["managed_dependencies"]


def test_manifest_loader_reports_missing_fields(tmp_path: Path) -> None:
    """测试目标：确认包清单缺字段时能暴露明确错误。

    测试方法：
    1. 在临时目录写入只有 `name` 的 JSON 清单。
    2. 要求 `_load_manifest` 校验 `name` 和 `version`。

    预期结果：
    1. 检查函数抛出 `RuntimeError`。
    2. 错误信息包含缺失字段名。
    """

    manifest_path = tmp_path / "package-manifest.json"
    manifest_path.write_text(json.dumps({"name": "demo"}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="version"):
        package_check._load_manifest(manifest_path, ["name", "version"])
