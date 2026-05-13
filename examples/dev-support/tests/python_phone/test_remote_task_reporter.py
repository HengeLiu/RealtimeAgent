from __future__ import annotations

import asyncio
import logging

import pytest

from audio_chat.protocol import Event
from audio_chat_python_phone_mock.remote_task import RemoteCommand, RemoteTaskReporter


def test_remote_task_reporter_sends_command_events_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    """测试目标：验证 Python phone 远程任务 helper 生成标准 command 回执。

    测试方法：构造一条 `command.requested`，使用 fake send_event 收集 accepted、
    progress、completed、failed 四类事件。
    预期结果：payload 自动包含 command_id、command、peer_session_id、task_type、
    role，并且日志里能看到 peer_session_id 和 status。
    """

    sent: list[Event] = []
    command_event = Event(
        event_name="command.requested",
        user_id="user-peer",
        producer_id="server-main",
        session_id="dev-phone",
        payload={
            "command_id": "cmd-phone",
            "command": "peer.video.receiver.start",
            "params": {
                "peer_session_id": "task-peer",
                "task_type": "find_object_task",
            },
        },
    )

    async def send_event(event: Event) -> None:
        sent.append(event)

    async def run() -> None:
        reporter = RemoteTaskReporter(
            command=RemoteCommand.from_event(command_event),
            producer_id="dev-phone",
            role="receiver",
            send_event=send_event,
        )
        await reporter.accepted(message="accepted")
        await reporter.progress("peer.receiver.ready", data={"receiver": {"url": "ws://127.0.0.1:19081/x"}}, metrics={"frame_count": 0})
        await reporter.completed(result={"source": "mock"}, message="done")
        await reporter.failed(message="failed", error_code="mock_failed")

    with caplog.at_level(logging.INFO):
        asyncio.run(run())

    assert [event.event_name for event in sent] == [
        "command.accepted",
        "command.progress",
        "command.completed",
        "command.failed",
    ]
    progress_payload = sent[1].payload
    assert progress_payload["command_id"] == "cmd-phone"
    assert progress_payload["command"] == "peer.video.receiver.start"
    assert progress_payload["peer_session_id"] == "task-peer"
    assert progress_payload["task_type"] == "find_object_task"
    assert progress_payload["role"] == "receiver"
    assert progress_payload["status"] == "peer.receiver.ready"
    assert progress_payload["data"]["receiver"]["url"].startswith("ws://")
    assert progress_payload["metrics"]["frame_count"] == 0
    assert "peer_session_id=task-peer" in caplog.text
    assert "status=peer.receiver.ready" in caplog.text


def test_remote_task_reporter_rejects_empty_progress_status() -> None:
    """测试目标：验证 helper 不允许发送空 status 的 progress。

    测试方法：调用 `progress("")`。
    预期结果：抛出 ValueError，避免端侧发出不可诊断的中间状态。
    """

    async def send_event(_: Event) -> None:
        return None

    reporter = RemoteTaskReporter(
        command=RemoteCommand(
            command_id="cmd-phone",
            command="peer.video.receiver.start",
            user_id="user-peer",
            session_id="dev-phone",
            params={"peer_session_id": "task-peer"},
        ),
        producer_id="dev-phone",
        role="receiver",
        send_event=send_event,
    )

    async def run() -> None:
        with pytest.raises(ValueError):
            await reporter.progress("")

    asyncio.run(run())
