from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

from openai import OpenAI


DEFAULT_TEXT = "帮我看看红绿灯我现在可以过马路了吗"
DEFAULT_REQUEST = (
    "examples/for-blind-app/agent-server/runs/"
    "user-browser-glass-001/dev-browser-glass-001/model-request.json"
)


def main() -> int:
    """运行红绿灯任务工具调用稳定性诊断。

    功能：直接调用 OpenAI-compatible Chat Completions API，重复发送同一条用户文本，
    统计模型是否返回 `start_traffic_light_task` 工具调用。
    主要逻辑：从最近一次 `model-request.json` 复用生产 system prompt、模型名、工具
    schema 和 provider 参数；分别测试干净上下文与回放上下文。
    参数：通过命令行传入迭代次数、请求快照路径、输出路径和模型文本。
    返回值：进程退出码，0 表示脚本执行完成。
    异常情况：缺少 API Key、请求快照或 provider 调用失败时记录到结果中。
    """

    args = _parse_args()
    request_path = Path(args.request).expanduser().resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    api_key = os.getenv(args.api_key_env) or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit(f"{args.api_key_env} or OPENAI_API_KEY is required")

    output_path = Path(args.output).expanduser().resolve() if args.output else _default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    client = OpenAI(
        api_key=api_key,
        base_url=args.base_url or _request_endpoint(request),
        timeout=float(args.timeout_seconds),
        max_retries=int(args.max_retries),
    )
    model = args.model or str(request.get("model") or "qwen3.6-flash")
    tools = list(request.get("tools") or [])
    prompt = _system_prompt(request)
    extra_body = _extra_body(request)
    scenarios = {
        "clean": [{"role": "system", "content": prompt}, {"role": "user", "content": args.text}],
        "replay": _replay_messages(request, text=args.text),
    }

    all_results: dict[str, Any] = {
        "text": args.text,
        "model": model,
        "request_path": str(request_path),
        "base_url": args.base_url or _request_endpoint(request),
        "iterations": int(args.iterations),
        "target_tool": args.target_tool,
        "extra_body": extra_body,
        "scenarios": {},
    }
    for scenario_name, messages in scenarios.items():
        results = [
            _call_once(
                client=client,
                model=model,
                messages=messages,
                tools=tools,
                extra_body=extra_body,
                target_tool=args.target_tool,
            )
            for _ in range(int(args.iterations))
        ]
        all_results["scenarios"][scenario_name] = {
            "summary": _summarize(results, target_tool=args.target_tool),
            "results": results,
        }

    output_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(all_results, output_path=output_path)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="测试红绿灯请求触发后台 Task 工具调用的稳定性。")
    parser.add_argument("--request", default=DEFAULT_REQUEST, help="生产 model-request.json 快照路径。")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="重复发送给模型的用户文本。")
    parser.add_argument("--iterations", type=int, default=20, help="每个场景重复次数。")
    parser.add_argument("--target-tool", default="start_traffic_light_task", help="目标工具名。")
    parser.add_argument("--model", default="", help="覆盖请求快照中的模型名。")
    parser.add_argument("--base-url", default="", help="覆盖请求快照中的 OpenAI-compatible base_url。")
    parser.add_argument("--api-key-env", default="DASHSCOPE_API_KEY", help="API Key 环境变量名。")
    parser.add_argument("--timeout-seconds", type=float, default=30.0, help="单次请求超时时间。")
    parser.add_argument("--max-retries", type=int, default=0, help="OpenAI SDK 重试次数。")
    parser.add_argument("--output", default="", help="输出 JSON 路径；默认写入 runs/diagnostics。")
    return parser.parse_args()


def _request_endpoint(request: dict[str, Any]) -> str:
    options = request.get("provider_request_options") or {}
    return str(options.get("endpoint") or "https://dashscope.aliyuncs.com/compatible-mode/v1")


def _extra_body(request: dict[str, Any]) -> dict[str, Any]:
    options = request.get("provider_request_options") or {}
    extra_body = options.get("extra_body")
    return dict(extra_body) if isinstance(extra_body, dict) else {"enable_thinking": False}


def _system_prompt(request: dict[str, Any]) -> str:
    messages = request.get("messages") or []
    for message in messages:
        if message.get("role") == "system":
            return str(message.get("content") or "")
    return str(request.get("prompt") or "")


def _replay_messages(request: dict[str, Any], *, text: str) -> list[dict[str, Any]]:
    messages = [dict(item) for item in request.get("messages") or [] if isinstance(item, dict)]
    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": _system_prompt(request)})
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user":
            messages[index] = {**messages[index], "content": text}
            return messages
    messages.append({"role": "user", "content": text})
    return messages


def _call_once(
    *,
    client: OpenAI,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    extra_body: dict[str, Any],
    target_tool: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools or None,
            extra_body=extra_body,
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        choice = response.choices[0]
        message = choice.message
        tool_calls = [
            {
                "id": getattr(tool_call, "id", ""),
                "name": getattr(getattr(tool_call, "function", None), "name", ""),
                "arguments": getattr(getattr(tool_call, "function", None), "arguments", ""),
            }
            for tool_call in (getattr(message, "tool_calls", None) or [])
        ]
        return {
            "ok": True,
            "elapsed_ms": elapsed_ms,
            "finish_reason": getattr(choice, "finish_reason", ""),
            "content": getattr(message, "content", "") or "",
            "tool_calls": tool_calls,
            "target_called": any(item.get("name") == target_tool for item in tool_calls),
        }
    except Exception as exc:  # noqa: BLE001 - 诊断脚本需要完整记录 provider 异常
        return {
            "ok": False,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "tool_calls": [],
            "target_called": False,
        }


def _summarize(results: list[dict[str, Any]], *, target_tool: str) -> dict[str, Any]:
    ok_results = [item for item in results if item.get("ok")]
    latencies = [float(item["elapsed_ms"]) for item in ok_results]
    target_called = [item for item in ok_results if item.get("target_called")]
    any_tool = [item for item in ok_results if item.get("tool_calls")]
    text_only = [item for item in ok_results if not item.get("tool_calls")]
    return {
        "total": len(results),
        "ok": len(ok_results),
        "errors": len(results) - len(ok_results),
        "target_tool": target_tool,
        "target_tool_calls": len(target_called),
        "any_tool_calls": len(any_tool),
        "text_only": len(text_only),
        "target_tool_call_rate": round(len(target_called) / len(ok_results), 4) if ok_results else 0,
        "latency_ms": _latency_stats(latencies),
        "text_only_samples": [str(item.get("content") or "")[:120] for item in text_only[:5]],
        "non_target_tool_names": sorted(
            {
                call.get("name")
                for item in any_tool
                for call in item.get("tool_calls", [])
                if call.get("name") != target_tool
            }
        ),
    }


def _latency_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    sorted_values = sorted(values)
    p95_index = min(len(sorted_values) - 1, int(round((len(sorted_values) - 1) * 0.95)))
    return {
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "mean": round(statistics.mean(values), 2),
        "median": round(statistics.median(values), 2),
        "p95": round(sorted_values[p95_index], 2),
    }


def _default_output_path() -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    return Path("runs/diagnostics") / f"traffic-light-tool-call-stability-{stamp}.json"


def _print_summary(report: dict[str, Any], *, output_path: Path) -> None:
    print(f"output={output_path}")
    for name, payload in report["scenarios"].items():
        summary = payload["summary"]
        latency = summary.get("latency_ms") or {}
        print(
            f"{name}: total={summary['total']} ok={summary['ok']} errors={summary['errors']} "
            f"target_calls={summary['target_tool_calls']} text_only={summary['text_only']} "
            f"rate={summary['target_tool_call_rate']:.2%} "
            f"latency_mean_ms={latency.get('mean', 0)} latency_p95_ms={latency.get('p95', 0)}"
        )
        for sample in summary.get("text_only_samples") or []:
            print(f"  text_only_sample={sample}")


if __name__ == "__main__":
    raise SystemExit(main())
