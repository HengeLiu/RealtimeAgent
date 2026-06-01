from __future__ import annotations

from collections.abc import Iterable


COMMAND_EVENTS = {
    "command.requested",
    "command.accepted",
    "command.progress",
    "command.completed",
    "command.failed",
}

INPUT_STREAM_EVENTS = {
    "stream.control.open.requested",
    "stream.input.opened",
    "stream.input.closed",
    "stream.input.failed",
}

OUTPUT_STREAM_EVENTS = {
    "stream.output.start.requested",
    "stream.output.ready",
    "stream.output.started",
    "stream.output.finished",
    "stream.output.failed",
    "stream.output.cancel.requested",
    "stream.output.cancelled",
}


def validate_command_event_sequence(event_names: Iterable[str]) -> None:
    """校验一次设备命令的协议事件顺序。

    主要逻辑：命令必须从 `command.requested` 开始，端侧随后接受、上报进度，
    最终只能进入 completed 或 failed 之一；终态之后不能再出现后续事件。
    参数：`event_names` 是按发生顺序排列的事件名列表。
    返回值：无，校验通过时直接返回。
    异常情况：事件缺失、顺序错误、终态重复或混入非命令事件时抛出 `ValueError`。
    """

    names = list(event_names)
    if not names:
        raise ValueError("command sequence is empty")
    _ensure_known_events(names, COMMAND_EVENTS, "command")
    if names[0] != "command.requested":
        raise ValueError("command sequence must start with command.requested")

    accepted = False
    terminal_seen = False
    for index, name in enumerate(names[1:], start=1):
        if terminal_seen:
            raise ValueError(f"command sequence has event after terminal state at index {index}")
        if name == "command.accepted":
            if accepted:
                raise ValueError("command.accepted must appear at most once")
            accepted = True
            continue
        if name == "command.progress":
            if not accepted:
                raise ValueError("command.progress must appear after command.accepted")
            continue
        if name in {"command.completed", "command.failed"}:
            if not accepted:
                raise ValueError(f"{name} must appear after command.accepted")
            terminal_seen = True
            continue
        raise ValueError(f"unsupported command transition: {name}")
    if not terminal_seen:
        raise ValueError("command sequence must end with command.completed or command.failed")


def validate_input_stream_event_sequence(event_names: Iterable[str]) -> None:
    """校验一次输入 stream 的控制事件顺序。

    主要逻辑：server 先请求打开输入 stream，端侧确认 opened 后上传二进制分片，
    最终通过 closed 或 failed 结束；这里校验控制事件顺序，二进制分片由 stream
    codec 测试单独覆盖。
    参数：`event_names` 是按发生顺序排列的事件名列表。
    返回值：无。
    异常情况：缺少打开请求、closed 早于 opened、终态后继续发事件时抛出 `ValueError`。
    """

    names = list(event_names)
    if not names:
        raise ValueError("input stream sequence is empty")
    _ensure_known_events(names, INPUT_STREAM_EVENTS, "input stream")
    if names[0] != "stream.control.open.requested":
        raise ValueError("input stream sequence must start with stream.control.open.requested")

    opened = False
    terminal_seen = False
    for index, name in enumerate(names[1:], start=1):
        if terminal_seen:
            raise ValueError(f"input stream sequence has event after terminal state at index {index}")
        if name == "stream.input.opened":
            if opened:
                raise ValueError("stream.input.opened must appear at most once")
            opened = True
            continue
        if name == "stream.input.closed":
            if not opened:
                raise ValueError("stream.input.closed must appear after stream.input.opened")
            terminal_seen = True
            continue
        if name == "stream.input.failed":
            terminal_seen = True
            continue
        raise ValueError(f"unsupported input stream transition: {name}")
    if not terminal_seen:
        raise ValueError("input stream sequence must end with stream.input.closed or stream.input.failed")


def validate_output_stream_event_sequence(event_names: Iterable[str]) -> None:
    """校验一次输出 stream 的控制事件顺序。

    主要逻辑：server 请求打开输出 stream，端侧先回 ready 表示本轮逻辑输出状态
    已经重置完成，随后开始播放并进入 finished、closed、cancelled 或 failed 终态；
    如果 server 发出取消请求，端侧最终必须回到 cancelled 或 failed。
    参数：`event_names` 是按发生顺序排列的事件名列表。
    返回值：无。
    异常情况：started 之前 finished、取消请求没有回执、终态后继续发事件时抛出 `ValueError`。
    """

    names = list(event_names)
    if not names:
        raise ValueError("output stream sequence is empty")
    _ensure_known_events(names, OUTPUT_STREAM_EVENTS, "output stream")
    if names[0] != "stream.output.start.requested":
        raise ValueError("output stream sequence must start with stream.output.start.requested")

    ready = False
    started = False
    cancel_requested = False
    terminal_seen = False
    for index, name in enumerate(names[1:], start=1):
        if terminal_seen:
            raise ValueError(f"output stream sequence has event after terminal state at index {index}")
        if name == "stream.output.ready":
            if ready:
                raise ValueError("stream.output.ready must appear at most once")
            if started:
                raise ValueError("stream.output.ready must appear before stream.output.started")
            ready = True
            continue
        if name == "stream.output.started":
            if not ready:
                raise ValueError("stream.output.started must appear after stream.output.ready")
            if started:
                raise ValueError("stream.output.started must appear at most once")
            started = True
            continue
        if name == "stream.output.finished":
            if not ready:
                raise ValueError("stream.output.finished must appear after stream.output.ready")
            if cancel_requested:
                raise ValueError("stream.output.finished must not follow cancel request")
            terminal_seen = True
            continue
        if name == "stream.output.cancel.requested":
            cancel_requested = True
            continue
        if name == "stream.output.cancelled":
            if not cancel_requested:
                raise ValueError("stream.output.cancelled must follow stream.output.cancel.requested")
            terminal_seen = True
            continue
        if name == "stream.output.failed":
            terminal_seen = True
            continue
        raise ValueError(f"unsupported output stream transition: {name}")
    if not terminal_seen:
        raise ValueError("output stream sequence must end with a terminal stream.output event")


def _ensure_known_events(names: list[str], allowed: set[str], label: str) -> None:
    """确认序列只包含当前状态机能处理的事件名。"""

    unknown = [name for name in names if name not in allowed]
    if unknown:
        raise ValueError(f"{label} sequence contains unsupported events: {unknown}")
