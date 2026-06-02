from __future__ import annotations

import asyncio
import contextlib
import wave
from pathlib import Path

from aiohttp import ClientSession

from .assertions import assert_case
from .case_schema import PlaybackCase, PlaybackSuite, load_case
from .protocol_client import PlaybackProtocolClient
from .report import CaseReport


def resolve_repo_path(path: str | Path, *, base: Path | None = None) -> Path:
    """解析仓库内相对路径。"""

    value = Path(path).expanduser()
    if value.is_absolute():
        return value
    for candidate in [*(([(base / value).resolve()] if base else [])), (Path.cwd() / value).resolve()]:
        if candidate.exists():
            return candidate
    return (Path.cwd() / value).resolve()


def load_wav_pcm(path: str | Path) -> tuple[bytes, int]:
    """读取单声道 16-bit WAV，返回 PCM 和采样率。"""

    with wave.open(str(path), "rb") as wav_file:
        if wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2:
            raise ValueError(f"only mono pcm16 wav is supported: {path}")
        return wav_file.readframes(wav_file.getnframes()), wav_file.getframerate()


async def run_case(case: PlaybackCase, *, server_url: str, runs_root: str | Path | None, report_dir: Path | None = None) -> CaseReport:
    """通过真实协议执行单个 Case。"""

    client = PlaybackProtocolClient(server_url=server_url, device=case.device)
    audio_session_opened = False
    async with ClientSession() as session:
        try:
            await client.connect(session)
            audio_input = case.inputs.get("audio") or {}
            if not audio_input.get("path"):
                assertion = assert_case(case, runs_root=runs_root, stats=client.stats)
                return CaseReport.from_assertion(case=case, assertion_result=assertion, stats=client.stats, report_dir=report_dir)
            if audio_input.get("path"):
                await client.send_event(client.event("control.user.wake.detected", {"wake_source": "python_playback_glass"}))
            audio_task: asyncio.Task[None] | None = None
            speaker_streams: set[str] = set()
            deadline = asyncio.get_running_loop().time() + case.timeout_seconds
            while asyncio.get_running_loop().time() < deadline:
                try:
                    item = await client.receive_control_event(timeout=1)
                except asyncio.TimeoutError:
                    if audio_task and audio_task.done() and client.stats.output_chunks and _case_can_finish(case, client, speaker_streams):
                        break
                    continue
                name = item.get("event_name")
                if name == "control.audio_session.open.requested":
                    await client.send_event(client.event("control.audio_session.opened", {"reason": "python_playback_glass_opened"}, session_id=client.device_id))
                    audio_session_opened = True
                    audio = case.inputs.get("audio") or {}
                    if audio.get("path"):
                        wav_path = resolve_repo_path(str(audio["path"]), base=case.path.parent)
                        pcm, sample_rate = load_wav_pcm(wav_path)
                        audio_task = asyncio.create_task(
                            client.send_mic_audio(
                                session,
                                pcm=pcm,
                                sample_rate=sample_rate,
                                chunk_ms=int(audio.get("chunk_ms") or 20),
                                source_path=str(wav_path),
                            )
                        )
                elif name == "stream.output.start.requested" and item.get("stream_type") == "actuator.speaker":
                    await client.ensure_stream(session)
                    stream_id = str(item.get("stream_id"))
                    speaker_streams.add(stream_id)
                    await client.send_event(
                        client.event(
                            "stream.output.ready",
                            {"stream_type": "actuator.speaker", "reason": "python_playback_glass_ready"},
                            session_id=client.device_id,
                            stream_id=stream_id,
                            stream_type="actuator.speaker",
                        )
                    )
                    asyncio.create_task(_drain_stream(client, stream_id=stream_id))
                elif name == "stream.output.close.requested" and item.get("stream_type") == "actuator.speaker":
                    await client.close_output(str(item.get("stream_id")))
                elif name == "stream.control.open.requested" and item.get("stream_type") == "sensor.rgb":
                    await client.send_rgb_fixture(session, request_event=item, image_path=_select_sensor_fixture(case, "sensor.rgb"))
                elif name == "control.audio_session.close.requested":
                    await client.send_event(client.event("control.audio_session.closed", {"reason": "python_playback_glass_closed"}, session_id=client.device_id))
                    audio_session_opened = False
                    break
            if audio_task:
                await asyncio.wait_for(audio_task, timeout=5)
            for stream_id in speaker_streams:
                await client.close_output(stream_id)
        finally:
            if audio_session_opened:
                with contextlib.suppress(Exception):
                    await client.send_event(client.event("control.audio_session.closed", {"reason": "python_playback_glass_finished"}, session_id=client.device_id))
            await client.close()
    return CaseReport.from_assertion(case=case, assertion_result=assert_case(case, runs_root=runs_root, stats=client.stats), stats=client.stats, report_dir=report_dir)


async def _drain_stream(client: PlaybackProtocolClient, *, stream_id: str) -> None:
    """后台接收 speaker chunk，直到超时。"""

    timeout = 8
    while True:
        try:
            chunk = await client.receive_stream_chunk(timeout=timeout)
        except Exception:
            return
        timeout = 1
        if chunk.get("stream_id") == stream_id and chunk.get("final"):
            await client.close_output(stream_id)
            return


def _select_sensor_fixture(case: PlaybackCase, stream_type: str) -> Path:
    """从 Case 中选择指定 stream 的第一个 fixture。"""

    fixtures = ((case.inputs.get("sensors") or {}).get(stream_type) or {}).get("fixtures") or []
    if not fixtures:
        raise RuntimeError(f"no fixture configured for {stream_type}: {case.path}")
    return resolve_repo_path(str(fixtures[0]["path"]), base=case.path.parent)


def _case_can_finish(case: PlaybackCase, client: PlaybackProtocolClient, speaker_streams: set[str]) -> bool:
    """判断当前回放 case 是否已经可以自动收口。

    主要逻辑：普通问答只要麦克风发完并收到 speaker 输出即可结束；带传感器 fixture
    的 case 需要至少完成一次 asset 上传，并等待工具后的第二段 speaker 输出，避免
    把工具 progress audio 误当成最终回答。
    """

    sensor_inputs = case.inputs.get("sensors") or {}
    if not sensor_inputs:
        return True
    if not client.stats.asset_uploads:
        return False
    return len(speaker_streams) >= 2


async def run_suite(suite: PlaybackSuite, *, server_url: str, runs_root: str | Path | None, report_dir: Path | None, fail_fast: bool = False) -> list[CaseReport]:
    """顺序执行 suite 中的 Case。"""

    reports = []
    for case_path in suite.cases:
        report = await run_case(load_case(case_path), server_url=server_url, runs_root=runs_root, report_dir=report_dir)
        reports.append(report)
        if fail_fast and not report.ok:
            break
    return reports


def run_case_sync(case: PlaybackCase, *, server_url: str, runs_root: str | Path | None, report_dir: Path | None = None) -> CaseReport:
    """同步包装，供 CLI 使用。"""

    return asyncio.run(run_case(case, server_url=server_url, runs_root=runs_root, report_dir=report_dir))


def run_suite_sync(suite: PlaybackSuite, *, server_url: str, runs_root: str | Path | None, report_dir: Path | None, fail_fast: bool = False) -> list[CaseReport]:
    """同步包装，供 CLI 使用。"""

    return asyncio.run(run_suite(suite, server_url=server_url, runs_root=runs_root, report_dir=report_dir, fail_fast=fail_fast))
