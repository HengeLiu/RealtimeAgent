import importlib.util
import os
import time
from pathlib import Path
from typing import Any

import pytest

from realtime_agent.agent_core.providers import (
    AsrProviderConfig,
    VisionModelProviderConfig,
    build_asr_provider,
    build_vision_model,
    run_provider_call_with_policy,
)
from realtime_agent.agent_core.omni import QwenOmniRealtimeAdapter, RealtimeProviderCallbacks, RealtimeProviderConfig
from realtime_agent.output import TtsProviderConfig
from realtime_agent.output.service import build_tts_provider
from realtime_agent.protocol import StreamChunk
from artifacts import elapsed_ms, write_provider_result, write_wav

EXPECTED_TEXT = "你好"
TESTDATA_DIR = Path(__file__).resolve().parent / "fixtures/provider"
ASR_SAMPLE = TESTDATA_DIR / "dashscope-nihao-16k.pcm"
ASR_EXPECTED = TESTDATA_DIR / "dashscope-nihao-expected.txt"

pytestmark = [
    pytest.mark.model_provider,
    pytest.mark.skipif(
        not os.getenv("DASHSCOPE_API_KEY") or importlib.util.find_spec("dashscope") is None,
        reason="DASHSCOPE_API_KEY and dashscope package are required for real provider integration tests",
    ),
]


def test_dashscope_asr_provider_transcribes_expected_sample() -> None:
    """测试目标：验证真实 DashScope ASR 能消费固定协议音频输入。

    测试方法：把固定 `sensor.mic` PCM 样例封装为 `StreamChunk` 输入 provider。
    预期结果：返回 final transcript，且测试产物记录 provider、model、耗时和识别文本。
    """

    if not ASR_SAMPLE.exists():
        _generate_asr_sample()
    expected = ASR_EXPECTED.read_text(encoding="utf-8").strip()
    model = os.getenv("REALTIME_AGENT_ASR_MODEL", "paraformer-realtime-v2")
    provider, downgrade = build_asr_provider(
        AsrProviderConfig(
            provider="dashscope",
            model=model,
            allow_mock_fallback=False,
            realtime_timeout_seconds=float(os.getenv("REALTIME_AGENT_ASR_TIMEOUT", "5")),
        )
    )

    started = time.monotonic()
    events = provider.append_audio(
        StreamChunk(
            user_id="integration-user",
            session_id="integration-session",
            stream_id="integration-stream",
            stream_type="sensor.mic",
            seq=0,
            payload=ASR_SAMPLE.read_bytes(),
            final=True,
        )
    )

    assert downgrade is None
    assert provider.provider_name == "dashscope"
    transcript = "".join(event.text for event in events if event.final)
    write_provider_result(
        "asr-result.json",
        {
            "provider": provider.provider_name,
            "model": model,
            "fallback_policy": "fail",
            "ok": expected in transcript,
            "elapsed_ms": elapsed_ms(started),
            "input_audio_path": str(ASR_SAMPLE),
            "input_audio_bytes": ASR_SAMPLE.stat().st_size,
            "transcript": transcript,
            "expected": expected,
            "error": "" if expected in transcript else "expected transcript not found",
        },
    )
    assert expected in transcript


def test_dashscope_streaming_tts_real_session_returns_pcm_and_metrics() -> None:
    """测试目标：验证真实 DashScope TTS 输出可播放音频和首音频指标。

    测试方法：发送短文本，收集 streaming delta 与 finish 后的 PCM，并写出 WAV 证据。
    预期结果：音频非空，metrics 包含首音频延迟，artifact 中记录 WAV 路径。
    """

    model = os.getenv("REALTIME_AGENT_TTS_MODEL", "cosyvoice-v3-flash")
    voice = os.getenv("REALTIME_AGENT_TTS_VOICE", "longanhuan")
    provider, downgrade = build_tts_provider(
        TtsProviderConfig(
            provider="dashscope",
            model=model,
            voice=voice,
            sample_rate_hz=16000,
            allow_mock_fallback=False,
        )
    )

    started = time.monotonic()
    first = provider.synthesize_delta(EXPECTED_TEXT)
    audio = first + provider.finish()
    while True:
        try:
            audio += provider._audio.get_nowait()
        except Exception:
            break
    metrics = provider.metrics()

    assert downgrade is None
    assert provider.provider_name == "dashscope"
    assert audio
    assert metrics["provider"] == "dashscope"
    assert metrics["sample_rate_hz"] == 16000
    assert "tts_first_audio_latency_ms" in metrics
    wav_path = write_wav("tts-output.wav", audio, sample_rate_hz=16000)
    write_provider_result(
        "tts-result.json",
        {
            "provider": provider.provider_name,
            "model": model,
            "voice": voice,
            "fallback_policy": "fail",
            "ok": True,
            "elapsed_ms": elapsed_ms(started),
            "first_audio_latency_ms": metrics.get("tts_first_audio_latency_ms"),
            "audio_bytes": len(audio),
            "audio_path": str(wav_path),
            "metrics": metrics,
            "error": "",
        },
    )


def test_dashscope_compatible_vision_model_smoke_returns_delta() -> None:
    """测试目标：验证真实 DashScope OpenAI-compatible Vision provider 可定位 smoke。

    测试方法：使用 `DASHSCOPE_API_KEY` 构建 vision model，发送一条短消息并读取首个文本 delta。
    预期结果：返回非空文本；失败时 pytest 输出 provider、model、endpoint 和 timeout。
    """

    model = os.getenv("REALTIME_AGENT_VISION_MODEL", "qwen-plus")
    timeout_seconds = 10
    max_retries = 1
    provider, downgrade = build_vision_model(
        VisionModelProviderConfig(
            provider="dashscope-compatible",
            model=model,
            allow_mock_fallback=False,
            request_timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
    )
    endpoint = getattr(provider, "endpoint", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    result, diagnostic = run_provider_call_with_policy(
        provider="dashscope-compatible",
        model=model,
        endpoint=endpoint,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        allow_mock_fallback=False,
        operation=lambda: next(iter(provider.stream_text("请只回复：ok"))),
    )
    write_provider_result(
        "vision-result.json",
        {
            **diagnostic.as_dict(),
            "first_token_latency_ms": diagnostic.elapsed_ms if diagnostic.ok else None,
            "first_delta": result or "",
        },
    )
    if not diagnostic.ok:
        pytest.fail(
            f"provider=dashscope-compatible model={model} endpoint={endpoint} timeout={timeout_seconds} "
            f"fallback_policy=fail error={diagnostic.error}"
        )

    assert downgrade is None
    assert provider.provider_name == "dashscope-compatible"
    assert result


def test_dashscope_compatible_vision_model_tool_call_smoke() -> None:
    """测试目标：验证真实 DashScope-compatible Vision provider 的 tool calling 输出可解析。

    测试方法：只暴露一个 `lookup_weather` 工具，并要求模型必须调用该工具查询上海天气。
    预期结果：stream 结束后返回 SDK 内部统一 `tool_call` 字典，artifact 记录工具名、参数和延时。
    """

    model = os.getenv("REALTIME_AGENT_VISION_MODEL", "qwen-plus")
    timeout_seconds = float(os.getenv("REALTIME_AGENT_VISION_TOOL_TIMEOUT", "30"))
    max_retries = 1
    provider, downgrade = build_vision_model(
        VisionModelProviderConfig(
            provider="dashscope-compatible",
            model=model,
            allow_mock_fallback=False,
            request_timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
    )
    endpoint = getattr(provider, "endpoint", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup_weather",
                "description": "查询指定城市的实时天气。",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string", "description": "城市名称，例如上海。"}},
                    "required": ["city"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    messages = [
        {
            "role": "user",
            "content": "请必须调用 lookup_weather 工具查询上海天气，不要直接回答天气内容。",
        }
    ]

    result, diagnostic = run_provider_call_with_policy(
        provider="dashscope-compatible",
        model=model,
        endpoint=endpoint,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        allow_mock_fallback=False,
        operation=lambda: list(provider.stream_messages(messages=messages, tools=tools)),
    )
    items = result if isinstance(result, list) else []
    tool_calls = [item for item in items if isinstance(item, dict) and item.get("type") == "tool_call"]
    first_tool_call = tool_calls[0] if tool_calls else None
    ok = bool(diagnostic.ok and first_tool_call and first_tool_call.get("name") == "lookup_weather")
    write_provider_result(
        "vision-tool-call-result.json",
        {
            **diagnostic.as_dict(),
            "ok": ok,
            "item_count": len(items),
            "vision_delta_count": sum(1 for item in items if isinstance(item, str) and item),
            "tool_call_count": len(tool_calls),
            "tool_call": first_tool_call,
            "error": diagnostic.error if diagnostic.error else ("" if ok else "lookup_weather tool_call not found"),
        },
    )
    if not diagnostic.ok:
        pytest.fail(
            f"provider=dashscope-compatible model={model} endpoint={endpoint} timeout={timeout_seconds} "
            f"fallback_policy=fail error={diagnostic.error}"
        )

    assert downgrade is None
    assert provider.provider_name == "dashscope-compatible"
    assert first_tool_call is not None
    assert first_tool_call["name"] == "lookup_weather"


def test_qwen_omni_realtime_provider_smoke_opens_and_closes_session() -> None:
    """测试目标：验证 Omni Manual Realtime provider 真实会话最小 smoke。

    测试方法：用 DashScope SDK 打开 Qwen Omni realtime manual turn detection
    会话，追加一段固定 PCM，显式提交输入并创建响应。
    预期结果：至少记录 provider 事件和输出音频；失败报告 provider、model、endpoint 和 timeout。
    """

    records: list[dict] = []
    audio_outputs: list[bytes] = []
    audio_done: list[dict[str, Any]] = []
    config = RealtimeProviderConfig(
        provider="qwen",
        model=os.getenv("REALTIME_AGENT_REALTIME_MODEL", "qwen3.5-omni-plus-realtime"),
        voice=os.getenv("REALTIME_AGENT_REALTIME_VOICE", "Tina"),
        turn_detection="manual",
    )
    provider = QwenOmniRealtimeAdapter(config)
    callbacks = RealtimeProviderCallbacks(
        audio_delta=lambda audio, _format, metadata: (audio_outputs.append(audio), records.append({"event": "test.audio_delta", **metadata})),
        audio_done=lambda metadata: audio_done.append(metadata),
        provider_event=records.append,
        error=lambda message, metadata: records.append({"event": "provider.error", "message": message, **metadata}),
    )

    started = time.monotonic()
    omni_error = ""
    try:
        if not ASR_SAMPLE.exists():
            _generate_asr_sample()
        provider.open(user_id="integration-user", session_id="integration-omni", callbacks=callbacks)
        provider.append_audio(
            StreamChunk(
                user_id="integration-user",
                session_id="integration-omni",
                stream_id="integration-omni-stream",
                stream_type="sensor.mic",
                seq=0,
                payload=ASR_SAMPLE.read_bytes(),
                final=True,
            )
        )
        provider.create_response(user_id="integration-user", session_id="integration-omni", reason="manual_smoke")
        deadline = time.monotonic() + float(os.getenv("REALTIME_AGENT_REALTIME_SMOKE_TIMEOUT", "12"))
        while time.monotonic() < deadline and not audio_outputs and not _has_provider_error(records):
            time.sleep(0.25)
        provider.close(user_id="integration-user", reason="smoke_done")
    except Exception as exc:  # noqa: BLE001
        omni_error = f"{type(exc).__name__}: {exc}"
        write_provider_result(
            "realtime-result.json",
            _realtime_result(config=config, records=records, audio_outputs=audio_outputs, audio_done=audio_done, started=started, error=omni_error),
        )
        pytest.fail(
            f"provider=qwen model={config.model} endpoint={config.websocket_url} timeout=provider_sdk "
            f"fallback_policy=fail error={type(exc).__name__}: {exc}"
        )

    write_provider_result(
        "realtime-result.json",
        _realtime_result(config=config, records=records, audio_outputs=audio_outputs, audio_done=audio_done, started=started, error=omni_error),
    )
    assert records
    assert not _has_provider_error(records)
    assert audio_outputs


def _generate_asr_sample() -> None:
    TESTDATA_DIR.mkdir(parents=True, exist_ok=True)
    provider, downgrade = build_tts_provider(
        TtsProviderConfig(
            provider="dashscope",
            model=os.getenv("REALTIME_AGENT_TTS_MODEL", "cosyvoice-v3-flash"),
            voice=os.getenv("REALTIME_AGENT_TTS_VOICE", "longanhuan"),
            sample_rate_hz=16000,
            allow_mock_fallback=False,
        )
    )
    pcm = provider.synthesize_delta(EXPECTED_TEXT)
    provider.finish()
    while True:
        try:
            pcm += provider._audio.get_nowait()
        except Exception:
            break
    assert pcm
    ASR_SAMPLE.write_bytes(pcm)
    ASR_EXPECTED.write_text(EXPECTED_TEXT, encoding="utf-8")


def _has_provider_error(records: list[dict]) -> bool:
    """判断 provider 事件列表中是否包含错误事件。"""

    return any(str(item.get("event") or "").endswith(".error") or item.get("event") == "provider.error" for item in records)


def _realtime_result(
    *,
    config: RealtimeProviderConfig,
    records: list[dict],
    audio_outputs: list[bytes],
    audio_done: list[dict[str, Any]],
    started: float,
    error: str,
) -> dict[str, Any]:
    """组装 Realtime provider smoke 的 artifact 内容。"""

    total_audio_bytes = sum(len(item) for item in audio_outputs)
    provider_errors = [
        {
            "event": item.get("event"),
            "message": item.get("message") or item.get("provider_error_message"),
            "provider_error_code": item.get("provider_error_code"),
            "provider_error_type": item.get("provider_error_type"),
            "provider_error_message": item.get("provider_error_message"),
        }
        for item in records
        if str(item.get("event") or "").endswith(".error") or item.get("event") == "provider.error"
    ]
    return {
        "provider": config.provider,
        "model": config.model,
        "endpoint": config.websocket_url,
        "voice": config.voice,
        "max_concurrent_sessions": config.max_concurrent_sessions,
        "fallback_policy": "fail",
        "ok": bool(records and audio_outputs and not error and not _has_provider_error(records)),
        "elapsed_ms": elapsed_ms(started),
        "provider_event_count": len(records),
        "audio_delta_count": len(audio_outputs),
        "audio_bytes": total_audio_bytes,
        "audio_done_count": len(audio_done),
        "provider_errors": provider_errors,
        "events": records[:30],
        "error": error,
    }
