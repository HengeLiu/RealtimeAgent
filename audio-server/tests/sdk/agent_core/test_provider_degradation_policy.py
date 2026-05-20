from __future__ import annotations

import pytest

from realtime_agent.agent_core.providers import (
    VisionModelProviderConfig,
    build_vision_model,
    run_provider_call_with_policy,
)


def test_provider_policy_records_retry_timeout_and_success() -> None:
    """测试目标：验证真实 provider 调用策略会记录 retry、timeout 和定位字段。

    测试方法：构造首次失败、第二次成功的 provider operation，并通过统一策略执行。
    预期结果：调用成功，诊断中包含 provider、model、endpoint、timeout 和 retry 次数。
    """

    calls = {"count": 0}

    def operation() -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("network timeout")
        return "ok"

    result, diagnostic = run_provider_call_with_policy(
        provider="dashscope",
        model="qwen-test",
        endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
        timeout_seconds=5,
        max_retries=1,
        allow_mock_fallback=True,
        operation=operation,
    )

    assert result == "ok"
    data = diagnostic.as_dict()
    assert data["ok"] is True
    assert data["attempts"] == 2
    assert data["provider"] == "dashscope"
    assert data["model"] == "qwen-test"
    assert data["endpoint"].startswith("https://")
    assert data["fallback_policy"] == "mock"


def test_provider_policy_reports_failure_without_fallback() -> None:
    """测试目标：验证禁止 fallback 时 provider 失败可诊断。

    测试方法：构造持续抛错的 provider operation，并声明 `allow_mock_fallback=False`。
    预期结果：诊断失败，fallback_policy 为 fail，错误中保留异常类型和信息。
    """

    result, diagnostic = run_provider_call_with_policy(
        provider="openai-compatible",
        model="local-qwen",
        endpoint="http://127.0.0.1:8000/v1",
        timeout_seconds=1,
        max_retries=1,
        allow_mock_fallback=False,
        operation=lambda: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )

    assert result is None
    data = diagnostic.as_dict()
    assert data["ok"] is False
    assert data["attempts"] == 2
    assert data["fallback_policy"] == "fail"
    assert "RuntimeError: connection refused" in data["error"]


def test_openai_compatible_missing_key_fallback_and_fail(monkeypatch) -> None:
    """测试目标：验证 Text Model 真实 provider 缺 key 时能 fallback 或明确失败。

    测试方法：清空 `OPENAI_API_KEY` 后分别构建允许和禁止 fallback 的配置。
    预期结果：允许 fallback 返回 mock；禁止 fallback 抛出包含 key 名称的错误。
    """

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    provider, reason = build_vision_model(
        VisionModelProviderConfig(provider="openai-compatible", allow_mock_fallback=True)
    )

    assert provider.provider_name == "mock"
    assert reason and "OPENAI_API_KEY" in reason
    with pytest.raises(Exception) as exc_info:
        build_vision_model(VisionModelProviderConfig(provider="openai-compatible", allow_mock_fallback=False))
    assert "OPENAI_API_KEY" in str(exc_info.value)
