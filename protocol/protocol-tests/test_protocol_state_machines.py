import pytest

from realtime_agent.protocol_state import (
    validate_command_event_sequence,
    validate_input_stream_event_sequence,
    validate_output_stream_event_sequence,
)


pytestmark = pytest.mark.protocol


def test_command_sequence_accepts_standard_lifecycle() -> None:
    """测试目标：确认命令协议支持 request -> accepted -> progress -> completed 标准生命周期。

    测试方法：把一组按真实通讯顺序排列的 command 事件交给协议状态机校验。
    预期结果：状态机不抛异常，说明 server sdk / device sdk 可基于同一顺序实现。
    """

    validate_command_event_sequence(
        [
            "command.requested",
            "command.accepted",
            "command.progress",
            "command.completed",
        ]
    )


def test_command_sequence_rejects_progress_before_acceptance() -> None:
    """测试目标：确认端侧不能在接受命令前上报进度。

    测试方法：构造 `command.progress` 早于 `command.accepted` 的反例序列。
    预期结果：状态机抛出 ValueError，暴露协议顺序错误。
    """

    with pytest.raises(ValueError, match="progress must appear after"):
        validate_command_event_sequence(["command.requested", "command.progress", "command.completed"])


def test_command_sequence_rejects_events_after_terminal_state() -> None:
    """测试目标：确认命令进入终态后不能继续发送进度或结果事件。

    测试方法：构造 completed 后又出现 progress 的反例序列。
    预期结果：状态机抛出 ValueError，避免 SDK 消费到双终态或幽灵进度。
    """

    with pytest.raises(ValueError, match="after terminal"):
        validate_command_event_sequence(
            [
                "command.requested",
                "command.accepted",
                "command.completed",
                "command.progress",
            ]
        )


def test_input_stream_sequence_accepts_open_upload_close_lifecycle() -> None:
    """测试目标：确认输入 stream 的控制事件顺序可表达一次完整上传。

    测试方法：校验 open requested -> input opened -> input closed 事件序列。
    预期结果：状态机不抛异常；二进制分片由 stream codec 契约测试覆盖。
    """

    validate_input_stream_event_sequence(
        [
            "stream.control.open.requested",
            "stream.input.opened",
            "stream.input.closed",
        ]
    )


def test_input_stream_sequence_rejects_close_before_opened() -> None:
    """测试目标：确认输入 stream 不能在端侧确认打开前声明关闭。

    测试方法：构造 open requested 后直接 closed 的反例序列。
    预期结果：状态机抛出 ValueError，提示 closed 必须晚于 opened。
    """

    with pytest.raises(ValueError, match="closed must appear after"):
        validate_input_stream_event_sequence(["stream.control.open.requested", "stream.input.closed"])


def test_output_stream_sequence_accepts_playback_lifecycle() -> None:
    """测试目标：确认输出 stream 可以表达一次完整播放。

    测试方法：校验 open requested -> ready -> started -> finished 事件序列。
    预期结果：状态机不抛异常，server sdk 可把它作为播放完成信号消费。
    """

    validate_output_stream_event_sequence(
        [
            "stream.output.open.requested",
            "stream.output.ready",
            "stream.output.started",
            "stream.output.finished",
        ]
    )


def test_output_stream_sequence_accepts_cancel_lifecycle() -> None:
    """测试目标：确认输出 stream 支持取消请求和取消回执。

    测试方法：校验 open requested -> ready -> started -> cancel requested -> cancelled 事件序列。
    预期结果：状态机不抛异常，端侧 SDK 可稳定映射为播放取消行为。
    """

    validate_output_stream_event_sequence(
        [
            "stream.output.open.requested",
            "stream.output.ready",
            "stream.output.started",
            "stream.output.cancel.requested",
            "stream.output.cancelled",
        ]
    )


def test_output_stream_sequence_accepts_ready_then_closed_without_started() -> None:
    """测试目标：确认空输出或未达到起播水位的输出可以在 ready 后直接关闭。

    测试方法：校验 open requested -> ready -> closed 事件序列。
    预期结果：状态机不抛异常，端侧可表达没有进入本地播放的输出终态。
    """

    validate_output_stream_event_sequence(
        [
            "stream.output.open.requested",
            "stream.output.ready",
            "stream.output.closed",
        ]
    )


def test_output_stream_sequence_rejects_finish_before_started() -> None:
    """测试目标：确认输出 stream 不能在播放开始前直接完成。

    测试方法：构造 open requested 后直接 finished 的反例序列。
    预期结果：状态机抛出 ValueError，暴露端侧播放状态回报错误。
    """

    with pytest.raises(ValueError, match="finished must appear after"):
        validate_output_stream_event_sequence(["stream.output.open.requested", "stream.output.finished"])
