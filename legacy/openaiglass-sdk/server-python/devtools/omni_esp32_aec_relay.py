"""ESP32-S3 AEC 到 Omni Realtime 的直连试验 relay。

这个脚本用于最小化验证 ESP32-S3 端 AEC 的可靠性，不接入完整 SDK 控制链路。
ESP32 试验固件只需要连接本脚本的 WebSocket：

1. ESP32 播放脚本下发的 `playback_audio` PCM16。
2. ESP32 使用本地 AEC 处理麦克风输入。
3. ESP32 把 AEC 后的 16 kHz 单声道 PCM16 通过 `mic_audio` 发回脚本。
4. 脚本把 `mic_audio` 直接追加给 Omni Realtime。
5. Omni 返回的音频由脚本重采样为 16 kHz 后下发给 ESP32 播放。

推荐 ESP32 文本消息：
    {"type":"hello","device_id":"glass-001","sample_rate":16000,"channels":1,"aec":"esp_sr_fd_low_cost"}
    {"type":"barge_in","reason":"endpoint_vad","confidence":0.9}

推荐 ESP32 二进制音频帧：
    使用 SDK MediaFrame 编码，header.frame_type="mic_audio"，payload 为 AEC 后 PCM16。

为了降低固件侧试验成本，也支持 JSON base64 音频：
    {"type":"mic_audio","audio":"<base64 pcm16>"}

脚本下发给 ESP32 的音频帧：
    使用 SDK MediaFrame 编码，header.frame_type="playback_audio"，payload 为 16 kHz PCM16。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import signal
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from protocol.media import MediaFrame


DEFAULT_MODEL = "qwen3.5-omni-plus-realtime"
DEFAULT_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
INPUT_RATE_HZ = 16000
OMNI_OUTPUT_RATE_HZ = 24000
PLAYBACK_RATE_HZ = 16000
PLAYBACK_FRAME_MS = 40
PLAYBACK_FRAME_BYTES = PLAYBACK_RATE_HZ * 2 * PLAYBACK_FRAME_MS // 1000


def _load_env_file(path: Path) -> None:
    """读取简单环境变量文件。

    主要逻辑：
    - 支持 `KEY=VALUE` 格式。
    - 忽略空行和注释。
    - 不覆盖进程里已有的环境变量。

    参数：
    - path：环境变量文件路径。

    返回值：
    - 无返回值，直接补充 `os.environ`。

    异常情况：
    - 文件不存在时直接忽略。
    """

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _summarize_event(message: dict[str, Any]) -> str:
    """把 Omni 事件转换成适合终端观察的短文本。

    参数：
    - message：Omni Realtime 回调事件。

    返回值：
    - 一行事件摘要。

    异常情况：
    - 字段缺失时返回空类型摘要，不抛异常。
    """

    event_type = str(message.get("type") or "")
    if event_type == "response.audio.delta":
        return f"type={event_type} delta_base64_len={len(str(message.get('delta') or ''))}"
    if event_type == "response.audio_transcript.delta":
        return f"type={event_type} delta={message.get('delta')!r}"
    if event_type == "conversation.item.input_audio_transcription.completed":
        return f"type={event_type} transcript={message.get('transcript')!r}"
    if event_type == "response.audio_transcript.done":
        return f"type={event_type} transcript={message.get('transcript')!r}"
    if event_type == "response.done":
        response = message.get("response") if isinstance(message.get("response"), dict) else {}
        return f"type={event_type} status={response.get('status')} details={response.get('status_details')}"
    return f"type={event_type}"


def _pcm16_linear_resample(pcm: bytes, *, from_rate: int, to_rate: int) -> bytes:
    """用线性插值重采样 PCM16 单声道音频。

    主要逻辑：
    - Omni Realtime 通常返回 24 kHz PCM16。
    - ESP32 当前播放链路按 16 kHz 单声道最简单。
    - 这里用轻量线性插值，避免为试验脚本引入额外依赖。

    参数：
    - pcm：输入 PCM16 little-endian 单声道字节。
    - from_rate：输入采样率。
    - to_rate：输出采样率。

    返回值：
    - 重采样后的 PCM16 little-endian 单声道字节。

    异常情况：
    - 输入为空或采样率一致时直接返回原字节。
    """

    if not pcm or from_rate == to_rate:
        return pcm
    if len(pcm) % 2:
        pcm = pcm[:-1]
    sample_count = len(pcm) // 2
    if sample_count <= 1:
        return pcm
    samples = [int.from_bytes(pcm[i * 2 : i * 2 + 2], "little", signed=True) for i in range(sample_count)]
    ratio = from_rate / to_rate
    out_count = max(1, int(sample_count / ratio))
    out = bytearray(out_count * 2)
    for i in range(out_count):
        pos = i * ratio
        left = int(pos)
        frac = pos - left
        a = samples[left] if left < sample_count else samples[-1]
        b = samples[left + 1] if left + 1 < sample_count else a
        value = int(a * (1.0 - frac) + b * frac)
        value = max(-32768, min(32767, value))
        out[i * 2 : i * 2 + 2] = value.to_bytes(2, "little", signed=True)
    return bytes(out)


class WavRecorder:
    """轻量 WAV 录制器。

    主要功能：
    - 把 ESP32 AEC 后上行音频或脚本下发音频保存下来，便于离线听诊。

    主要属性：
    - `_path`：输出 WAV 文件路径。
    - `_wave`：标准库 wave 写入对象。
    """

    def __init__(self, path: str | None, *, sample_rate: int) -> None:
        self._path = path
        self._sample_rate = sample_rate
        self._wave: wave.Wave_write | None = None
        if path:
            output_path = Path(path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            self._wave = wave.open(str(output_path), "wb")
            self._wave.setnchannels(1)
            self._wave.setsampwidth(2)
            self._wave.setframerate(sample_rate)

    def write(self, pcm: bytes) -> None:
        """写入一段 PCM16 音频。

        参数：
        - pcm：PCM16 little-endian 单声道字节。

        返回值：
        - 无返回值。

        异常情况：
        - 未配置输出路径时直接忽略。
        """

        if self._wave is not None and pcm:
            self._wave.writeframes(pcm)

    def close(self) -> None:
        """关闭 WAV 文件。"""

        if self._wave is not None:
            self._wave.close()
            self._wave = None


@dataclass(slots=True)
class Esp32AudioStats:
    """ESP32 AEC 试验统计。

    主要属性：
    - input_bytes：ESP32 回传的 AEC 后麦克风字节数。
    - output_bytes：脚本下发给 ESP32 播放的字节数。
    - response_count：Omni 完成响应次数。
    - cancel_count：ESP32 触发打断次数。
    """

    input_bytes: int = 0
    output_bytes: int = 0
    response_count: int = 0
    cancel_count: int = 0


class Esp32AecOmniRelaySession:
    """单个 ESP32 AEC 试验会话。

    主要功能：
    - 接收 ESP32 AEC 后的麦克风音频。
    - 转发给 Omni Realtime。
    - 把 Omni 返回音频下发给 ESP32 播放。

    主要属性：
    - `_websocket`：ESP32 建立的 WebSocket 连接。
    - `_conversation`：DashScope Omni Realtime 会话。
    - `_send_queue`：DashScope 回调线程到 asyncio 线程的发送队列。
    - `_send_lock`：串行化 websocket 写入，避免打断控制消息和音频帧并发发送。
    """

    def __init__(
        self,
        *,
        websocket: Any,
        args: argparse.Namespace,
        loop: asyncio.AbstractEventLoop,
        playback_state: dict[str, Any] | None = None,
    ) -> None:
        self._websocket = websocket
        self._args = args
        self._loop = loop
        self._playback_state = playback_state or {}
        self._send_queue: asyncio.Queue[dict[str, Any] | bytes | None] = asyncio.Queue()
        self._send_lock = asyncio.Lock()
        self._conversation = None
        self._closed = False
        self._stats = Esp32AudioStats()
        self._playback_stream_id = ""
        self._playback_seq = 0
        self._next_playback_send_at = 0.0
        self._response_in_progress = False
        self._audio_output_started = False
        self._mic_recorder = WavRecorder(args.record_mic_wav, sample_rate=INPUT_RATE_HZ)
        self._playback_recorder = WavRecorder(args.record_playback_wav, sample_rate=PLAYBACK_RATE_HZ)

    async def run(self, *, initial_message: str | bytes | None = None) -> None:
        """运行 ESP32 与 Omni 之间的转发循环。

        主要逻辑：
        - 建立 Omni 会话。
        - 后台发送 Omni 下行音频到 ESP32。
        - 主循环读取 ESP32 上行音频和控制消息。

        返回值：
        - 无返回值，ESP32 断开后结束。

        异常情况：
        - 非法消息会打印并继续，方便固件试验阶段快速定位格式问题。
        """

        self._connect_omni()
        sender_task = asyncio.create_task(self._esp32_sender())
        try:
            await self._send_json(
                {
                    "type": "relay_ready",
                    "input_sample_rate": INPUT_RATE_HZ,
                    "playback_sample_rate": PLAYBACK_RATE_HZ,
                    "playback_format": "pcm16le",
                }
            )
            if initial_message is not None:
                if isinstance(initial_message, bytes):
                    self._handle_binary(initial_message)
                else:
                    await self._handle_text(str(initial_message))
            async for raw in self._websocket:
                try:
                    if isinstance(raw, bytes):
                        self._handle_binary(raw)
                    else:
                        await self._handle_text(str(raw))
                except Exception as exc:  # noqa: BLE001 - 试验工具保留连接，继续收集现场
                    print(f"[esp32] message handling failed: {exc!r}")
        except Exception as exc:  # noqa: BLE001 - ESP32 断线通常没有标准 close frame
            print(f"[relay] esp32 websocket receive loop ended: {exc!r}")
        finally:
            self._closed = True
            await self._send_queue.put(None)
            sender_task.cancel()
            try:
                await sender_task
            except asyncio.CancelledError:
                pass
            if self._conversation is not None:
                self._conversation.close()
            self._mic_recorder.close()
            self._playback_recorder.close()
            print(
                "[relay] esp32 disconnected "
                f"input_bytes={self._stats.input_bytes} output_bytes={self._stats.output_bytes} "
                f"responses={self._stats.response_count} cancels={self._stats.cancel_count}"
            )

    def _connect_omni(self) -> None:
        """创建并配置 Omni Realtime 会话。

        返回值：
        - 无返回值，连接对象保存到 `_conversation`。

        异常情况：
        - DashScope SDK 导入、鉴权或网络失败时直接抛出。
        """

        import dashscope
        from dashscope.audio.qwen_omni import (
            AudioFormat,
            MultiModality,
            OmniRealtimeCallback,
            OmniRealtimeConversation,
        )

        dashscope.api_key = self._args.api_key
        relay = self

        class Callback(OmniRealtimeCallback):
            def on_open(self) -> None:
                print("[omni] websocket opened")

            def on_close(self, close_status_code: Any, close_msg: Any) -> None:
                print(f"[omni] websocket closed code={close_status_code} message={close_msg}")

            def on_event(self, message: dict[str, Any]) -> None:
                event_type = str(message.get("type") or "")
                if event_type == "error":
                    print(f"[omni] error payload={json.dumps(message, ensure_ascii=False)}")
                if event_type == "input_audio_buffer.speech_started":
                    should_interrupt = relay._response_in_progress or relay._audio_output_started
                    if should_interrupt:
                        relay._interrupt_playback_from_omni_speech_start(force=True)
                if event_type == "response.audio.delta":
                    relay._handle_omni_audio_delta(str(message.get("delta") or ""))
                    return
                if event_type == "response.created":
                    relay._response_in_progress = True
                    relay._start_new_playback_stream(str(message.get("response", {}).get("id") or ""))
                if event_type == "response.done":
                    relay._response_in_progress = False
                    relay._stats.response_count += 1
                    relay._enqueue_from_callback({"type": "playback_end", "stream_id": relay._playback_stream_id})
                summary = _summarize_event(message)
                print(f"[omni] {summary}")
                relay._enqueue_from_callback({"type": "omni_event", "summary": summary})

        self._conversation = OmniRealtimeConversation(
            model=self._args.model,
            callback=Callback(),
            url=self._args.url.rstrip("/"),
            api_key=self._args.api_key,
        )
        self._conversation.connect()
        self._conversation.update_session(
            output_modalities=[MultiModality.TEXT, MultiModality.AUDIO],
            voice=self._args.voice,
            input_audio_format=AudioFormat.PCM_16000HZ_MONO_16BIT,
            output_audio_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            enable_input_audio_transcription=True,
            input_audio_transcription_model="paraformer-realtime-v2",
            enable_turn_detection=True,
            turn_detection_type="server_vad" if self._args.server_vad else "semantic_vad",
            turn_detection_threshold=self._args.threshold,
            turn_detection_silence_duration_ms=self._args.silence_ms,
            prefix_padding_ms=self._args.prefix_ms,
            instructions=self._args.instructions,
        )

    async def _handle_text(self, raw: str) -> None:
        """处理 ESP32 文本消息。

        支持消息：
        - hello：打印端侧 AEC 信息。
        - mic_audio：base64 PCM16 上行。
        - barge_in：端侧判定用户插话，转发 Omni cancel。
        """

        message = json.loads(raw)
        message_type = str(message.get("type") or "")
        if message_type == "hello":
            print(f"[esp32] hello {json.dumps(message, ensure_ascii=False)}")
            return
        if message_type == "mic_audio":
            audio_b64 = str(message.get("audio") or "")
            pcm = base64.b64decode(audio_b64) if audio_b64 else b""
            self._append_mic_audio(pcm)
            return
        if message_type == "barge_in":
            self._stats.cancel_count += 1
            reason = str(message.get("reason") or "esp32_barge_in")
            confidence = message.get("confidence")
            print(f"[esp32] barge_in reason={reason} confidence={confidence}")
            self._interrupt_playback_on_loop(reason)
            return
        if message_type == "ping":
            await self._send_json({"type": "pong", "ts_ms": int(time.time() * 1000)})
            return
        print(f"[esp32] ignored text message type={message_type!r}")

    def _handle_binary(self, raw: bytes) -> None:
        """处理 ESP32 二进制媒体帧。

        参数：
        - raw：SDK MediaFrame 编码后的二进制帧。

        返回值：
        - 无返回值。
        """

        frame = MediaFrame.decode(raw)
        frame_type = str(frame.header.get("frame_type") or "")
        if frame_type not in {"mic_audio", "esp32.aec.output", "audio_chunk"}:
            print(f"[esp32] ignored binary frame_type={frame_type!r}")
            return
        self._append_mic_audio(frame.payload)

    def _append_mic_audio(self, pcm: bytes) -> None:
        """把 ESP32 AEC 后音频追加给 Omni。

        参数：
        - pcm：16 kHz 单声道 PCM16。

        返回值：
        - 无返回值。
        """

        if not pcm:
            return
        if self._conversation is None:
            return
        if len(pcm) % 2:
            pcm = pcm[:-1]
        self._stats.input_bytes += len(pcm)
        self._mic_recorder.write(pcm)
        self._conversation.append_audio(base64.b64encode(pcm).decode("ascii"))

    def _start_new_playback_stream(self, response_id: str) -> None:
        """开始一条新的下行播放流。

        参数：
        - response_id：Omni response id，用于生成稳定 stream id。
        """

        self._playback_stream_id = response_id or f"reply_{int(time.time() * 1000)}"
        self._playback_seq = 0
        self._next_playback_send_at = 0.0
        self._audio_output_started = False
        self._enqueue_from_callback(
            {
                "type": "playback_start",
                "stream_id": self._playback_stream_id,
                "sample_rate": PLAYBACK_RATE_HZ,
                "channels": 1,
                "codec": "pcm16le",
            }
        )

    def _handle_omni_audio_delta(self, audio_b64: str) -> None:
        """处理 Omni 下行音频增量。

        主要逻辑：
        - 解码 24 kHz PCM16。
        - 重采样到 16 kHz。
        - 打包成 MediaFrame 下发给 ESP32 播放。
        """

        if not audio_b64:
            return
        pcm24 = base64.b64decode(audio_b64)
        pcm16 = _pcm16_linear_resample(pcm24, from_rate=OMNI_OUTPUT_RATE_HZ, to_rate=PLAYBACK_RATE_HZ)
        if not pcm16:
            return
        if not self._playback_stream_id:
            self._start_new_playback_stream("")
        self._audio_output_started = True
        self._stats.output_bytes += len(pcm16)
        self._playback_recorder.write(pcm16)
        for offset in range(0, len(pcm16), PLAYBACK_FRAME_BYTES):
            chunk = pcm16[offset : offset + PLAYBACK_FRAME_BYTES]
            if not chunk:
                continue
            frame = MediaFrame(
                header={
                    "version": "v1",
                    "stream_id": self._playback_stream_id,
                    "frame_type": "playback_audio",
                    "seq": self._playback_seq,
                    "ts_ms": int(time.time() * 1000),
                    "codec": "pcm16le",
                    "sample_rate": PLAYBACK_RATE_HZ,
                    "channels": 1,
                    "payload_size": len(chunk),
                    "frame_ms": PLAYBACK_FRAME_MS,
                    "final": False,
                },
                payload=chunk,
            )
            self._playback_seq += 1
            self._enqueue_from_callback(frame.encode())

    def _cancel_omni_response(self) -> None:
        """转发端侧插话取消到 Omni。"""

        try:
            self._conversation.cancel_response()
            print("[relay] response.cancel forwarded")
        except Exception as exc:  # noqa: BLE001 - 试验工具继续保持连接
            print(f"[relay] response.cancel failed: {exc!r}")

    def _interrupt_playback_from_omni_speech_start(self, *, force: bool = False) -> None:
        """在 Omni 检测到新用户语音开始时尽快停止旧播放。

        主要逻辑：
        - 该回调来自 DashScope 线程，不能直接操作 websocket。
        - 把真正的清队列、取消响应、发送停止播放控制消息切回 asyncio 事件循环。
        - `force=True` 表示回调线程已经观察到旧响应或旧播放仍活跃，避免事件循环排队后
          `response.done` 先到达并清掉状态，导致本地播放残留无法及时清理。
        """

        self._loop.call_soon_threadsafe(self._interrupt_playback_on_loop, "omni_speech_started", force)

    def _interrupt_playback_on_loop(self, reason: str, force: bool = False) -> None:
        """在事件循环线程内取消当前响应并清理待下发播放队列。

        主要逻辑：
        1. 先丢弃尚未发送给 ESP32 的旧播放音频帧，避免控制消息排在大量音频后面。
        2. 重置播放节流时间，让后续新回复不会被旧节流状态拖慢。
        3. 向 Omni 转发 response.cancel，并立即通知 ESP32 清空本地播放缓冲。

        参数：
        - reason：触发取消的原因，用于日志和端侧观察。
        - force：是否按回调线程观察到的活跃状态强制执行取消。
        """

        if not force and not self._response_in_progress and not self._audio_output_started:
            return

        dropped = 0
        while True:
            try:
                pending = self._send_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if pending is None:
                self._send_queue.put_nowait(None)
                break
            if isinstance(pending, bytes):
                dropped += 1
                continue
            if isinstance(pending, dict) and pending.get("type") in {"playback_end", "playback_start"}:
                dropped += 1
                continue
            self._send_queue.put_nowait(pending)

        self._next_playback_send_at = 0.0
        self._audio_output_started = False
        self._stats.cancel_count += 1
        print(f"[relay] interrupt playback reason={reason} dropped_pending_playback={dropped}")
        self._cancel_omni_response()
        asyncio.create_task(
            self._send_json(
                {
                    "type": "playback_cancelled",
                    "stream_id": self._playback_stream_id,
                    "reason": reason,
                    "dropped_pending_playback": dropped,
                }
            )
        )

    def _enqueue_from_callback(self, message: dict[str, Any] | bytes) -> None:
        """从 DashScope 回调线程安全投递到 asyncio 队列。"""

        if self._closed:
            return
        self._loop.call_soon_threadsafe(self._send_queue.put_nowait, message)

    async def _send_json(self, message: dict[str, Any]) -> None:
        """向 ESP32 发送 JSON 文本消息。"""

        target = self._playback_state.get("websocket") or self._websocket
        if target is None:
            return
        async with self._send_lock:
            await target.send(json.dumps(message, ensure_ascii=False))

    async def _esp32_sender(self) -> None:
        """把队列中的消息发送给 ESP32。"""

        while True:
            message = await self._send_queue.get()
            if message is None:
                return
            if isinstance(message, bytes):
                target = self._playback_state.get("websocket") or self._websocket
                if target is None:
                    continue
                await self._pace_playback_frame(message)
                async with self._send_lock:
                    await target.send(message)
            else:
                await self._send_json(message)
                if message.get("type") == "playback_end":
                    self._audio_output_started = False

    async def _pace_playback_frame(self, message: bytes) -> None:
        """按 PCM 实际播放时长节流下行音频。

        主要逻辑：
        - Omni 的音频 delta 可能比真实播放速度更快到达。
        - ESP32 端 I2S 只能按 16 kHz 实时消耗，直接突发下发会填爆播放环形缓冲。
        - 这里只对 `playback_audio` 帧做节流，控制消息和非播放帧不等待。

        参数：
        - message：MediaFrame 编码后的二进制消息。

        返回值：
        - 无返回值；必要时异步等待到下一段音频应该下发的时间。

        异常情况：
        - MediaFrame 解析失败时不节流，保持试验工具不中断。
        """

        if self._args.no_playback_pacing:
            return
        try:
            frame = MediaFrame.decode(message)
        except Exception:  # noqa: BLE001 - 非播放帧或异常帧不影响转发
            return

        if str(frame.header.get("frame_type") or "") != "playback_audio":
            return
        sample_rate = int(frame.header.get("sample_rate") or PLAYBACK_RATE_HZ)
        channels = int(frame.header.get("channels") or 1)
        if sample_rate <= 0 or channels <= 0:
            return
        duration_s = len(frame.payload) / float(sample_rate * channels * 2)
        now = time.monotonic()
        if self._next_playback_send_at > now:
            await asyncio.sleep(self._next_playback_send_at - now)
            now = time.monotonic()
        self._next_playback_send_at = max(now, self._next_playback_send_at) + duration_s


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Run direct ESP32-S3 AEC Omni relay")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--ws-port", type=int, default=9886)
    parser.add_argument("--model", default=os.getenv("VOICE_OMNI_REALTIME_MODEL_NAME", DEFAULT_MODEL))
    parser.add_argument("--url", default=os.getenv("VOICE_OMNI_REALTIME_URL", DEFAULT_URL))
    parser.add_argument("--voice", default=os.getenv("VOICE_MODEL_VOICE", "Tina"))
    parser.add_argument("--api-key", default=os.getenv("DASHSCOPE_API_KEY", ""))
    parser.add_argument("--env-file", default="openaiglass-sdk/config/local_server.env")
    parser.add_argument("--server-vad", action="store_true")
    parser.add_argument("--threshold", type=float, default=0.65)
    parser.add_argument("--silence-ms", type=int, default=800)
    parser.add_argument("--prefix-ms", type=int, default=300)
    parser.add_argument("--instructions", default="你是中文语音助手。请用简短口语回答用户。")
    parser.add_argument("--record-mic-wav", default="", help="保存 ESP32 AEC 后上行音频的 WAV 路径")
    parser.add_argument("--record-playback-wav", default="", help="保存下发给 ESP32 播放音频的 WAV 路径")
    parser.add_argument(
        "--no-playback-pacing",
        action="store_true",
        help="关闭下行播放实时节流；仅用于复现固件播放缓冲溢出问题。",
    )
    return parser.parse_args()


async def _main_async(args: argparse.Namespace) -> int:
    """启动 ESP32 直连 WebSocket relay。

    参数：
    - args：命令行参数。

    返回值：
    - 退出码。

    异常情况：
    - 缺少 websockets 依赖时返回 2。
    """

    try:
        import websockets
    except ImportError:
        print("Missing websockets. Run with: uv run --with websockets --with dashscope python ...")
        return 2

    stop_event = asyncio.Event()

    def _stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)

    async def _playback_loop(websocket: Any, initial_message: str | bytes | None) -> None:
        """保持 ESP32 播放下行连接打开。

        ESP32 官方 AEC 试验链路使用两条 WebSocket：
        - mic：只上行 AEC 后麦克风音频。
        - playback：只下行 Omni 播放音频和取消控制。
        这样可以避开 `esp_websocket_client` 单连接全双工高频读写时的 transport 写入错误。
        """

        nonlocal active_playback_websocket
        if active_playback_websocket is not None and active_playback_websocket is not websocket:
            try:
                await active_playback_websocket.close(code=1012, reason="superseded by new playback connection")
                print("[relay] closed stale esp32 playback connection before accepting new one")
            except Exception as exc:  # noqa: BLE001 - 旧连接清理失败不阻塞新连接
                print(f"[relay] close stale esp32 playback connection failed: {exc!r}")
        active_playback_websocket = websocket
        playback_state["websocket"] = websocket
        print("[relay] esp32 playback connected")
        try:
            if initial_message is not None:
                print(f"[esp32] playback hello {initial_message!r}")
            async for raw in websocket:
                if isinstance(raw, str):
                    print(f"[esp32] playback text {raw}")
        except Exception as exc:  # noqa: BLE001 - ESP32 断线通常没有标准 close frame
            print(f"[relay] esp32 playback loop ended: {exc!r}")
        finally:
            if active_playback_websocket is websocket:
                active_playback_websocket = None
                playback_state["websocket"] = None
            print("[relay] esp32 playback disconnected")

    async def _handler(websocket: Any) -> None:
        nonlocal active_mic_websocket, active_session
        try:
            initial_message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
        except Exception as exc:  # noqa: BLE001 - 首包必须声明连接角色
            print(f"[relay] esp32 connection missing hello: {exc!r}")
            await websocket.close(code=1002, reason="missing hello")
            return

        role = "full_duplex"
        if isinstance(initial_message, str):
            try:
                hello = json.loads(initial_message)
                role = str(hello.get("role") or hello.get("connection_role") or role)
            except json.JSONDecodeError:
                pass

        if role == "playback":
            await _playback_loop(websocket, initial_message)
            return

        if active_mic_websocket is not None and active_mic_websocket is not websocket:
            try:
                await active_mic_websocket.close(code=1012, reason="superseded by new ESP32 mic connection")
                print("[relay] closed stale esp32 mic connection before accepting new one")
            except Exception as exc:  # noqa: BLE001 - 旧连接清理失败不阻塞新连接
                print(f"[relay] close stale esp32 mic connection failed: {exc!r}")
        if active_session is not None:
            active_session._closed = True
        active_mic_websocket = websocket
        print(f"[relay] esp32 mic connected role={role}")
        session = Esp32AecOmniRelaySession(
            websocket=websocket,
            args=args,
            loop=asyncio.get_running_loop(),
            playback_state=playback_state,
        )
        active_session = session
        try:
            await session.run(initial_message=initial_message)
        finally:
            if active_mic_websocket is websocket:
                active_mic_websocket = None
            if active_session is session:
                active_session = None

    print(f"[relay] waiting ESP32 AEC client ws://{args.host}:{args.ws_port}/ws")
    print("[relay] protocol: binary MediaFrame frame_type=mic_audio, downlink frame_type=playback_audio")
    active_mic_websocket: Any | None = None
    active_playback_websocket: Any | None = None
    active_session: Esp32AecOmniRelaySession | None = None
    playback_state: dict[str, Any] = {"websocket": None}
    async with websockets.serve(
        _handler,
        args.host,
        args.ws_port,
        ping_interval=None,
        ping_timeout=None,
        compression=None,
    ):
        await stop_event.wait()
    return 0


def main() -> int:
    """命令行入口。"""

    args = _parse_args()
    _load_env_file(Path(args.env_file))
    if not args.api_key:
        args.api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not args.api_key.strip():
        print("Missing DASHSCOPE_API_KEY. Set it in env or pass --api-key.")
        return 2
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
