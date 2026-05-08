"""半双工播放流队列工具。

本模块只处理本地播放流的队列、完成、打断和等待逻辑，不负责业务语音、
模型调用或设备控制消息下发。它是从 `VoiceRuntime` 中拆出的播放子系统
基础层，目的是让播放仲裁状态和 HTTP 播放流状态可以独立测试和演进。
"""

from __future__ import annotations

import queue
import time
from typing import Callable

from infra.errors import ErrorCode, build_error
from infra.logging import LogContext, log_debug, log_info
from runtime.audio_utils import wav_header_unknown_size
from runtime.playback_arbiter import PlaybackArbiter, PlaybackIntent
from runtime.voice_state import PlaybackStreamContext, VoiceSessionController


def playback_priority_value(priority: str) -> int:
    """把播放优先级转换为本地待播队列排序值。

    主要逻辑：
    1. 将 SDK 对外暴露的 low、normal、high、critical 映射为整数。
    2. 对未知优先级按 normal 处理，避免业务配置写错后破坏播放链路。

    参数：
    1. `priority`：播放优先级字符串。

    返回值：
    1. 用于待播队列排序的整数，数值越大优先级越高。

    异常情况：
    1. 本函数不抛出异常。
    """

    return {
        "low": 0,
        "normal": 1,
        "high": 2,
        "critical": 3,
    }.get(priority, 1)


def pop_pending_playback(
    controller: VoiceSessionController,
    stream_id: str,
) -> PlaybackStreamContext | None:
    """按播放流编号从待播队列取出下一条播放流。

    主要逻辑：
    1. 遍历当前设备控制器的待播队列。
    2. 找到目标 `stream_id` 后从队列中移除并返回。
    3. 如果不存在目标播放流，则返回 None，让上层按空队列处理。

    参数：
    1. `controller`：设备语音会话控制器。
    2. `stream_id`：目标播放流编号。

    返回值：
    1. 找到时返回播放流上下文，否则返回 None。

    异常情况：
    1. 本函数要求调用方已完成必要加锁，不主动抛出结构化异常。
    """

    for index, pending in enumerate(controller.pending_playbacks):
        if pending.stream_id == stream_id:
            return controller.pending_playbacks.pop(index)
    return None


def mark_playback_interrupted(
    *,
    controller: VoiceSessionController,
    playback: PlaybackStreamContext,
    reason: str,
    interrupted_playback_streams: set[tuple[str, str]],
) -> None:
    """把播放流标记为已中断并同步控制器状态。

    主要逻辑：
    1. 标记播放流失败、完成并唤醒等待方。
    2. 将播放流编号加入已中断集合，容忍设备之后补报 finished。
    3. 如果它是当前播放流，则把控制器恢复到 listening；否则从待播队列移除。

    参数：
    1. `controller`：设备语音会话控制器。
    2. `playback`：被中断的播放流。
    3. `reason`：中断原因。
    4. `interrupted_playback_streams`：运行时记录的已中断播放流集合。

    返回值：
    1. 无返回值。

    异常情况：
    1. 本函数要求调用方已完成必要加锁，不主动抛出结构化异常。
    """

    playback.failed = True
    playback.completed = True
    playback.abort_event.set()
    playback.finished_event.set()
    interrupted_playback_streams.add((playback.device_id, playback.stream_id))
    controller.last_playback_stream_id = playback.stream_id
    controller.last_playback_state = "interrupted"
    controller.last_playback_reason = reason
    if controller.current_playback is playback:
        controller.current_playback = None
        controller.state = "listening"
    else:
        controller.pending_playbacks = [
            pending for pending in controller.pending_playbacks if pending is not playback
        ]


def remove_playback_by_intent(
    *,
    controller: VoiceSessionController,
    intent: PlaybackIntent | None,
    playback_streams: dict[tuple[str, str], PlaybackStreamContext],
    notification_stream_requests: dict[tuple[str, str], str],
    notification_request_streams: dict[str, tuple[str, str]],
    interrupted_playback_streams: set[tuple[str, str]],
    reason: str,
) -> tuple[PlaybackStreamContext | None, str | None]:
    """按仲裁器意图移除本地播放流。

    主要逻辑：
    1. 根据仲裁器返回的意图找到本地播放流。
    2. 同步清理通知请求到播放流的双向映射。
    3. 找到播放流时标记为中断，便于 HTTP 播放流和设备回包自然收敛。

    参数：
    1. `controller`：设备语音会话控制器。
    2. `intent`：仲裁器返回的播放意图，可能为空。
    3. `playback_streams`：本地播放流索引。
    4. `notification_stream_requests`：播放流到通知请求的映射。
    5. `notification_request_streams`：通知请求到播放流的映射。
    6. `interrupted_playback_streams`：已中断播放流集合。
    7. `reason`：移除原因。

    返回值：
    1. 二元组：被移除的播放流、被取消的通知请求编号。

    异常情况：
    1. 本函数要求调用方已完成必要加锁，不主动抛出结构化异常。
    """

    if intent is None:
        return None, None
    playback = playback_streams.pop((intent.device_id, intent.stream_id), None)
    request_id = notification_stream_requests.pop((intent.device_id, intent.stream_id), None)
    if request_id is not None:
        notification_request_streams.pop(request_id, None)
    if playback is None:
        return None, request_id
    mark_playback_interrupted(
        controller=controller,
        playback=playback,
        reason=reason,
        interrupted_playback_streams=interrupted_playback_streams,
    )
    return playback, request_id


def enqueue_playback_chunk(playback: PlaybackStreamContext, chunk: bytes) -> None:
    """把 PCM 分片写入播放流队列。

    主要逻辑：
    1. 队列满时短暂等待，避免 TTS 或 Omni 输出线程忙等。
    2. 如果播放流已经被中止，则直接返回，避免阻塞模型线程。

    参数：
    1. `playback`：目标播放流。
    2. `chunk`：要下发的 PCM 字节。

    返回值：
    1. 无返回值。

    异常情况：
    1. 队列满会内部重试；播放已中止时静默返回。
    """

    while True:
        try:
            playback.queue.put(chunk, timeout=0.5)
            return
        except queue.Full:
            if playback.abort_event.is_set():
                return


def finish_playback_stream(playback: PlaybackStreamContext) -> None:
    """结束播放流并唤醒 HTTP 播放读取方。

    主要逻辑：
    1. 标记播放流完成并设置完成事件。
    2. 向队列写入 None 作为终止哨兵。
    3. 队列满时忽略哨兵写入失败，因为读取方仍会通过 completed 状态退出。

    参数：
    1. `playback`：目标播放流。

    返回值：
    1. 无返回值。

    异常情况：
    1. 队列满时不会向外抛出异常。
    """

    playback.completed = True
    playback.finished_event.set()
    try:
        playback.queue.put_nowait(None)
    except queue.Full:
        pass


def request_playback_start(
    *,
    controllers: dict[str, VoiceSessionController],
    lock,
    send_control_message: Callable[[str, str, str, str, dict], None],
    logger,
    now_ms: Callable[[], int],
    latency_ms,
    device_id: str,
    session_id: str,
    playback: PlaybackStreamContext,
    force: bool,
    sample_rate: int,
    channels: int,
) -> None:
    """按当前播放队列状态决定是否下发播放请求。

    主要逻辑：
    1. 只有当前激活播放流才能真正启动播放。
    2. 已经下发过 `actuator.audio.play` 的流不重复下发。
    3. 对排队中的后续流，仅缓存音频，待前序流结束后再启动。

    参数：
    1. `controllers`：设备语音会话控制器索引。
    2. `lock`：保护控制器和播放流状态的锁。
    3. `send_control_message`：下发控制消息的回调。
    4. `logger`：SDK 日志对象。
    5. `now_ms`：返回当前毫秒时间戳的回调。
    6. `latency_ms`：计算延迟的回调。
    7. `device_id`：设备编号。
    8. `session_id`：会话编号。
    9. `playback`：目标播放流。
    10. `force`：是否在已有音频时立即启动。
    11. `sample_rate`：下行 PCM 采样率。
    12. `channels`：下行 PCM 声道数。

    返回值：
    1. 无返回值。

    异常情况：
    1. 找不到设备控制器时抛出 `ErrorCode.STREAM_NOT_FOUND`。
    """

    should_send = False
    with lock:
        controller = controllers.get(device_id)
        if controller is None:
            raise build_error(
                ErrorCode.STREAM_NOT_FOUND,
                "未找到对应设备的语音会话控制器",
                details={"device_id": device_id},
            )
        if controller.current_playback is playback and not playback.play_requested and force:
            playback.play_requested = True
            if playback.first_play_request_at_ms is None:
                playback.first_play_request_at_ms = now_ms()
            should_send = True

    if should_send:
        send_control_message(
            device_id,
            "request",
            "actuator.audio.play",
            session_id,
            {
                "mode": "stream",
                "stream_id": playback.stream_id,
                "format": "pcm16",
                "sample_rate": sample_rate,
                "channels": channels,
                "interrupt_policy": "forbid",
            },
        )
        log_info(
            logger,
            (
                "下行播放请求已发送 "
                f"stream_id={playback.stream_id} audio_source={playback.audio_source} "
                f"text_to_play_request_ms={latency_ms(start=playback.first_text_delta_at_ms, end=playback.first_play_request_at_ms)} "
                f"source_audio_to_play_request_ms={latency_ms(start=playback.first_audio_chunk_at_ms, end=playback.first_play_request_at_ms)}"
            ),
            LogContext(device_id=device_id, session_id=session_id, message_id=playback.stream_id),
        )


def create_playback_stream(
    *,
    controllers: dict[str, VoiceSessionController],
    lock,
    playback_condition,
    playback_arbiter: PlaybackArbiter,
    playback_streams: dict[tuple[str, str], PlaybackStreamContext],
    notification_stream_requests: dict[tuple[str, str], str],
    notification_request_streams: dict[str, tuple[str, str]],
    interrupted_playback_streams: set[tuple[str, str]],
    send_control_message: Callable[[str, str, str, str, dict], None],
    device_id: str,
    session_id: str,
    stream_id: str,
    source: str,
    priority: str,
    interrupt_policy: str,
    resume_policy: str,
    task_id: str | None,
    audio_source: str,
    sample_rate: int,
    channels: int,
) -> PlaybackStreamContext:
    """创建播放流并提交统一播放仲裁器。

    主要逻辑：
    1. 构造 `PlaybackStreamContext` 和对应 `PlaybackIntent`。
    2. 把播放意图提交给统一播放仲裁器。
    3. 如果新播放流需要打断旧播放流，则同步标记旧流并下发设备中断。
    4. 如果新播放流需要排队，则按播放优先级和创建时间排序。

    参数：
    1. `controllers`：设备语音会话控制器索引。
    2. `lock`：保护控制器和播放状态的锁。
    3. `playback_condition`：播放流创建条件变量。
    4. `playback_arbiter`：统一播放仲裁器。
    5. `playback_streams`：本地播放流索引。
    6. `notification_stream_requests`：播放流到通知请求的映射。
    7. `notification_request_streams`：通知请求到播放流的映射。
    8. `interrupted_playback_streams`：已中断播放流集合。
    9. `send_control_message`：下发设备控制消息的回调。
    10. 其余参数用于描述播放流来源、优先级、中断策略和音频格式。

    返回值：
    1. 新创建的播放流上下文。

    异常情况：
    1. 找不到设备控制器时抛出 `ErrorCode.STREAM_NOT_FOUND`。
    """

    intent_id = f"{source}:{stream_id}"
    playback = PlaybackStreamContext(
        device_id=device_id,
        session_id=session_id,
        stream_id=stream_id,
        sample_rate=sample_rate,
        channels=channels,
        source=source,
        audio_source=audio_source,
        priority=priority,
        interrupt_policy=interrupt_policy,
        resume_policy=resume_policy,
        task_id=task_id,
        intent_id=intent_id,
    )
    interrupted_playback: PlaybackStreamContext | None = None
    with lock:
        controller = controllers.get(device_id)
        if controller is None:
            raise build_error(
                ErrorCode.STREAM_NOT_FOUND,
                "未找到对应设备的语音会话控制器",
                details={"device_id": device_id},
            )
        intent = PlaybackIntent(
            intent_id=intent_id,
            source=source,
            device_id=device_id,
            session_id=session_id,
            stream_id=stream_id,
            priority=priority,
            interrupt_policy=interrupt_policy,
            resume_policy=resume_policy,
            task_id=task_id,
        )
        submit_result = playback_arbiter.submit(intent)
        if submit_result.interrupted_intent is not None:
            interrupted_stream_id = submit_result.interrupted_intent.stream_id
            interrupted_playback = playback_streams.pop((device_id, interrupted_stream_id), None)
            if interrupted_playback is not None:
                mark_playback_interrupted(
                    controller=controller,
                    playback=interrupted_playback,
                    reason="higher_priority_playback",
                    interrupted_playback_streams=interrupted_playback_streams,
                )
                request_id = notification_stream_requests.pop((device_id, interrupted_stream_id), None)
                if request_id is not None:
                    notification_request_streams.pop(request_id, None)
        if submit_result.decision.action in {"play_now", "interrupt"}:
            controller.current_playback = playback
        else:
            controller.pending_playbacks.append(playback)
            controller.pending_playbacks.sort(
                key=lambda item: (-playback_priority_value(item.priority), item.created_at_ms)
            )
        playback_streams[(device_id, stream_id)] = playback
        playback_condition.notify_all()
    if interrupted_playback is not None:
        send_control_message(
            device_id,
            "request",
            "actuator.audio.interrupt",
            interrupted_playback.session_id,
            {
                "device_id": device_id,
                "stream_id": interrupted_playback.stream_id,
                "reason": "higher_priority_playback",
                "incoming_stream_id": stream_id,
                "resume_policy": interrupted_playback.resume_policy,
            },
        )
        try:
            interrupted_playback.queue.put_nowait(None)
        except queue.Full:
            pass
    return playback


def wait_for_playback(
    *,
    playback_streams: dict[tuple[str, str], PlaybackStreamContext],
    playback_condition,
    device_id: str,
    stream_id: str,
    timeout_s: float,
) -> PlaybackStreamContext:
    """等待播放流被创建。

    主要逻辑：
    1. HTTP 播放请求可能先于模型首段音频到达，因此需要按条件变量等待。
    2. 在超时时间内找到播放流后立即返回。
    3. 超时后抛出结构化错误，便于 HTTP 层返回明确失败。

    参数：
    1. `playback_streams`：本地播放流索引。
    2. `playback_condition`：播放流创建通知条件变量。
    3. `device_id`：设备编号。
    4. `stream_id`：播放流编号。
    5. `timeout_s`：最长等待秒数。

    返回值：
    1. 找到的播放流上下文。

    异常情况：
    1. 等待超时抛出 `ErrorCode.TIMEOUT`。
    """

    deadline = time.monotonic() + timeout_s
    with playback_condition:
        while True:
            playback = playback_streams.get((device_id, stream_id))
            if playback is not None:
                return playback
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise build_error(
                    ErrorCode.TIMEOUT,
                    "等待播放流超时",
                    details={"device_id": device_id, "stream_id": stream_id},
                )
            playback_condition.wait(timeout=remaining)


def send_chunked_wav_headers(handler) -> None:
    """向 HTTP 客户端写出分块 WAV 响应头。

    主要逻辑：
    1. 使用 `audio/wav` 表示后续内容是 WAV 容器。
    2. 使用 chunked 传输，允许模型边生成音频边下发。
    3. 要求连接关闭，避免端侧误以为还能复用当前播放响应。

    参数：
    1. `handler`：标准库 HTTP 请求处理对象。

    返回值：
    1. 无返回值。

    异常情况：
    1. 底层 socket 写入失败会由调用方或上层捕获。
    """

    handler.send_response(200)
    handler.send_header("Content-Type", "audio/wav")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Transfer-Encoding", "chunked")
    handler.send_header("Connection", "close")
    handler.end_headers()


def write_chunked_payload(handler, payload: bytes) -> None:
    """向 HTTP chunked 响应写入一个数据块。

    主要逻辑：
    1. 空数据直接跳过。
    2. 先写十六进制长度，再写数据和 CRLF。
    3. 每块写完立即 flush，保证端侧能尽快收到首段音频。

    参数：
    1. `handler`：标准库 HTTP 请求处理对象。
    2. `payload`：要写出的字节。

    返回值：
    1. 无返回值。

    异常情况：
    1. 底层 socket 写入失败会向外抛出。
    """

    if not payload:
        return
    handler.wfile.write(f"{len(payload):X}\r\n".encode("ascii"))
    handler.wfile.write(payload)
    handler.wfile.write(b"\r\n")
    handler.wfile.flush()


def finish_chunked_payload(handler) -> None:
    """结束 HTTP chunked 响应。

    主要逻辑：
    1. 写入长度为 0 的 chunk。
    2. flush 输出缓冲区，让端侧播放流自然结束。

    参数：
    1. `handler`：标准库 HTTP 请求处理对象。

    返回值：
    1. 无返回值。

    异常情况：
    1. 底层 socket 写入失败会向外抛出。
    """

    handler.wfile.write(b"0\r\n\r\n")
    handler.wfile.flush()


def stream_playback_to_http(
    *,
    handler,
    playback: PlaybackStreamContext,
    device_id: str,
    stream_id: str,
    logger,
    now_ms,
    latency_ms,
) -> None:
    """把本地播放队列流式写给眼镜端 HTTP 播放请求。

    主要逻辑：
    1. 先写未知长度 WAV 头，保持眼镜端播放器实现简单。
    2. 从播放队列持续取 PCM 分片并以 chunked 方式写出。
    3. 记录首段 HTTP 音频写出延迟，便于联调首响耗时。
    4. 遇到客户端主动断开时只写 DEBUG 日志，不把它当作模型错误。

    参数：
    1. `handler`：标准库 HTTP 请求处理对象。
    2. `playback`：目标播放流。
    3. `device_id`：设备编号。
    4. `stream_id`：播放流编号。
    5. `logger`：SDK 日志对象。
    6. `now_ms`：返回当前毫秒时间戳的回调。
    7. `latency_ms`：计算两个毫秒时间戳差值的回调。

    返回值：
    1. 无返回值。

    异常情况：
    1. BrokenPipe、连接重置和连接中止会被捕获并记录 DEBUG 日志。
    2. 其他异常继续向外抛出，由 HTTP 层统一处理。
    """

    try:
        send_chunked_wav_headers(handler)
        write_chunked_payload(handler, wav_header_unknown_size(playback.sample_rate, playback.channels))
        log_debug(
            logger,
            f"播放流 HTTP 已建立 stream_id={stream_id} sample_rate={playback.sample_rate} channels={playback.channels}",
            LogContext(device_id=device_id, session_id=playback.session_id, message_id=stream_id),
        )

        while True:
            if playback.abort_event.is_set():
                break
            try:
                item = playback.queue.get(timeout=0.5)
            except queue.Empty:
                if playback.completed:
                    break
                continue
            if item is None:
                break
            if playback.first_http_audio_chunk_at_ms is None:
                playback.first_http_audio_chunk_at_ms = now_ms()
                log_info(
                    logger,
                    (
                        "播放流写出首段音频 "
                        f"stream_id={stream_id} audio_source={playback.audio_source} bytes={len(item)} "
                        f"play_request_to_http_audio_ms={latency_ms(start=playback.first_play_request_at_ms, end=playback.first_http_audio_chunk_at_ms)} "
                        f"source_audio_to_http_audio_ms={latency_ms(start=playback.first_audio_chunk_at_ms, end=playback.first_http_audio_chunk_at_ms)}"
                    ),
                    LogContext(device_id=device_id, session_id=playback.session_id, message_id=stream_id),
                )
            write_chunked_payload(handler, item)

        finish_chunked_payload(handler)
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
        log_debug(
            logger,
            f"播放流 HTTP 客户端已断开: device_id={device_id} stream_id={stream_id} reason={exc.__class__.__name__}",
            LogContext(device_id=device_id, session_id=playback.session_id, message_id=stream_id),
        )
