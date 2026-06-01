from __future__ import annotations

import json
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LAYER_REPORTS = {
    "protocol_spec": "protocol-spec-report.json",
    "protocol": "l0-protocol-report.json",
    "interop": "l1-interop-report.json",
    "sdk": "l1-server-sdk-report.json",
    "device_sdk": "l1-device-sdk-report.json",
    "model_provider": "l2-model-provider-report.json",
    "app": "l3-app-report.json",
    "replay": "l3-replay-report.json",
    "hardware": "l3-hardware-report.json",
}


def pytest_sessionstart(session) -> None:
    """记录 pytest 会话开始时间，供分层测试报告使用。"""

    session.config._realtime_agent_started_at = datetime.now(timezone.utc)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """为按 marker 执行的系统级回归测试输出轻量 JSON 报告。

    主要逻辑：根据 `-m` marker 判断当前测试层级，汇总 pytest 结果和关键输入资产，
    写入 `runs/regression-reports/latest/`。默认未使用分层 marker 时仍写 `all` 报告，
    但不会改变 pytest 的退出码和测试语义。
    """

    markexpr = str(getattr(config.option, "markexpr", "") or "").strip()
    layer = _layer_from_markexpr(markexpr)
    report_dir = Path(os.getenv("REALTIME_AGENT_TEST_REPORT_DIR", "runs/regression-reports/latest"))
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / LAYER_REPORTS.get(layer, "all-report.json")
    started_at = getattr(config, "_realtime_agent_started_at", datetime.now(timezone.utc))
    finished_at = datetime.now(timezone.utc)
    counts = _pytest_counts(terminalreporter.stats)
    report = {
        "layer": layer,
        "marker": markexpr,
        "command": " ".join(sys.argv),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "status": _status_from_exitstatus(exitstatus, counts),
        **counts,
        "inputs": _inputs_for_layer(layer),
        "artifacts": _artifacts_for_layer(layer, report_path),
        "failures": _failure_summaries(terminalreporter.stats),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "has_dashscope_api_key": bool(os.getenv("DASHSCOPE_API_KEY")),
            "cwd": str(Path.cwd()),
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_summary(report_dir)


def _layer_from_markexpr(markexpr: str) -> str:
    for marker in sorted(LAYER_REPORTS, key=len, reverse=True):
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(marker)}(?![A-Za-z0-9_])", markexpr):
            return marker
    for marker in sorted(LAYER_REPORTS, key=len, reverse=True):
        if marker in markexpr:
            return marker
    return "all"


def _pytest_counts(stats: dict[str, list[Any]]) -> dict[str, int]:
    return {
        "total": sum(len(stats.get(name, [])) for name in ("passed", "failed", "error", "skipped", "xfailed", "xpassed")),
        "passed": len(stats.get("passed", [])),
        "failed": len(stats.get("failed", [])) + len(stats.get("error", [])),
        "skipped": len(stats.get("skipped", [])),
    }


def _status_from_exitstatus(exitstatus: int, counts: dict[str, int]) -> str:
    if counts["total"] == counts["skipped"] and counts["total"] > 0:
        return "skipped"
    if exitstatus == 0:
        return "passed"
    if counts["passed"] > 0 or counts["skipped"] > 0:
        return "partial"
    return "failed"


def _inputs_for_layer(layer: str) -> dict[str, Any]:
    root = Path.cwd()
    spec_root = root / "agent-server/realtime_agent/spec"
    protocol_root = root / "protocol/data/fixtures"
    inputs: dict[str, Any] = {
        "schemas": sorted(str(path) for path in spec_root.glob("realtime-agent-*")),
        "protocol_versions": {
            "data": str(root / "protocol/data/version.json"),
            "behavior": str(root / "protocol/behavior/version.json"),
        },
        "protocol_fixtures": {
            "devices": _count_files(protocol_root / "devices"),
            "events": _count_files(protocol_root / "events"),
            "streams": _count_files(protocol_root / "streams"),
            "invalid_devices": _count_files(protocol_root / "invalid/devices"),
            "invalid_events": _count_files(protocol_root / "invalid/events"),
            "invalid_streams": _count_files(protocol_root / "invalid/streams"),
        },
    }
    if layer == "protocol_spec":
        inputs["protocol_document"] = str(root / "protocol/docs/protocol.md")
    if layer == "model_provider":
        inputs["providers"] = {
            "dashscope": bool(os.getenv("DASHSCOPE_API_KEY")),
            "openai_compatible": bool(os.getenv("OPENAI_API_KEY")),
        }
    if layer in {"app", "replay", "hardware"}:
        inputs["app"] = {
            "name": "device_demo",
            "root": str(root / "examples/device_demo"),
            "audio_samples": sorted(str(path) for path in (root / "testdata/audio-sample").glob("*.wav")),
            "image_samples": sorted(str(path) for path in (root / "testdata/image-sample").glob("*")),
            "video_samples": sorted(str(path) for path in (root / "testdata/video-sample").glob("*")),
            "device_references": [
                "examples/dev-support/devices/browser-glass",
                "examples/dev-support/devices/python-glass",
                "examples/dev-support/devices/python-phone",
                "examples/device_demo/ios",
            ],
            "manual_acceptance_gap": "iOS/ESP32 真机和浏览器摄像头权限需要人工联调确认",
        }
    return inputs


def _artifacts_for_layer(layer: str, report_path: Path) -> dict[str, str]:
    """返回当前分层测试报告关联的运行产物路径。

    主要逻辑：通用层级只记录报告文件；L2 大模型接入层额外记录真实 provider
    smoke 的 artifact 目录，方便从 `l2-model-provider-report.json` 跳转到音频、
    latency 和 provider event 证据。
    参数：`layer` 为 marker 推断出的测试层级，`report_path` 为本层报告路径。
    返回值：artifact 名称到路径的映射。
    异常情况：无。
    """

    artifacts = {"report": str(report_path)}
    if layer == "model_provider":
        artifacts["provider_tests"] = os.getenv("REALTIME_AGENT_PROVIDER_TEST_RUNS", "runs/provider-tests/latest")
    return artifacts


def _count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.iterdir() if item.is_file())


def _failure_summaries(stats: dict[str, list[Any]]) -> list[dict[str, str]]:
    failures = []
    for name in ("failed", "error"):
        for report in stats.get(name, [])[:20]:
            failures.append(
                {
                    "nodeid": getattr(report, "nodeid", ""),
                    "phase": getattr(report, "when", ""),
                    "summary": str(getattr(report, "longreprtext", ""))[:1200],
                }
            )
    return failures


def _write_summary(report_dir: Path) -> None:
    reports = sorted(report_dir.glob("*-report.json"))
    lines = ["# realtime-agent 回归测试报告", "", f"更新时间：{datetime.now(timezone.utc).isoformat()}", ""]
    for path in reports:
        data = json.loads(path.read_text(encoding="utf-8"))
        lines.append(
            f"- `{data['layer']}`: {data['status']} "
            f"(total={data['total']}, passed={data['passed']}, failed={data['failed']}, skipped={data['skipped']})"
        )
    lines.append("")
    report_dir.joinpath("summary.md").write_text("\n".join(lines), encoding="utf-8")
