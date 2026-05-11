from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
from urllib.request import urlopen

from audio_chat.config import AudioChatYamlConfig, load_yaml_config
from audio_chat.mcp import McpGateway
from audio_chat.protocol import CONTROL_EVENTS, STREAM_TYPES


def main(argv: list[str] | None = None) -> None:
    """运行 audio-chat 本地预检。

    主要逻辑：
    1. 读取 YAML 配置和 dev_checks。
    2. 聚合协议摘要、包导入、边界、live server 和最近回放检查。
    3. 输出稳定 JSON 报告，供 P0 验收和后续并行开发复用。

    参数：`argv` 为命令行参数。
    返回值：无。
    异常情况：必需检查失败时以非零退出码结束。
    """

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="")
    parser.add_argument("--require-server", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args(argv)

    loaded = load_yaml_config(args.config) if args.config else AudioChatYamlConfig()
    report_path = Path(args.report or loaded.dev_checks.report_path)
    checks = []
    checks.append(_protocol_summary_check())
    checks.append(_config_validation_check(loaded))
    if loaded.dev_checks.run_package_check:
        checks.append(_package_import_check())
    else:
        checks.append(_skipped("package_import", "disabled by dev_checks.run_package_check"))
    if loaded.dev_checks.run_boundary_check:
        checks.append(_boundary_check())
    else:
        checks.append(_skipped("boundary", "disabled by dev_checks.run_boundary_check"))
    if args.require_server:
        checks.append(_live_server_check(loaded))
    else:
        checks.append(_skipped("live_server", "use --require-server to enable"))
    if loaded.dev_checks.require_recent_playback_ok:
        checks.append(_recent_playback_check())
    else:
        checks.append(_skipped("recent_playback", "disabled by dev_checks.require_recent_playback_ok"))
    checks.append(_provider_key_check(loaded))
    checks.append(_provider_runtime_profile_check(loaded))
    checks.append(_mcp_config_check(loaded))
    checks.append(_memory_skill_config_check(loaded))
    checks.append(_endpoint_config_check(loaded))
    checks.append(_audio_pipeline_check(loaded))

    ok = all(check["ok"] for check in checks)
    report = {
        "status": "ok" if ok else "failed",
        "ok": ok,
        "protocol_version": "audio-chat.v1",
        "control_events": sorted(CONTROL_EVENTS),
        "stream_types": sorted(STREAM_TYPES),
        "checks": checks,
        "not_implemented": {
            "audio_pipeline.vad": "server VAD is diagnostic only; Agent/provider still owns turn boundary",
            "audio_pipeline.asr_sidecar": "not implemented",
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"preflight {'ok' if ok else 'failed'}: {report_path}")
    if not ok:
        raise SystemExit(1)


def live_check(argv: list[str] | None = None) -> None:
    """运行面向本地联调的 live-check。

    主要逻辑：
    1. 检查 server health 和 debug devices，但 server 未启动时不把命令本身变成失败。
    2. 汇总最近注册失败、最近 playback、provider key/fallback 和参考端侧配置一致性。
    3. 输出稳定 JSON，帮助开发者判断下一步该启动 server、同步配置还是补 provider key。

    参数：`argv` 为命令行参数。
    返回值：无。
    异常情况：报告文件不可写或配置文件不可解析时抛出异常。
    """

    parser = argparse.ArgumentParser(prog="audio-chat.dev.live-check", description="检查 audio-chat 本地联调状态")
    parser.add_argument("--config", default="examples/for-blind-app/audio-server/server.yaml")
    parser.add_argument("--generated-dir", default="examples/for-blind-app/audio-server/config/generated")
    parser.add_argument("--report", default="runs/default-app/live-check.json")
    args = parser.parse_args(argv)

    loaded = load_yaml_config(args.config)
    checks = [
        _live_server_check(loaded),
        _recent_registration_failures_check(loaded),
        _recent_playback_observation(loaded),
        _provider_key_check(loaded),
        _provider_runtime_profile_check(loaded),
        _mcp_config_check(loaded),
        _memory_skill_config_check(loaded),
        _reference_endpoint_config_consistency_check(Path(args.generated_dir), loaded),
    ]
    blocking_failures = [
        check
        for check in checks
        if not check["ok"] and check["name"] not in {"live_server", "recent_playback", "recent_registration_failures"}
    ]
    report = {
        "ok": not blocking_failures,
        "status": "ok" if not blocking_failures else "failed",
        "server_url": loaded.server.public_url,
        "generated_dir": str(Path(args.generated_dir)),
        "checks": checks,
        "next_actions": _live_check_next_actions(checks),
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if blocking_failures:
        raise SystemExit(1)


def _protocol_summary_check() -> dict:
    """检查协议事件和 stream 类型基础集合。"""

    required_events = {
        "control.device.register.requested",
        "control.device.registered",
        "stream.input.opened",
        "stream.output.open.requested",
    }
    required_streams = {"sensor.rgb", "sensor.imu", "sensor.tof", "actuator.haptic"}
    missing_events = sorted(required_events - set(CONTROL_EVENTS))
    missing_streams = sorted(required_streams - set(STREAM_TYPES))
    return {
        "name": "protocol_summary",
        "ok": not missing_events and not missing_streams,
        "missing_events": missing_events,
        "missing_streams": missing_streams,
    }


def _package_import_check() -> dict:
    """检查公开包和 P0 对象能稳定导入。"""

    required_names = [
        "ArtifactRef",
        "AssetRef",
        "AudioChatApp",
        "AudioChatConfig",
        "AudioChatError",
        "BaseTask",
        "BaseTool",
        "TaskSignal",
        "TaskRef",
        "ToolError",
        "ToolResult",
        "ToolDeviceFacade",
        "TaskDeviceFacade",
    ]
    errors: list[str] = []
    try:
        package = importlib.import_module("audio_chat")
        missing = [name for name in required_names if not hasattr(package, name)]
        errors.extend(f"missing public export: {name}" for name in missing)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return {"name": "package_import", "ok": not errors, "required_names": required_names, "errors": errors}


def _config_validation_check(config: AudioChatYamlConfig) -> dict:
    """检查 YAML 配置中的关键开发者入口字段。

    参数：`config` 为已加载配置。
    返回值：结构化检查结果。
    异常情况：本函数只收集错误，不抛出异常。
    """

    errors: list[str] = []
    if not str(config.server.public_url).startswith(("http://", "https://")):
        errors.append("server.public_url must start with http:// or https://")
    if int(config.server.port) <= 0:
        errors.append("server.port must be positive")
    if config.auth.mode not in {"disabled", "static_token", "signed_token"}:
        errors.append(f"unsupported auth.mode: {config.auth.mode}")
    if config.control.transport != "websocket":
        errors.append(f"unsupported control.transport: {config.control.transport}")
    if config.stream.transport != "websocket_binary":
        errors.append(f"unsupported stream.transport: {config.stream.transport}")
    if config.audio_pipeline.aec != "endpoint_only":
        errors.append("audio_pipeline.aec must be endpoint_only; server cannot replace endpoint AEC")
    if config.agent.mode not in {"text", "realtime_audio"}:
        errors.append(f"unsupported agent.mode: {config.agent.mode}")
    if config.tools.discover.enabled and not config.tools.discover.recursive:
        errors.append("tools.discover.recursive should be true for developer app roots")
    if config.tasks.discover.enabled and not config.tasks.discover.recursive:
        errors.append("tasks.discover.recursive should be true for developer app roots")
    return {"name": "config_validation", "ok": not errors, "errors": errors}


def _boundary_check() -> dict:
    """检查 server SDK 核心包没有把 endpoint 参考实现混进顶层公开 API。"""

    errors: list[str] = []
    try:
        package = importlib.import_module("audio_chat")
        forbidden_names = ["PythonPlaybackEndpoint", "Esp32AecEndpointState"]
        leaked = [name for name in forbidden_names if hasattr(package, name)]
        errors.extend(f"endpoint reference leaked from audio_chat: {name}" for name in leaked)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return {"name": "boundary", "ok": not errors, "errors": errors}


def _provider_key_check(config: AudioChatYamlConfig) -> dict:
    """检查真实 provider 相关环境变量是否可解释。

    主要逻辑：mock provider 不需要 key；DashScope provider 缺 key 时，如果允许 mock
    fallback 则记录降级，否则预检失败。
    """

    required: list[str] = []
    text = config.agent.text
    if (
        text.asr_provider == "dashscope"
        or text.model_provider in {"dashscope", "dashscope-compatible"}
        or text.tts_provider == "dashscope"
    ):
        required.append("DASHSCOPE_API_KEY")
    if text.model_provider == "openai-compatible":
        required.append("OPENAI_API_KEY")
    if config.agent.mode == "realtime_audio" and config.agent.realtime.provider in {"qwen", "dashscope"}:
        required.append("DASHSCOPE_API_KEY")
    required = sorted(set(required))
    missing = [env_name for env_name in required if not os.getenv(env_name)]
    allow_fallback = bool(text.allow_mock_fallback)
    errors = []
    degradations = []
    if missing and allow_fallback:
        degradations.append(f"missing provider keys {missing}; mock fallback is enabled")
    elif missing:
        errors.append(f"missing provider keys {missing}; set key or enable agent.text.allow_mock_fallback")
    return {
        "name": "provider_keys",
        "ok": not errors,
        "required_env": required,
        "missing_env": missing,
        "allow_mock_fallback": allow_fallback,
        "degradations": degradations,
        "errors": errors,
    }


def _provider_runtime_profile_check(config: AudioChatYamlConfig) -> dict:
    """解释当前 provider 环境适合哪类联调。

    主要逻辑：根据真实 key、mock fallback、timeout 和 retry 配置输出 profile，
    让开发者区分 mock、本地联调和真实 provider smoke。
    """

    key_check = _provider_key_check(config)
    text = config.agent.text
    real_provider_requested = any(
        provider not in {"mock", ""}
        for provider in (text.asr_provider, text.model_provider, text.tts_provider)
    ) or (config.agent.mode == "realtime_audio" and config.agent.realtime.provider not in {"mock", ""})
    if not real_provider_requested:
        profile = "mock"
    elif key_check["missing_env"] and text.allow_mock_fallback:
        profile = "local_with_mock_fallback"
    elif key_check["missing_env"]:
        profile = "blocked_missing_provider_key"
    else:
        profile = "real_provider_smoke_ready"
    return {
        "name": "provider_runtime_profile",
        "ok": profile != "blocked_missing_provider_key",
        "profile": profile,
        "providers": {
            "asr": text.asr_provider,
            "text": text.model_provider,
            "tts": text.tts_provider,
            "realtime": config.agent.realtime.provider if config.agent.mode == "realtime_audio" else "",
        },
        "timeout_seconds": text.request_timeout_seconds,
        "max_retries": text.max_retries,
        "allow_mock_fallback": text.allow_mock_fallback,
        "missing_env": key_check["missing_env"],
        "errors": [] if profile != "blocked_missing_provider_key" else key_check["errors"],
    }


def _mcp_config_check(config: AudioChatYamlConfig) -> dict:
    """检查 MCP 配置文件和外部 server smoke 状态。"""

    path = _resolve_audio_chat_path(config.mcp.config_path)
    if not config.mcp.enabled:
        return {
            "name": "mcp_config",
            "ok": True,
            "enabled": False,
            "config_path": str(path),
            "degradations": ["mcp disabled by config"],
            "errors": [],
        }
    if not path.exists():
        return {
            "name": "mcp_config",
            "ok": False,
            "enabled": True,
            "config_path": str(path),
            "server_smoke": [],
            "errors": [f"missing MCP config file: {path}"],
        }
    try:
        gateway = McpGateway(enabled=True, config_path=path, default_timeout_seconds=config.mcp.default_timeout_seconds)
        server_smoke = gateway.smoke_external_servers()
    except Exception as exc:
        return {
            "name": "mcp_config",
            "ok": False,
            "enabled": True,
            "config_path": str(path),
            "server_smoke": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
    errors = [
        f"{item['name']}: {', '.join(item.get('errors') or [])}"
        for item in server_smoke
        if not item.get("ok")
    ]
    return {
        "name": "mcp_config",
        "ok": not errors,
        "enabled": True,
        "config_path": str(path),
        "server_smoke": server_smoke,
        "errors": errors,
    }


def _memory_skill_config_check(config: AudioChatYamlConfig) -> dict:
    """检查 Memory store 和 Skill roots 的配置可解释性。"""

    errors: list[str] = []
    degradations: list[str] = []
    memory_path = _resolve_audio_chat_path(config.memory.path)
    if config.memory.enabled:
        if config.memory.store_type != "jsonl":
            errors.append(f"unsupported memory.store_type: {config.memory.store_type}")
        parent = memory_path.parent if memory_path.suffix else memory_path
        if not parent.exists():
            degradations.append(f"memory path parent will be created at runtime: {parent}")
    else:
        degradations.append("memory disabled by config")
    skill_roots = [_resolve_audio_chat_path(root) for root in config.skill.roots]
    if config.skill.enabled:
        missing = [str(root) for root in skill_roots if not root.exists()]
        errors.extend(f"missing skill root: {root}" for root in missing)
    else:
        degradations.append("skill disabled by config")
    return {
        "name": "memory_skill",
        "ok": not errors,
        "memory": {
            "enabled": config.memory.enabled,
            "store_type": config.memory.store_type,
            "path": str(memory_path),
        },
        "skill": {
            "enabled": config.skill.enabled,
            "roots": [str(root) for root in skill_roots],
        },
        "degradations": degradations,
        "errors": errors,
    }


def _endpoint_config_check(config: AudioChatYamlConfig) -> dict:
    """检查参考端侧默认配置是否具备可联调的最低字段。"""

    defaults = dict(config.endpoint_defaults or {})
    errors: list[str] = []
    wake_word = defaults.get("wake_word")
    if wake_word is not None and wake_word not in {"manual", "browser", "endpoint", "disabled"}:
        errors.append(f"unsupported endpoint_defaults.wake_word: {wake_word}")
    aec = defaults.get("aec")
    if aec is not None and aec not in {"browser_webrtc", "endpoint", "endpoint_only", "disabled"}:
        errors.append(f"unsupported endpoint_defaults.aec: {aec}")
    return {"name": "endpoint_config", "ok": not errors, "endpoint_defaults": defaults, "errors": errors}


def _audio_pipeline_check(config: AudioChatYamlConfig) -> dict:
    """检查 Audio Pipeline 处理器启用和降级状态。

    主要逻辑：resample、volume probe 和 VAD 都必须有明确状态；配置声明启用但代码不支持
    时返回失败或降级原因，避免静默跳过。
    """

    audio = config.audio_pipeline
    stream_format = config.stream.default_sensor_mic
    errors: list[str] = []
    degradations: list[str] = []
    processors = ["format_validator", "pcm16_resampler"]
    if audio.volume_normalize:
        processors.append("volume_probe")
        degradations.append("audio_pipeline.volume_normalize currently records volume metrics only; it does not change PCM samples")
    if audio.vad in {"", "disabled", "off", "false"}:
        degradations.append("audio_pipeline.vad disabled by config")
    elif audio.vad in {"endpoint_or_server", "diagnostic", "server_diagnostic"}:
        processors.append("quality_vad_probe")
        degradations.append("audio_pipeline.vad is diagnostic only; Agent/provider still owns turn boundary")
    elif audio.vad == "provider":
        degradations.append("audio_pipeline.vad delegated to realtime provider")
    else:
        errors.append(f"unsupported audio_pipeline.vad: {audio.vad}")
    if audio.resample in {"auto", "enabled", "server"}:
        if stream_format.get("codec", "pcm16le") != "pcm16le":
            errors.append("audio_pipeline.resample only supports pcm16le")
    elif audio.resample in {"disabled", "off", "false"}:
        degradations.append("audio_pipeline.resample disabled by config")
    else:
        errors.append(f"unsupported audio_pipeline.resample: {audio.resample}")
    if audio.aec != "endpoint_only":
        errors.append("audio_pipeline.aec must stay endpoint_only; server-side AEC is not implemented")
    return {
        "name": "audio_pipeline",
        "ok": not errors,
        "processors": processors,
        "resample": {
            "mode": audio.resample,
            "status": "enabled" if audio.resample in {"auto", "enabled", "server"} else "disabled",
        },
        "volume_probe": {"enabled": bool(audio.volume_normalize), "changes_audio": False},
        "vad": {
            "mode": audio.vad,
            "status": (
                "diagnostic"
                if audio.vad in {"endpoint_or_server", "diagnostic", "server_diagnostic"}
                else "provider"
                if audio.vad == "provider"
                else "disabled"
            ),
            "owns_turn_boundary": False,
        },
        "degradations": degradations,
        "errors": errors,
    }


def _live_server_check(config: AudioChatYamlConfig) -> dict:
    """检查已启动 server 的 health 和 debug devices。"""

    errors: list[str] = []
    server_health = None
    server_debug = None
    try:
        health_url = config.server.public_url.rstrip("/") + "/api/health"
        with urlopen(health_url, timeout=5) as response:
            server_health = json.loads(response.read().decode("utf-8"))
        debug_url = config.server.public_url.rstrip("/") + "/api/debug/devices"
        with urlopen(debug_url, timeout=5) as response:
            server_debug = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return {
        "name": "live_server",
        "ok": not errors,
        "server_health": server_health,
        "server_debug_devices": server_debug,
        "action": "" if not errors else f"启动 server: uv run audio-chat.server.run --config <server.yaml>，当前检查地址 {config.server.public_url}",
        "errors": errors,
    }


def _recent_playback_check() -> dict:
    """检查最近一次 playback result 是否成功。

    主要逻辑：P0 只冻结检查入口，后续 playback 线路可以扩展 result schema。
    """

    roots = [Path("runs"), Path("audio-chat/runs")]
    candidates: list[Path] = []
    for root in roots:
        if root.exists():
            candidates.extend(root.rglob("result.json"))
    if not candidates:
        return {"name": "recent_playback", "ok": False, "errors": ["missing recent playback result.json"]}
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"name": "recent_playback", "ok": False, "path": str(latest), "errors": [f"{type(exc).__name__}: {exc}"]}
    ok = bool(data.get("ok") is True or data.get("status") == "ok")
    return {"name": "recent_playback", "ok": ok, "path": str(latest), "result": data, "errors": [] if ok else ["latest playback result is not ok"]}


def _recent_playback_observation(config: AudioChatYamlConfig) -> dict:
    """读取最近 playback 结果；没有结果时只作为诊断，不阻塞 live-check。

    参数：`config` 为已加载的 server.yaml 配置，用于生成 app_name 相关的提示路径。
    返回值：结构化检查结果。
    异常情况：无。
    """

    result = _recent_playback_check()
    if result["ok"]:
        return result
    generated_dir = Path(config.observability.runs_root) / "generated"
    return {
        **result,
        "ok": True,
        "observed": False,
        "action": f"运行一次 playback: uv run audio-chat.config.sync --output-dir {generated_dir} 后使用生成的 glass.playback.yaml",
    }


def _recent_registration_failures_check(config: AudioChatYamlConfig) -> dict:
    """扫描最近控制事件中的注册失败。

    主要逻辑：读取常见 runs 目录下的 control-events.jsonl，寻找
    `control.device.register.failed` 或注册阶段 `system.error.raised`。
    参数：`config` 为已加载的 server.yaml 配置，用于定位当前 app 的 runs 根目录。
    返回值：结构化检查结果。
    异常情况：无。
    """

    candidates = [
        Path(config.observability.runs_root) / "control-events.jsonl",
        Path("runs/default-app/control-events.jsonl"),
        Path("runs/control-events.jsonl"),
    ]
    failures: list[dict] = []
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_name = str(event.get("event_name") or event.get("event") or "")
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if event_name == "control.device.register.failed" or (
                event_name == "system.error.raised" and "register" in json.dumps(payload, ensure_ascii=False).lower()
            ):
                failures.append({"path": str(path), "event": event})
    return {
        "name": "recent_registration_failures",
        "ok": True,
        "failure_count": len(failures),
        "recent_failures": failures[-5:],
    }


def _reference_endpoint_config_consistency_check(generated_dir: Path, config: AudioChatYamlConfig) -> dict:
    """检查 config.sync 生成的多端配置是否与 server 配置一致。

    参数：`generated_dir` 为 `audio-chat.config.sync` 输出目录。
    返回值：结构化检查结果。
    异常情况：本函数只记录错误，不抛出异常。
    """

    import yaml

    errors: list[str] = []
    expected_server_url = config.server.public_url
    files = {
        "server": generated_dir / "server.local.yaml",
        "phone_mock": generated_dir / "phone.mock.yaml",
        "glass_playback": generated_dir / "glass.playback.yaml",
        "web_glass": generated_dir / "browser-glass.yaml",
        "ios_phone": generated_dir / "ios-phone.local.json",
        "esp32_s3": generated_dir / "esp32-s3.local.env",
    }
    missing = [name for name, path in files.items() if not path.exists()]
    if missing:
        return {
            "name": "endpoint_config_consistency",
            "ok": False,
            "generated_dir": str(generated_dir),
            "missing": missing,
            "errors": [f"missing generated endpoint config: {', '.join(missing)}"],
            "action": f"先运行 audio-chat.config.sync --output-dir {generated_dir}",
        }

    user_ids: set[str] = set()
    device_ids: set[str] = set()
    try:
        server = yaml.safe_load(files["server"].read_text(encoding="utf-8")) or {}
        if server.get("server", {}).get("public_url") != expected_server_url:
            errors.append("server.local.yaml server.public_url differs from live-check config")
        for name in ("phone_mock", "glass_playback", "web_glass"):
            data = yaml.safe_load(files[name].read_text(encoding="utf-8")) or {}
            if data.get("server_url") != expected_server_url:
                errors.append(f"{name} server_url differs from live-check config")
            user_ids.add(str(data.get("user_id", "")))
            device_ids.add(str(data.get("device_id", "")))
        ios = json.loads(files["ios_phone"].read_text(encoding="utf-8"))
        if ios.get("server_url") != expected_server_url:
            errors.append("ios_phone server_url differs from live-check config")
        user_ids.add(str(ios.get("user_id", "")))
        device_ids.add(str(ios.get("device_id", "")))
        esp32_values = _read_env_values(files["esp32_s3"])
        if esp32_values.get("AUDIO_CHAT_SERVER_URL") != expected_server_url:
            errors.append("esp32_s3 AUDIO_CHAT_SERVER_URL differs from live-check config")
        user_ids.add(str(esp32_values.get("AUDIO_CHAT_USER_ID", "")))
        device_ids.add(str(esp32_values.get("AUDIO_CHAT_DEVICE_ID", "")))
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")

    if "" in user_ids:
        errors.append("some endpoint config has empty user_id")
    if "" in device_ids:
        errors.append("some endpoint config has empty device_id")
    if len(user_ids) > 1:
        errors.append(f"endpoint user_id mismatch: {sorted(user_ids)}")
    if len(device_ids) != 5:
        errors.append("endpoint device_id must be unique across phone/playback/web/iOS/ESP32")
    return {
        "name": "endpoint_config_consistency",
        "ok": not errors,
        "generated_dir": str(generated_dir),
        "server_url": expected_server_url,
        "user_ids": sorted(user_ids),
        "device_ids": sorted(device_ids),
        "errors": errors,
    }


def _read_env_values(path: Path) -> dict[str, str]:
    """读取 KEY=VALUE 格式 env 文件。"""

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _live_check_next_actions(checks: list[dict]) -> list[str]:
    """根据检查结果生成可操作下一步。"""

    actions: list[str] = []
    for check in checks:
        action = check.get("action")
        if action:
            actions.append(str(action))
        if check["name"] == "provider_keys" and check.get("missing_env") and check.get("allow_mock_fallback"):
            actions.append("当前缺 provider key 但 mock fallback 已启用；真实 provider 验收前补齐 DASHSCOPE_API_KEY")
    return actions


def _skipped(name: str, reason: str) -> dict:
    return {"name": name, "ok": True, "skipped": True, "reason": reason}


def _resolve_audio_chat_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.exists():
        return path
    if path.parts and path.parts[0] == "audio-chat":
        trimmed = Path(*path.parts[1:])
        if trimmed.exists():
            return trimmed
    return path


if __name__ == "__main__":
    main()
