from __future__ import annotations

import json
import subprocess
from pathlib import Path


AUDIO_ROOT = Path(__file__).resolve().parents[1]


def test_package_check_covers_wheel_editable_public_api_and_boundary(tmp_path) -> None:
    """测试目标：冻结发布前检查的最小包检查范围。

    测试方法：运行 `audio-chat.sdk.package-check` 并读取 JSON 报告。
    预期结果：wheel 构建、editable install、公开 API 导入和 endpoint 边界检查都通过。
    """

    report = tmp_path / "package-check.json"
    completed = subprocess.run(
        ["uv", "run", "audio-chat.sdk.package-check", "--report", str(report)],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, f"stdout={completed.stdout}\nstderr={completed.stderr}"
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["checks"]["wheel_build"]["ok"] is True
    assert data["checks"]["wheel_install"]["ok"] is True
    assert data["checks"]["wheel_contents"]["ok"] is True
    assert data["checks"]["editable_install"]["ok"] is True
    assert data["checks"]["public_api"]["ok"] is True
    assert data["checks"]["boundary"]["ok"] is True
    assert data["checks"]["source_boundary"]["ok"] is True
    assert data["checks"]["endpoint_sources"]["ok"] is True
    assert data["checks"]["release_candidate"]["ok"] is True


def test_release_wheel_does_not_include_private_or_endpoint_sources(tmp_path) -> None:
    """测试目标：确认 server SDK wheel 不混入端侧源码、样例、运行产物或本地私密配置。

    测试方法：读取 package-check 报告中的 wheel 内容检查结果。
    预期结果：`forbidden` 清单为空，且 `py.typed` 作为 package data 被包含。
    """

    report = tmp_path / "package-check.json"
    completed = subprocess.run(
        ["uv", "run", "audio-chat.sdk.package-check", "--report", str(report)],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, f"stdout={completed.stdout}\nstderr={completed.stderr}"
    wheel_contents = json.loads(report.read_text(encoding="utf-8"))["checks"]["wheel_contents"]
    assert wheel_contents["ok"] is True
    assert wheel_contents["forbidden"] == []
    assert wheel_contents["missing_required"] == []


def test_endpoint_reference_modules_are_importable_but_not_top_level_exports() -> None:
    """测试目标：确认参考端侧实现可单独导入，但不泄漏到 SDK 顶层公开包。

    测试方法：在当前测试进程导入 endpoint 模块和 `audio_chat` 顶层包。
    预期结果：endpoint 类只存在于 endpoint 子模块，顶层 `audio_chat` 不暴露它们。
    """

    import audio_chat
    from audio_chat.endpoints.python_playback import NetworkPythonPlaybackEndpoint

    assert NetworkPythonPlaybackEndpoint is not None
    assert not hasattr(audio_chat, "NetworkPythonPlaybackEndpoint")
    assert not hasattr(audio_chat, "PythonPhoneMockEndpoint")


def test_public_api_imports_from_clean_python_process() -> None:
    """测试目标：确认公开 API 不依赖 pytest 路径副作用。

    测试方法：使用 `uv run python -c` 在独立解释器中导入顶层对象。
    预期结果：开发者安装后可直接从 `audio_chat` 导入扩展 API。
    """

    code = "from audio_chat import AudioChatApp, BaseTool, BaseTask, ToolResult, TaskEvent, UserDeviceContext"
    completed = subprocess.run(
        ["uv", "run", "python", "-c", code],
        cwd=AUDIO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
