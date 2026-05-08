"""真实音频样例批量回归工具。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.request import urlopen


def now_ms() -> int:
    """返回当前毫秒时间戳。"""

    return int(time.time() * 1000)


@dataclass(slots=True)
class AudioSampleCase:
    """单条真实音频样例。"""

    sample_name: str
    wav_path: Path


@dataclass(slots=True)
class AudioSampleExecutionResult:
    """单条样例执行结果。"""

    sample_name: str
    wav_path: str
    ok: bool
    returncode: int
    session_id: str = ""
    reply_text: str = ""
    reply_wav_path: str = ""
    result_json_path: str = ""
    agent_session: dict | None = None
    agent_session_fetch_error: str = ""
    assertions_ok: bool = True
    assertion_failures: list[str] = field(default_factory=list)
    expectations: dict = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    started_at_ms: int = 0
    finished_at_ms: int = 0
    duration_ms: int = 0


@dataclass(slots=True)
class AudioSampleBatchSummary:
    """批量执行汇总。"""

    host: str
    port: int
    samples_dir: str
    output_root: str
    total_count: int
    success_count: int
    failure_count: int
    started_at_ms: int
    finished_at_ms: int
    duration_ms: int
    results: list[AudioSampleExecutionResult] = field(default_factory=list)


def repo_root() -> Path:
    """返回当前项目根目录。"""

    return Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="批量执行真实音频样例回归")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--device-id", default="glass-001")
    parser.add_argument("--pair-token", default="pair-demo-token")
    parser.add_argument(
        "--samples-dir",
        default="openaiglass-sdk/tests/data/audio-sample/wav",
        help="待执行 wav 样例目录",
    )
    parser.add_argument(
        "--output-root",
        default="runs/audio-sample-regression",
        help="批量结果输出目录",
    )
    parser.add_argument(
        "--sample",
        action="append",
        default=[],
        help="只执行指定样例名，可重复传入，例如 --sample 你是谁呀",
    )
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    parser.add_argument("--chunk-interval-ms", type=int, default=20)
    parser.add_argument("--fail-fast", action="store_true", help="首条失败后立即停止")
    parser.add_argument("--expectations", default="", help="可选断言 JSON 文件")
    return parser


def resolve_path(raw_path: str) -> Path:
    """把相对路径解析到仓库根目录。"""

    path = Path(raw_path)
    if path.is_absolute():
        return path
    return repo_root() / path


def discover_audio_samples(samples_dir: Path, sample_names: list[str] | None = None) -> list[AudioSampleCase]:
    """发现待执行的真实音频样例。"""

    if not samples_dir.exists():
        raise FileNotFoundError(f"样例目录不存在: {samples_dir}")

    wanted = {name.strip() for name in (sample_names or []) if name.strip()}
    cases: list[AudioSampleCase] = []
    for wav_path in sorted(samples_dir.glob("*.wav")):
        sample_name = wav_path.stem
        if wanted and sample_name not in wanted:
            continue
        cases.append(AudioSampleCase(sample_name=sample_name, wav_path=wav_path))

    if wanted:
        discovered = {case.sample_name for case in cases}
        missing = sorted(wanted - discovered)
        if missing:
            raise FileNotFoundError(f"未找到指定样例: {', '.join(missing)}")
    return cases


def build_reply_path(output_root: Path, sample_name: str) -> Path:
    """构造单条样例的回复音频路径。"""

    return output_root / sample_name / "reply.wav"


def build_result_path(output_root: Path, sample_name: str) -> Path:
    """构造单条样例的结果文件路径。"""

    return output_root / sample_name / "result.json"


def build_client_command(
    *,
    host: str,
    port: int,
    device_id: str,
    pair_token: str,
    wav_path: Path,
    save_reply_path: Path,
    timeout_seconds: float,
    chunk_interval_ms: int,
) -> list[str]:
    """构造单条样例执行命令。"""

    return [
        sys.executable,
        "-m",
        "devtools.simple_glass_audio_client",
        "--host",
        host,
        "--port",
        str(port),
        "--device-id",
        device_id,
        "--pair-token",
        pair_token,
        "--wav",
        str(wav_path),
        "--save-reply",
        str(save_reply_path),
        "--timeout-seconds",
        str(timeout_seconds),
        "--chunk-interval-ms",
        str(chunk_interval_ms),
    ]


def parse_client_stdout(stdout: str) -> tuple[str, str, str]:
    """从 simple_glass_audio_client 输出中解析会话编号、文本回复与回复音频路径。"""

    session_id = ""
    reply_text = ""
    reply_wav_path = ""
    for line in stdout.splitlines():
        if line.startswith("voice_session_open: "):
            session_id = line.split("voice_session_open: ", 1)[1].strip()
        elif line.startswith("reply_text: "):
            reply_text = line.split("reply_text: ", 1)[1].strip()
        elif line.startswith("saved_reply_wav: "):
            reply_wav_path = line.split("saved_reply_wav: ", 1)[1].strip()
    return session_id, reply_text, reply_wav_path


def fetch_agent_session_snapshot(host: str, port: int, session_id: str, timeout_seconds: float) -> dict:
    """从服务端调试接口拉取单轮 agent 会话快照。"""

    query = urlencode({"session_id": session_id})
    with urlopen(f"http://{host}:{port}/api/agent/session?{query}", timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))["session"]


def write_result_json(result_path: Path, result: AudioSampleExecutionResult) -> None:
    """把单条样例结果写入 JSON。"""

    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_summary_json(summary_path: Path, summary: AudioSampleBatchSummary) -> None:
    """把批量执行汇总写入 JSON。"""

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_expectations(path: str | Path | None) -> dict[str, dict]:
    """加载批量回归断言配置。"""

    if not path:
        return {}
    source = resolve_path(str(path))
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("expectations 文件必须是 JSON object")
    defaults = payload.get("defaults", {})
    cases = payload.get("cases", {})
    if defaults is not None and not isinstance(defaults, dict):
        raise ValueError("expectations.defaults 必须是 JSON object")
    if not isinstance(cases, dict):
        raise ValueError("expectations.cases 必须是 JSON object")
    result = {str(name): dict(value) for name, value in cases.items() if isinstance(value, dict)}
    if isinstance(defaults, dict) and defaults:
        result["__defaults__"] = dict(defaults)
    return result


def merge_expectation(expectations: dict[str, dict] | None, sample_name: str) -> dict:
    """合并默认断言和单样例断言。"""

    source = expectations or {}
    merged = dict(source.get("__defaults__", {}))
    merged.update(dict(source.get(sample_name, {})))
    return merged


def evaluate_expectations(*, result: AudioSampleExecutionResult, expectation: dict) -> list[str]:
    """执行单条样例断言。"""

    failures: list[str] = []
    for fragment in _as_string_list(expectation.get("reply_text_contains")):
        if fragment not in result.reply_text:
            failures.append(f"reply_text 缺少片段: {fragment}")
    for fragment in _as_string_list(expectation.get("reply_text_not_contains")):
        if fragment in result.reply_text:
            failures.append(f"reply_text 不应包含片段: {fragment}")

    agent_session = result.agent_session if isinstance(result.agent_session, dict) else {}
    traces = agent_session.get("capability_traces", [])
    trace_items = traces if isinstance(traces, list) else []
    for expected_trace in expectation.get("required_capability_traces", []) or []:
        if not isinstance(expected_trace, dict):
            failures.append("required_capability_traces 条目必须是对象")
            continue
        if not _trace_matches(trace_items, expected_trace):
            failures.append(f"缺少能力调用轨迹: {json.dumps(expected_trace, ensure_ascii=False)}")

    model_request_text = json.dumps(agent_session.get("model_request", {}), ensure_ascii=False, default=str)
    for fragment in _as_string_list(expectation.get("model_request_contains")):
        if fragment not in model_request_text:
            failures.append(f"model_request 缺少片段: {fragment}")
    return failures


def _as_string_list(value: object) -> list[str]:
    """把断言字段归一化为字符串列表。"""

    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _trace_matches(trace_items: list, expected_trace: dict) -> bool:
    """判断能力轨迹中是否存在匹配项。"""

    for item in trace_items:
        if not isinstance(item, dict):
            continue
        if all(item.get(key) == expected_value for key, expected_value in expected_trace.items()):
            return True
    return False


def run_audio_sample_case(
    *,
    case: AudioSampleCase,
    host: str,
    port: int,
    device_id: str,
    pair_token: str,
    output_root: Path,
    timeout_seconds: float,
    chunk_interval_ms: int,
    expectation: dict | None = None,
    session_fetcher: Callable[[str, int, str, float], dict] = fetch_agent_session_snapshot,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> AudioSampleExecutionResult:
    """执行单条真实音频样例。"""

    reply_path = build_reply_path(output_root, case.sample_name)
    reply_path.parent.mkdir(parents=True, exist_ok=True)

    command = build_client_command(
        host=host,
        port=port,
        device_id=device_id,
        pair_token=pair_token,
        wav_path=case.wav_path,
        save_reply_path=reply_path,
        timeout_seconds=timeout_seconds,
        chunk_interval_ms=chunk_interval_ms,
    )
    started_at_ms = now_ms()
    process = executor(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    finished_at_ms = now_ms()
    session_id, reply_text, parsed_reply_path = parse_client_stdout(process.stdout)
    result_path = build_result_path(output_root, case.sample_name)
    agent_session = None
    agent_session_fetch_error = ""
    if session_id and process.returncode == 0:
        try:
            agent_session = session_fetcher(host, port, session_id, timeout_seconds)
        except Exception as exc:  # pragma: no cover - 仅在外部联调时触发
            agent_session_fetch_error = str(exc)
    result = AudioSampleExecutionResult(
        sample_name=case.sample_name,
        wav_path=str(case.wav_path),
        ok=process.returncode == 0,
        returncode=process.returncode,
        session_id=session_id,
        reply_text=reply_text,
        reply_wav_path=parsed_reply_path or str(reply_path),
        result_json_path=str(result_path),
        agent_session=agent_session,
        agent_session_fetch_error=agent_session_fetch_error,
        stdout=process.stdout,
        stderr=process.stderr,
        started_at_ms=started_at_ms,
        finished_at_ms=finished_at_ms,
        duration_ms=finished_at_ms - started_at_ms,
        expectations=dict(expectation or {}),
    )
    if expectation:
        result.assertion_failures = evaluate_expectations(result=result, expectation=expectation)
        result.assertions_ok = not result.assertion_failures
        result.ok = result.ok and result.assertions_ok
    write_result_json(result_path, result)
    return result


def run_audio_sample_batch(
    *,
    host: str,
    port: int,
    device_id: str,
    pair_token: str,
    samples_dir: Path,
    output_root: Path,
    sample_names: list[str] | None = None,
    timeout_seconds: float = 45.0,
    chunk_interval_ms: int = 20,
    fail_fast: bool = False,
    expectations: dict[str, dict] | None = None,
    session_fetcher: Callable[[str, int, str, float], dict] = fetch_agent_session_snapshot,
    executor: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> AudioSampleBatchSummary:
    """批量执行真实音频样例。"""

    cases = discover_audio_samples(samples_dir, sample_names)
    started_at_ms = now_ms()
    results: list[AudioSampleExecutionResult] = []

    for case in cases:
        result = run_audio_sample_case(
            case=case,
            host=host,
            port=port,
            device_id=device_id,
            pair_token=pair_token,
            output_root=output_root,
            timeout_seconds=timeout_seconds,
            chunk_interval_ms=chunk_interval_ms,
            expectation=merge_expectation(expectations, case.sample_name),
            session_fetcher=session_fetcher,
            executor=executor,
        )
        results.append(result)
        if fail_fast and not result.ok:
            break

    finished_at_ms = now_ms()
    summary = AudioSampleBatchSummary(
        host=host,
        port=port,
        samples_dir=str(samples_dir),
        output_root=str(output_root),
        total_count=len(results),
        success_count=sum(1 for item in results if item.ok),
        failure_count=sum(1 for item in results if not item.ok),
        started_at_ms=started_at_ms,
        finished_at_ms=finished_at_ms,
        duration_ms=finished_at_ms - started_at_ms,
        results=results,
    )
    write_summary_json(output_root / "summary.json", summary)
    return summary


def print_summary(summary: AudioSampleBatchSummary) -> None:
    """打印批量执行摘要。"""

    print(
        "audio_sample_batch_summary: "
        f"total={summary.total_count} "
        f"success={summary.success_count} "
        f"failure={summary.failure_count} "
        f"output_root={summary.output_root}"
    )
    for result in summary.results:
        status = "OK" if result.ok else "FAIL"
        print(
            f"[{status}] sample={result.sample_name} "
            f"assertions_ok={result.assertions_ok} "
            f"reply={result.reply_text or '<empty>'} "
            f"result_json={result.result_json_path}"
        )
        for failure in result.assertion_failures:
            print(f"  assertion_failure: {failure}")


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""

    parser = build_parser()
    args = parser.parse_args(argv)
    samples_dir = resolve_path(args.samples_dir)
    output_root = resolve_path(args.output_root)

    summary = run_audio_sample_batch(
        host=args.host,
        port=args.port,
        device_id=args.device_id,
        pair_token=args.pair_token,
        samples_dir=samples_dir,
        output_root=output_root,
        sample_names=args.sample,
        timeout_seconds=args.timeout_seconds,
        chunk_interval_ms=args.chunk_interval_ms,
        fail_fast=args.fail_fast,
        expectations=load_expectations(args.expectations),
    )
    print_summary(summary)
    return 0 if summary.failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
