"""Minimal local microphone to DashScope Omni Realtime loopback test.

This script intentionally bypasses the OpenAI Glasses server runtime. It opens
one DashScope Omni Realtime WebSocket, streams local microphone PCM directly to
the model, and writes returned PCM audio directly to the local speaker.
"""

from __future__ import annotations

import argparse
import base64
import collections
import math
import os
import queue
import random
import signal
import sys
import threading
import time
import warnings
import wave
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", message="'audioop' is deprecated.*", category=DeprecationWarning)
import audioop


DEFAULT_MODEL = "qwen3.5-omni-plus-realtime"
DEFAULT_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_VOICE = "Tina"
INPUT_SAMPLE_RATE = 16_000
OUTPUT_SAMPLE_RATE = 24_000
CHANNELS = 1
SAMPLE_WIDTH = 2
INT16_SCALE = 32768.0


class SimpleNlmsEchoCanceller:
    """Small single-channel NLMS echo canceller for local loopback testing.

    It is intentionally simple: feed it the PCM that is actually written to the
    speaker, then process microphone PCM before sending it upstream. This is not
    a production AEC, but it is useful to validate whether reference-based echo
    reduction makes Omni barge-in usable.
    """

    def __init__(
        self,
        *,
        filter_ms: int,
        delay_ms: int,
        mu: float,
        suppression_gain: float,
        doubletalk_threshold: float = 6.0,
    ) -> None:
        self._filter_len = max(16, int(INPUT_SAMPLE_RATE * filter_ms / 1000))
        self._delay_samples = max(0, int(INPUT_SAMPLE_RATE * delay_ms / 1000))
        self._mu = max(0.0, min(mu, 1.0))
        self._suppression_gain = max(0.0, min(suppression_gain, 2.0))
        self._doubletalk_threshold = max(1.0, doubletalk_threshold)
        self._weights = [0.0] * self._filter_len
        self._history = [0.0] * self._filter_len
        self._reference_fifo: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()
        self._ratecv_state = None
        self.processed_bytes = 0
        self.reference_bytes = 0

    def add_speaker_reference(self, pcm24: bytes) -> None:
        """Append actually played 24 kHz speaker PCM to the 16 kHz reference."""

        if not pcm24:
            return
        pcm16, self._ratecv_state = audioop.ratecv(
            pcm24,
            SAMPLE_WIDTH,
            CHANNELS,
            OUTPUT_SAMPLE_RATE,
            INPUT_SAMPLE_RATE,
            self._ratecv_state,
        )
        samples = _pcm16le_to_floats(pcm16)
        with self._lock:
            self._reference_fifo.extend(samples)
            max_keep = self._delay_samples + self._filter_len + INPUT_SAMPLE_RATE * 2
            while len(self._reference_fifo) > max_keep:
                self._reference_fifo.popleft()
        self.reference_bytes += len(pcm16)

    def process_mic(self, pcm16: bytes, *, playback_active: bool) -> bytes:
        """Return microphone PCM after subtracting estimated echo."""

        mic_samples = _pcm16le_to_floats(pcm16)
        if not mic_samples:
            return pcm16
        with self._lock:
            ref_samples = []
            for _ in mic_samples:
                if len(self._reference_fifo) > self._delay_samples:
                    ref_samples.append(self._reference_fifo.popleft())
                else:
                    ref_samples.append(0.0)
        ref_energy = sum(sample * sample for sample in ref_samples) / max(len(ref_samples), 1)
        if ref_energy < 1e-8:
            return pcm16
        mic_energy = sum(sample * sample for sample in mic_samples) / max(len(mic_samples), 1)
        adapt = mic_energy < ref_energy * self._doubletalk_threshold
        output: list[float] = []
        for desired, reference in zip(mic_samples, ref_samples, strict=False):
            self._history = [reference] + self._history[:-1]
            estimated_echo = 0.0
            power = 1e-6
            for weight, ref in zip(self._weights, self._history, strict=False):
                estimated_echo += weight * ref
                power += ref * ref
            error = desired - estimated_echo
            if adapt:
                scale = self._mu * error / power
                for index, ref in enumerate(self._history):
                    self._weights[index] += scale * ref
            if playback_active:
                error *= self._suppression_gain
            output.append(max(-1.0, min(1.0, error)))
        self.processed_bytes += len(pcm16)
        return _floats_to_pcm16le(output)


class SpeexEchoCanceller:
    """基于 pyaec/SpeexDSP 的单通道回声消除器。

    这个类把 24 kHz 的扬声器播放参考音频转换成 16 kHz，并按估计延迟送入
    SpeexDSP AEC。它比本文件里的简化 NLMS 更接近真实语音通话链路，但仍然
    依赖稳定的播放参考、较低失真和合理的播放/录音延迟。
    """

    def __init__(
        self,
        *,
        pyaec_module: Any,
        frame_size: int,
        filter_ms: int,
        delay_ms: int,
        suppression_gain: float,
        enable_preprocess: bool,
    ) -> None:
        """初始化 SpeexDSP AEC。

        参数:
            pyaec_module: 已导入的 pyaec 模块，避免在未启用 Speex 后端时强依赖。
            frame_size: 每次处理的 16 kHz 单声道采样点数。
            filter_ms: 回声尾长，单位毫秒。
            delay_ms: 扬声器到麦克风的估计延迟，单位毫秒。
            suppression_gain: 播放期间残余音频增益，用于压低未消干净的回声。
            enable_preprocess: 是否启用 SpeexDSP 预处理。预处理可能降低噪声，也可能引入金属感伪影。

        返回值:
            无。

        异常:
            当 pyaec 无法创建 AEC 实例时，底层会抛出 RuntimeError。
        """

        self._frame_size = max(80, frame_size)
        self._delay_samples = max(0, int(INPUT_SAMPLE_RATE * delay_ms / 1000))
        self._suppression_gain = max(0.0, min(suppression_gain, 2.0))
        filter_len = max(self._frame_size, int(INPUT_SAMPLE_RATE * filter_ms / 1000))
        self._aec = pyaec_module.Aec(
            self._frame_size,
            filter_len,
            INPUT_SAMPLE_RATE,
            enable_preprocess=enable_preprocess,
        )
        self._reference_fifo: collections.deque[int] = collections.deque()
        self._lock = threading.Lock()
        self._ratecv_state = None
        self.processed_bytes = 0
        self.reference_bytes = 0

    def add_speaker_reference(self, pcm24: bytes) -> None:
        """追加真实写入扬声器的 PCM，作为 SpeexDSP 的 far-end reference。"""

        if not pcm24:
            return
        pcm16, self._ratecv_state = audioop.ratecv(
            pcm24,
            SAMPLE_WIDTH,
            CHANNELS,
            OUTPUT_SAMPLE_RATE,
            INPUT_SAMPLE_RATE,
            self._ratecv_state,
        )
        samples = _pcm16le_to_ints(pcm16)
        with self._lock:
            self._reference_fifo.extend(samples)
            max_keep = self._delay_samples + self._frame_size * 8 + INPUT_SAMPLE_RATE * 2
            while len(self._reference_fifo) > max_keep:
                self._reference_fifo.popleft()
        self.reference_bytes += len(pcm16)

    def process_mic(self, pcm16: bytes, *, playback_active: bool) -> bytes:
        """对麦克风 PCM 做 SpeexDSP 回声消除后返回。

        参数:
            pcm16: 16 kHz 单声道 PCM16 小端字节。
            playback_active: 当前是否处于扬声器播放期。

        返回值:
            已消除回声的 PCM16 字节。长度与输入一致。

        异常:
            底层 pyaec 处理失败时会向外抛出异常，调用方会停止测试脚本。
        """

        mic_samples = _pcm16le_to_ints(pcm16)
        if not mic_samples:
            return pcm16
        with self._lock:
            ref_samples = []
            for _ in mic_samples:
                if len(self._reference_fifo) > self._delay_samples:
                    ref_samples.append(self._reference_fifo.popleft())
                else:
                    ref_samples.append(0)
        if not any(ref_samples):
            return pcm16
        output: list[int] = []
        for offset in range(0, len(mic_samples), self._frame_size):
            mic_frame = mic_samples[offset : offset + self._frame_size]
            ref_frame = ref_samples[offset : offset + self._frame_size]
            original_len = len(mic_frame)
            if original_len < self._frame_size:
                mic_frame = mic_frame + [0] * (self._frame_size - original_len)
                ref_frame = ref_frame + [0] * (self._frame_size - original_len)
            cleaned = self._aec.cancel_echo(mic_frame, ref_frame)
            output.extend(cleaned[:original_len])
        if playback_active and self._suppression_gain < 1.0:
            output = [int(max(-32768, min(32767, sample * self._suppression_gain))) for sample in output]
        self.processed_bytes += len(pcm16)
        return _ints_to_pcm16le(output)


def _pcm16le_to_floats(pcm: bytes) -> list[float]:
    """Convert little-endian PCM16 bytes to normalized float samples."""

    sample_count = len(pcm) // SAMPLE_WIDTH
    return [int.from_bytes(pcm[i * 2 : i * 2 + 2], "little", signed=True) / INT16_SCALE for i in range(sample_count)]


def _floats_to_pcm16le(samples: list[float]) -> bytes:
    """Convert normalized float samples to little-endian PCM16 bytes."""

    out = bytearray()
    for sample in samples:
        value = int(max(-1.0, min(0.999969, sample)) * 32767)
        out.extend(value.to_bytes(2, "little", signed=True))
    return bytes(out)


def _pcm16le_to_ints(pcm: bytes) -> list[int]:
    """把 PCM16 小端字节转换成整数采样列表。"""

    sample_count = len(pcm) // SAMPLE_WIDTH
    return [int.from_bytes(pcm[i * 2 : i * 2 + 2], "little", signed=True) for i in range(sample_count)]


def _ints_to_pcm16le(samples: list[int]) -> bytes:
    """把整数采样列表转换成 PCM16 小端字节。"""

    out = bytearray()
    for sample in samples:
        value = max(-32768, min(32767, int(sample)))
        out.extend(value.to_bytes(2, "little", signed=True))
    return bytes(out)


def _scale_pcm16le(pcm: bytes, gain: float) -> bytes:
    """按指定增益缩放 PCM16 小端音频，避免本地扬声器破音。"""

    gain = max(0.0, min(gain, 2.0))
    if abs(gain - 1.0) < 1e-6:
        return pcm
    return _ints_to_pcm16le([int(sample * gain) for sample in _pcm16le_to_ints(pcm)])


def _estimate_echo_delay_ms(*, probe: list[float], recorded: list[float], max_delay_ms: int) -> tuple[int, float]:
    """Estimate speaker-to-mic delay by normalized cross-correlation."""

    if not probe or not recorded:
        return 0, 0.0
    max_delay = min(max(1, int(INPUT_SAMPLE_RATE * max_delay_ms / 1000)), max(len(recorded) - 1, 1))
    probe_len = min(len(probe), max(len(recorded) - max_delay, 1), INPUT_SAMPLE_RATE)
    if probe_len <= 64:
        return 0, 0.0
    best_delay = 0
    best_score = 0.0
    probe_energy = sum(sample * sample for sample in probe[:probe_len])
    if probe_energy <= 1e-9:
        return 0, 0.0
    for delay in range(max_delay):
        rec_slice = recorded[delay : delay + probe_len]
        if len(rec_slice) < probe_len:
            break
        rec_energy = sum(sample * sample for sample in rec_slice)
        if rec_energy <= 1e-9:
            continue
        corr = sum(probe_sample * rec_sample for probe_sample, rec_sample in zip(probe[:probe_len], rec_slice))
        score = abs(corr) / math.sqrt(probe_energy * rec_energy)
        if score > best_score:
            best_score = score
            best_delay = delay
    return int(best_delay * 1000 / INPUT_SAMPLE_RATE), best_score


def _auto_tune_aec(
    *,
    sd: Any,
    input_device: int | str | None,
    output_device: int | str | None,
    probe_ms: int,
    max_delay_ms: int,
) -> tuple[int, int]:
    """Play a short probe and choose AEC delay/filter parameters."""

    probe_samples = int(INPUT_SAMPLE_RATE * max(200, probe_ms) / 1000)
    rng = random.Random(20260505)
    probe = [0.16 * (1.0 if rng.getrandbits(1) else -1.0) for _ in range(probe_samples)]
    probe_pcm16 = _floats_to_pcm16le(probe)
    probe_pcm24, _ = audioop.ratecv(
        probe_pcm16,
        SAMPLE_WIDTH,
        CHANNELS,
        INPUT_SAMPLE_RATE,
        OUTPUT_SAMPLE_RATE,
        None,
    )
    output_offset = 0
    recorded = bytearray()
    done_at = time.monotonic() + (probe_ms / 1000.0) + 0.35

    def _probe_input_callback(indata: bytes, frames: int, time_info: Any, status: Any) -> None:
        del frames, time_info
        if status:
            print(f"[aec-auto] mic status={status}", file=sys.stderr)
        recorded.extend(bytes(indata))

    def _probe_output_callback(outdata: bytearray, frames: int, time_info: Any, status: Any) -> None:
        nonlocal output_offset
        del time_info
        if status:
            print(f"[aec-auto] speaker status={status}", file=sys.stderr)
        needed = frames * CHANNELS * SAMPLE_WIDTH
        chunk = probe_pcm24[output_offset : output_offset + needed]
        outdata[: len(chunk)] = chunk
        output_offset += len(chunk)
        if len(chunk) < needed:
            outdata[len(chunk) : needed] = b"\x00" * (needed - len(chunk))

    print("[aec-auto] playing short probe to estimate speaker-to-mic delay...")
    with sd.RawInputStream(
        samplerate=INPUT_SAMPLE_RATE,
        blocksize=0,
        channels=CHANNELS,
        dtype="int16",
        device=input_device,
        callback=_probe_input_callback,
    ), sd.RawOutputStream(
        samplerate=OUTPUT_SAMPLE_RATE,
        blocksize=0,
        channels=CHANNELS,
        dtype="int16",
        device=output_device,
        callback=_probe_output_callback,
    ):
        while time.monotonic() < done_at:
            time.sleep(0.05)
    delay_ms, score = _estimate_echo_delay_ms(
        probe=probe,
        recorded=_pcm16le_to_floats(bytes(recorded)),
        max_delay_ms=max_delay_ms,
    )
    filter_ms = max(32, min(160, delay_ms + 48))
    print(f"[aec-auto] estimated_delay_ms={delay_ms} correlation={score:.3f} selected_filter_ms={filter_ms}")
    return delay_ms, filter_ms


def _load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE lines without requiring python-dotenv."""

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
    event_type = str(message.get("type") or "")
    if event_type == "response.audio.delta":
        return f"type={event_type} delta_base64_len={len(str(message.get('delta') or ''))}"
    if event_type == "response.audio_transcript.delta":
        return f"type={event_type} delta={message.get('delta')!r}"
    if event_type == "conversation.item.input_audio_transcription.completed":
        return f"type={event_type} transcript={message.get('transcript')!r}"
    if event_type == "response.audio_transcript.done":
        return f"type={event_type} transcript={message.get('transcript')!r}"
    if event_type in {
        "session.created",
        "session.updated",
        "input_audio_buffer.speech_started",
        "input_audio_buffer.speech_stopped",
        "input_audio_buffer.committed",
        "response.created",
        "response.audio.done",
        "response.done",
        "response.cancelled",
        "error",
    }:
        return f"type={event_type} payload={message}"
    return f"type={event_type}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stream local microphone audio directly to DashScope Omni Realtime "
            "and play returned audio locally."
        )
    )
    parser.add_argument("--model", default=os.getenv("VOICE_OMNI_REALTIME_MODEL_NAME", DEFAULT_MODEL))
    parser.add_argument("--url", default=os.getenv("VOICE_OMNI_REALTIME_URL", DEFAULT_URL))
    parser.add_argument("--voice", default=os.getenv("VOICE_MODEL_VOICE", DEFAULT_VOICE))
    parser.add_argument("--api-key", default=os.getenv("DASHSCOPE_API_KEY", ""))
    parser.add_argument("--env-file", default="openaiglass-sdk/config/local_server.env")
    parser.add_argument("--input-device", default=None, help="sounddevice input device id/name")
    parser.add_argument("--output-device", default=None, help="sounddevice output device id/name")
    parser.add_argument("--output-gain", type=float, default=0.85, help="local speaker playback gain")
    parser.add_argument("--block-ms", type=int, default=40, help="microphone callback block size in ms")
    parser.add_argument("--server-vad", action="store_true", help="use server_vad instead of semantic_vad")
    parser.add_argument("--threshold", type=float, default=0.65)
    parser.add_argument("--silence-ms", type=int, default=800)
    parser.add_argument("--prefix-ms", type=int, default=300)
    parser.add_argument("--record-output-wav", default="", help="optional path to save returned Omni PCM as WAV")
    parser.add_argument("--record-mic-wav", default="", help="optional path to save microphone PCM sent to Omni")
    parser.add_argument("--record-raw-mic-wav", default="", help="optional path to save microphone PCM before AEC")
    parser.add_argument("--aec", action="store_true", help="enable local echo cancellation")
    parser.add_argument(
        "--aec-backend",
        choices=["speex", "simple"],
        default="speex",
        help="AEC backend: speex uses pyaec/SpeexDSP; simple uses the built-in NLMS diagnostic implementation",
    )
    parser.add_argument("--aec-filter-ms", type=int, default=64, help="AEC adaptive filter length")
    parser.add_argument("--aec-delay-ms", type=int, default=40, help="speaker-to-mic echo delay estimate")
    parser.add_argument("--aec-mu", type=float, default=0.08, help="AEC NLMS adaptation step for --aec-backend simple")
    parser.add_argument(
        "--aec-auto-tune",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="automatically estimate AEC delay/filter before Omni connects; enabled by default when --aec is used",
    )
    parser.add_argument("--aec-probe-ms", type=int, default=800, help="AEC auto-tune probe duration")
    parser.add_argument("--aec-max-delay-ms", type=int, default=220, help="AEC auto-tune max delay search window")
    parser.add_argument(
        "--aec-suppression-gain",
        type=float,
        default=1.0,
        help="residual gain applied while speaker playback is active",
    )
    parser.add_argument(
        "--aec-preprocess",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="enable SpeexDSP preprocessing for --aec-backend speex; disabled by default to avoid metallic artifacts",
    )
    parser.add_argument(
        "--keep-mic-during-playback",
        action="store_true",
        help="do not mute local microphone while Omni audio is playing",
    )
    parser.add_argument(
        "--playback-barge-in-gate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="during playback, only send AEC microphone audio when near-end speech is strong enough",
    )
    parser.add_argument("--barge-in-rms", type=int, default=700, help="RMS threshold for playback barge-in gate")
    parser.add_argument(
        "--barge-in-trigger-frames",
        type=int,
        default=3,
        help="consecutive frames required before opening playback barge-in gate",
    )
    parser.add_argument("--barge-in-hold-ms", type=int, default=900, help="barge-in gate hold duration after trigger")
    parser.add_argument(
        "--instructions",
        default="你是中文语音助手。请用简短口语回答用户。",
        help="Realtime session instructions",
    )
    parser.add_argument("--list-devices", action="store_true", help="print local audio devices and exit")
    return parser.parse_args()


def _parse_sounddevice_selector(value: str | None) -> int | str | None:
    """Convert numeric device selectors to int; keep device names as strings."""

    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit() or (stripped.startswith("-") and stripped[1:].isdigit()):
        return int(stripped)
    return stripped


def main() -> int:
    args = _parse_args()
    _load_env_file(Path(args.env_file))
    if not args.api_key:
        args.api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not args.api_key.strip():
        print("Missing DASHSCOPE_API_KEY. Set it in env or pass --api-key.", file=sys.stderr)
        return 2

    try:
        import dashscope
        import sounddevice as sd
        from dashscope.audio.qwen_omni import (
            AudioFormat,
            MultiModality,
            OmniRealtimeCallback,
            OmniRealtimeConversation,
        )
    except ImportError as exc:
        print(
            "Missing dependency. Run: uv run --with sounddevice --with dashscope python "
            "openaiglass-sdk/server-python/devtools/omni_mic_loopback.py",
            file=sys.stderr,
        )
        print(f"Import error: {exc}", file=sys.stderr)
        return 2

    if args.list_devices:
        print(sd.query_devices())
        return 0

    dashscope.api_key = args.api_key
    input_device = _parse_sounddevice_selector(args.input_device)
    output_device = _parse_sounddevice_selector(args.output_device)
    pyaec_module = None
    if args.aec and args.aec_backend == "speex":
        try:
            import pyaec as pyaec_module
        except ImportError as exc:
            print(
                "Missing pyaec for --aec-backend speex. Run with: "
                "uv run --with sounddevice --with dashscope --with pyaec python "
                "openaiglass-sdk/server-python/devtools/omni_mic_loopback.py ... "
                "or use --aec-backend simple.",
                file=sys.stderr,
            )
            print(f"Import error: {exc}", file=sys.stderr)
            return 2
    if args.aec and args.aec_auto_tune:
        args.aec_delay_ms, args.aec_filter_ms = _auto_tune_aec(
            sd=sd,
            input_device=input_device,
            output_device=output_device,
            probe_ms=args.aec_probe_ms,
            max_delay_ms=args.aec_max_delay_ms,
        )
    stop_event = threading.Event()
    mic_queue: queue.Queue[bytes] = queue.Queue(maxsize=100)
    playback_queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
    aec_reference_queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
    playback_pending = bytearray()
    playback_lock = threading.Lock()
    playback_state = {"active": False, "audio_done": False}
    output_wav = None
    mic_wav = None
    raw_mic_wav = None
    blocksize = max(160, int(INPUT_SAMPLE_RATE * args.block_ms / 1000))
    if args.aec and args.aec_backend == "speex":
        echo_canceller = SpeexEchoCanceller(
            pyaec_module=pyaec_module,
            frame_size=blocksize,
            filter_ms=args.aec_filter_ms,
            delay_ms=args.aec_delay_ms,
            suppression_gain=args.aec_suppression_gain,
            enable_preprocess=args.aec_preprocess,
        )
    elif args.aec:
        echo_canceller = SimpleNlmsEchoCanceller(
            filter_ms=args.aec_filter_ms,
            delay_ms=args.aec_delay_ms,
            mu=args.aec_mu,
            suppression_gain=args.aec_suppression_gain,
        )
    else:
        echo_canceller = None
    stats = {
        "input_bytes": 0,
        "output_bytes": 0,
        "aec_processed_bytes": 0,
        "aec_reference_bytes": 0,
        "dropped_input_chunks": 0,
        "dropped_output_chunks": 0,
        "dropped_aec_reference_chunks": 0,
        "barge_in_gate_dropped_chunks": 0,
        "barge_in_gate_open_count": 0,
        "responses": 0,
    }

    class Callback(OmniRealtimeCallback):
        def on_open(self) -> None:
            print("[omni] websocket opened")

        def on_close(self, close_status_code, close_msg) -> None:
            print(f"[omni] websocket closed code={close_status_code} message={close_msg}")
            stop_event.set()

        def on_event(self, message: dict[str, Any]) -> None:
            event_type = str(message.get("type") or "")
            print(f"[omni] {_summarize_event(message)}")
            if event_type == "response.audio.delta":
                delta = str(message.get("delta") or "")
                if not delta:
                    return
                audio = _scale_pcm16le(base64.b64decode(delta), args.output_gain)
                stats["output_bytes"] += len(audio)
                if output_wav is not None:
                    output_wav.writeframes(audio)
                with playback_lock:
                    playback_state["active"] = True
                    playback_state["audio_done"] = False
                try:
                    playback_queue.put_nowait(audio)
                except queue.Full:
                    stats["dropped_output_chunks"] += 1
                return
            if event_type == "response.audio.done":
                with playback_lock:
                    playback_state["audio_done"] = True
                    if not playback_pending and playback_queue.empty():
                        playback_state["active"] = False
                return
            if event_type == "response.created":
                stats["responses"] += 1
                return
            if event_type == "error":
                stop_event.set()

    conversation = OmniRealtimeConversation(
        model=args.model,
        callback=Callback(),
        url=args.url.rstrip("/"),
        api_key=args.api_key,
    )

    def _handle_signal(_signum: int, _frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    sender_done = threading.Event()
    reference_done = threading.Event()
    gate_pending_frames: collections.deque[bytes] = collections.deque(
        maxlen=max(1, int(args.barge_in_trigger_frames))
    )
    gate_state = {"candidate_frames": 0, "open_until": 0.0}

    def _frames_allowed_by_barge_in_gate(pcm: bytes, *, playback_active: bool) -> list[bytes]:
        """播放期间过滤残余回声，只在检测到用户打断时放行麦克风音频。

        参数:
            pcm: 已经过 AEC 的 16 kHz PCM16 音频。
            playback_active: 当前是否正在播放模型语音。

        返回值:
            允许发送给 Omni 的 PCM 列表。返回空列表表示这一帧被当作残余回声丢弃。

        异常:
            无。该函数只做本地阈值判断。
        """

        if not args.playback_barge_in_gate or echo_canceller is None or not playback_active:
            gate_pending_frames.clear()
            gate_state["candidate_frames"] = 0
            return [pcm]
        now = time.monotonic()
        rms = audioop.rms(pcm, SAMPLE_WIDTH)
        if now < gate_state["open_until"]:
            if rms >= max(1, args.barge_in_rms // 2):
                gate_state["open_until"] = now + max(0.1, args.barge_in_hold_ms / 1000.0)
            return [pcm]
        if rms >= args.barge_in_rms:
            gate_pending_frames.append(pcm)
            gate_state["candidate_frames"] += 1
            if gate_state["candidate_frames"] >= max(1, args.barge_in_trigger_frames):
                gate_state["open_until"] = now + max(0.1, args.barge_in_hold_ms / 1000.0)
                stats["barge_in_gate_open_count"] += 1
                frames = list(gate_pending_frames)
                gate_pending_frames.clear()
                return frames
            return []
        if gate_pending_frames:
            stats["barge_in_gate_dropped_chunks"] += len(gate_pending_frames)
        gate_pending_frames.clear()
        gate_state["candidate_frames"] = 0
        stats["barge_in_gate_dropped_chunks"] += 1
        return []

    def _send_pcm_to_omni(pcm: bytes) -> bool:
        """把一帧 PCM 写入调试录音并发送给 Omni。

        参数:
            pcm: 16 kHz 单声道 PCM16 音频。

        返回值:
            发送成功返回 True；传输失败时返回 False，并设置停止事件。

        异常:
            传输异常会被捕获并打印到 stderr。
        """

        if mic_wav is not None:
            mic_wav.writeframes(pcm)
        try:
            conversation.append_audio(base64.b64encode(pcm).decode("ascii"))
            stats["input_bytes"] += len(pcm)
            return True
        except Exception as exc:  # noqa: BLE001 - test tool should stop on transport failure
            print(f"[local] append_audio failed: {exc!r}", file=sys.stderr)
            stop_event.set()
            return False

    def _sender() -> None:
        while not stop_event.is_set() or not mic_queue.empty():
            try:
                pcm = mic_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if raw_mic_wav is not None:
                raw_mic_wav.writeframes(pcm)
            if echo_canceller is not None:
                with playback_lock:
                    playback_active = playback_state["active"]
                pcm = echo_canceller.process_mic(pcm, playback_active=playback_active)
                stats["aec_processed_bytes"] = echo_canceller.processed_bytes
                stats["aec_reference_bytes"] = echo_canceller.reference_bytes
            else:
                playback_active = False
            for frame in _frames_allowed_by_barge_in_gate(pcm, playback_active=playback_active):
                if not _send_pcm_to_omni(frame):
                    break
        sender_done.set()

    def _aec_reference_worker() -> None:
        while not stop_event.is_set() or not aec_reference_queue.empty():
            try:
                pcm = aec_reference_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if echo_canceller is None:
                continue
            echo_canceller.add_speaker_reference(pcm)
            stats["aec_reference_bytes"] = echo_canceller.reference_bytes
        reference_done.set()

    def _mic_callback(indata: bytes, frames: int, time_info: Any, status: Any) -> None:
        del frames, time_info
        if status:
            print(f"[mic] {status}", file=sys.stderr)
        if stop_event.is_set():
            return
        if not args.keep_mic_during_playback and echo_canceller is None:
            with playback_lock:
                if playback_state["active"]:
                    return
        try:
            mic_queue.put_nowait(bytes(indata))
        except queue.Full:
            stats["dropped_input_chunks"] += 1

    def _speaker_callback(outdata: bytearray, frames: int, time_info: Any, status: Any) -> None:
        del time_info
        if status:
            print(f"[speaker] {status}", file=sys.stderr)
        needed = frames * CHANNELS * SAMPLE_WIDTH
        with playback_lock:
            while len(playback_pending) < needed:
                try:
                    playback_pending.extend(playback_queue.get_nowait())
                except queue.Empty:
                    break
            take = min(len(playback_pending), needed)
            outdata[:take] = playback_pending[:take]
            del playback_pending[:take]
            if playback_state["audio_done"] and not playback_pending and playback_queue.empty():
                playback_state["active"] = False
        if echo_canceller is not None and take > 0:
            try:
                aec_reference_queue.put_nowait(bytes(outdata[:take]))
            except queue.Full:
                stats["dropped_aec_reference_chunks"] += 1
        if take < needed:
            outdata[take:needed] = b"\x00" * (needed - take)

    sender_thread = threading.Thread(target=_sender, name="omni-mic-sender", daemon=True)
    reference_thread = threading.Thread(target=_aec_reference_worker, name="omni-aec-reference", daemon=True)

    try:
        if args.record_output_wav:
            output_path = Path(args.record_output_wav)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_wav = wave.open(str(output_path), "wb")
            output_wav.setnchannels(CHANNELS)
            output_wav.setsampwidth(SAMPLE_WIDTH)
            output_wav.setframerate(OUTPUT_SAMPLE_RATE)
        if args.record_mic_wav:
            mic_path = Path(args.record_mic_wav)
            mic_path.parent.mkdir(parents=True, exist_ok=True)
            mic_wav = wave.open(str(mic_path), "wb")
            mic_wav.setnchannels(CHANNELS)
            mic_wav.setsampwidth(SAMPLE_WIDTH)
            mic_wav.setframerate(INPUT_SAMPLE_RATE)
        if args.record_raw_mic_wav:
            raw_mic_path = Path(args.record_raw_mic_wav)
            raw_mic_path.parent.mkdir(parents=True, exist_ok=True)
            raw_mic_wav = wave.open(str(raw_mic_path), "wb")
            raw_mic_wav.setnchannels(CHANNELS)
            raw_mic_wav.setsampwidth(SAMPLE_WIDTH)
            raw_mic_wav.setframerate(INPUT_SAMPLE_RATE)
        with sd.RawInputStream(
            samplerate=INPUT_SAMPLE_RATE,
            blocksize=blocksize,
            channels=CHANNELS,
            dtype="int16",
            device=input_device,
            callback=_mic_callback,
        ), sd.RawOutputStream(
            samplerate=OUTPUT_SAMPLE_RATE,
            blocksize=0,
            channels=CHANNELS,
            dtype="int16",
            device=output_device,
            callback=_speaker_callback,
        ):
            print("[local] connecting Omni Realtime...")
            conversation.connect()
            conversation.update_session(
                output_modalities=[MultiModality.TEXT, MultiModality.AUDIO],
                voice=args.voice,
                input_audio_format=AudioFormat.PCM_16000HZ_MONO_16BIT,
                output_audio_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                enable_input_audio_transcription=True,
                input_audio_transcription_model="paraformer-realtime-v2",
                enable_turn_detection=True,
                turn_detection_type="server_vad" if args.server_vad else "semantic_vad",
                turn_detection_threshold=args.threshold,
                turn_detection_silence_duration_ms=args.silence_ms,
                prefix_padding_ms=args.prefix_ms,
                instructions=args.instructions,
            )
            sender_thread.start()
            if echo_canceller is not None:
                reference_thread.start()
            print(
                "[local] running. Speak into the microphone. Press Ctrl+C to stop. "
                f"turn_detection={'server_vad' if args.server_vad else 'semantic_vad'} "
                f"mute_mic_during_playback={not args.keep_mic_during_playback and echo_canceller is None} "
                f"aec_enabled={echo_canceller is not None} "
                f"aec_backend={args.aec_backend} "
                f"aec_delay_ms={args.aec_delay_ms} aec_filter_ms={args.aec_filter_ms} "
                f"aec_preprocess={args.aec_preprocess} "
                f"aec_suppression_gain={args.aec_suppression_gain} "
                f"output_gain={args.output_gain} "
                f"playback_barge_in_gate={args.playback_barge_in_gate} "
                f"barge_in_rms={args.barge_in_rms}"
            )
            while not stop_event.is_set():
                time.sleep(0.2)
    finally:
        stop_event.set()
        sender_done.wait(timeout=2)
        reference_done.wait(timeout=2)
        if output_wav is not None:
            output_wav.close()
        if mic_wav is not None:
            mic_wav.close()
        if raw_mic_wav is not None:
            raw_mic_wav.close()
        try:
            conversation.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[local] close failed: {exc!r}", file=sys.stderr)
        print(
            "[local] stopped. "
            f"input_bytes={stats['input_bytes']} output_bytes={stats['output_bytes']} "
            f"responses={stats['responses']} dropped_input_chunks={stats['dropped_input_chunks']} "
            f"dropped_output_chunks={stats['dropped_output_chunks']} "
            f"aec_processed_bytes={stats['aec_processed_bytes']} "
            f"aec_reference_bytes={stats['aec_reference_bytes']} "
            f"dropped_aec_reference_chunks={stats['dropped_aec_reference_chunks']} "
            f"barge_in_gate_dropped_chunks={stats['barge_in_gate_dropped_chunks']} "
            f"barge_in_gate_open_count={stats['barge_in_gate_open_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
