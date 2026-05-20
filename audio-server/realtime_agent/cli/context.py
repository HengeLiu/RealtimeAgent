from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from realtime_agent.agent_core.context import ContextCompileRequest, ContextCompiler
from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig


def inspect() -> None:
    """打印当前配置下最终模型上下文。

    主要功能：给开发者本地查看 instructions、messages、tools 和 source map。
    参数：通过命令行传入 config、mode、user_id、session_id 和可选Vision 输入。
    返回值：无；结果输出到 stdout。
    异常情况：配置或 prompt registry 错误时抛出并由命令行显示。
    """

    parser = argparse.ArgumentParser(prog="realtime-agent.context.inspect")
    parser.add_argument("--config", required=True, help="server.yaml 路径")
    parser.add_argument("--mode", choices=["vision", "omni"], default="", help="要检查的 Agent 模式")
    parser.add_argument("--user-id", default="inspect-user", help="上下文检查使用的用户 ID")
    parser.add_argument("--session-id", default="inspect-device", help="上下文检查使用的会话 / 设备 ID")
    parser.add_argument("--text", default="你好", help="vision 模式当前用户输入")
    parser.add_argument("--compare-model-request", default="", help="可选：与已有 model-request.json 做摘要 diff")
    args = parser.parse_args()

    config = RealtimeAgentConfig.from_yaml(Path(args.config))
    app = RealtimeAgentApp(config)
    mode = args.mode or ("omni" if config.agent_mode == "omni" else "vision")
    compiler = ContextCompiler()
    if mode == "vision":
        provider = config.vision_provider
        model = config.vision_model
        base_instructions = config.vision_prompt
        current_input: dict[str, Any] = {"type": "text", "transcript": args.text}
        include_realtime_tool_rules = False
    else:
        provider = config.omni_provider
        model = config.omni_model
        base_instructions = config.omni_prompt
        current_input = {"type": "input_audio_stream", "stream_type": "sensor.mic"}
        include_realtime_tool_rules = True
    context = compiler.compile(
        ContextCompileRequest(
            mode=mode,
            provider=provider,
            model=model,
            user_id=args.user_id,
            session_id=args.session_id,
            base_instructions=base_instructions,
            current_input=current_input,
            include_tools=True,
            include_realtime_tool_rules=include_realtime_tool_rules,
            reason="context_inspect_cli",
            memory_service=app.memory_service,
            control_service=app.control_service,
            tool_gateway=app.tool_gateway,
            max_context_messages=config.vision_max_context_messages,
        )
    )
    payload = {
        "mode": context.mode,
        "provider": context.provider,
        "model": context.model,
        "instructions": context.instructions,
        "messages": context.messages,
        "tools": context.tools,
        "tool_count": len(context.tools),
        "modal_inputs": context.modal_inputs,
        "prompts": context.prompt_records(),
        "context_sources": context.source_records(),
        "warnings": context.warnings,
        "truncations": context.truncations,
        "metadata": context.metadata,
    }
    if args.compare_model_request:
        payload["diff"] = _diff_model_request(payload, Path(args.compare_model_request))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _diff_model_request(current: dict[str, Any], previous_path: Path) -> dict[str, Any]:
    """对比当前上下文和已有 model-request.json。

    主要逻辑：只做排障友好的摘要级 diff，不做大段文本逐字符 diff。
    参数：`current` 为当前 CLI 编译结果；`previous_path` 为历史 model-request.json。
    返回值：包含 prompt、message、tool、source map 变化的摘要。
    异常情况：历史文件不存在或 JSON 非法时返回 error 字段。
    """

    if not previous_path.is_file():
        return {"error": f"compare file not found: {previous_path}"}
    try:
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - CLI 需要把错误写入 JSON 结果
        return {"error": f"compare file cannot be read as JSON: {exc}"}
    previous_tools = _tool_names(previous.get("tools") or [])
    current_tools = _tool_names(current.get("tools") or [])
    previous_sources = _source_names(previous.get("context_sources") or [])
    current_sources = _source_names(current.get("context_sources") or [])
    return {
        "instructions_changed": str(previous.get("prompt") or previous.get("instructions") or "") != str(current.get("instructions") or ""),
        "message_count": {
            "previous": len(previous.get("messages") or []),
            "current": len(current.get("messages") or []),
        },
        "tool_count": {
            "previous": len(previous_tools),
            "current": len(current_tools),
        },
        "tools_added": sorted(set(current_tools) - set(previous_tools)),
        "tools_removed": sorted(set(previous_tools) - set(current_tools)),
        "sources_added": sorted(set(current_sources) - set(previous_sources)),
        "sources_removed": sorted(set(previous_sources) - set(current_sources)),
    }


def _tool_names(tools: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for item in tools:
        function = item.get("function") if isinstance(item, dict) else None
        if isinstance(function, dict):
            names.append(str(function.get("name") or ""))
        elif isinstance(item, dict):
            names.append(str(item.get("name") or ""))
    return [name for name in names if name]


def _source_names(sources: list[dict[str, Any]]) -> list[str]:
    return [
        str(item.get("source_name") or item.get("source_id") or "")
        for item in sources
        if isinstance(item, dict) and (item.get("source_name") or item.get("source_id"))
    ]
