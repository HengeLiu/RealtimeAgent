"""WebRTC 全双工迁移测试 relay。

这个脚本启动一个本地 HTTP 页面和一个 WebSocket relay。浏览器页面使用
WebRTC 的回声消除能力采集麦克风，relay 负责把 PCM16 音频转发给 Omni
Realtime，并把 Omni 返回的音频和事件转回浏览器。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import signal
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "qwen3.5-omni-plus-realtime"
DEFAULT_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
MIGRATION_DIR = Path(__file__).resolve().parent
STATIC_DIR = MIGRATION_DIR / "static"
HTML_FILE = STATIC_DIR / "index.html"


def _load_env_file(path: Path) -> None:
    """读取简单环境变量文件。

    主要逻辑：
    - 支持 `KEY=VALUE` 格式。
    - 忽略空行和注释。
    - 不覆盖进程里已经存在的环境变量。

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
    """把 Omni 事件压缩成适合浏览器日志展示的短文本。

    主要逻辑：
    - 音频增量只打印 base64 长度，避免刷屏。
    - 转写和回复文本保留关键内容。
    - response.done 只保留完成状态。

    参数：
    - message：Omni Realtime 回调事件。

    返回值：
    - 用于页面日志的一行摘要。

    异常情况：
    - 事件字段缺失时返回空类型摘要，不抛异常。
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
        return f"type={event_type} status={response.get('status')}"
    return f"type={event_type}"


def _session_update_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """构造 Omni Realtime session.update 参数。

    主要逻辑：
    - 输入固定为 16k 单声道 PCM16，和浏览器发送格式保持一致。
    - 输出固定为 24k 单声道 PCM16，浏览器播放前负责重采样。
    - 默认使用 semantic VAD，也可通过 `--server-vad` 切换。

    参数：
    - args：命令行参数对象。

    返回值：
    - 可直接传给 DashScope SDK `update_session` 的参数字典。

    异常情况：
    - 本函数不访问网络，不主动抛业务异常。
    """

    from dashscope.audio.qwen_omni import AudioFormat, MultiModality

    return {
        "output_modalities": [MultiModality.TEXT, MultiModality.AUDIO],
        "voice": args.voice,
        "input_audio_format": AudioFormat.PCM_16000HZ_MONO_16BIT,
        "output_audio_format": AudioFormat.PCM_24000HZ_MONO_16BIT,
        "enable_input_audio_transcription": True,
        "input_audio_transcription_model": "paraformer-realtime-v2",
        "enable_turn_detection": True,
        "turn_detection_type": "server_vad" if args.server_vad else "semantic_vad",
        "turn_detection_threshold": args.threshold,
        "turn_detection_silence_duration_ms": args.silence_ms,
        "prefix_padding_ms": args.prefix_ms,
        "instructions": args.instructions,
    }


class _MigrationRequestHandler(SimpleHTTPRequestHandler):
    """迁移测试页面的静态文件处理器。

    主要功能：
    - 只从当前迁移目录的 `static` 子目录提供页面文件。
    - 把 HTTP 访问日志打印到终端，方便本地排查。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 父类接口固定命名
        print(f"[http] {self.address_string()} {format % args}")


def _start_http_server(port: int) -> ThreadingHTTPServer:
    """启动本地 HTTP 服务。

    参数：
    - port：监听端口。

    返回值：
    - 已启动的 `ThreadingHTTPServer`，调用方负责 shutdown。

    异常情况：
    - 端口被占用时由标准库抛出 `OSError`。
    """

    server = ThreadingHTTPServer(("127.0.0.1", port), _MigrationRequestHandler)
    thread = threading.Thread(target=server.serve_forever, name="webrtc-migration-http", daemon=True)
    thread.start()
    return server


class OmniRelaySession:
    """浏览器到 Omni 的单会话转发器。

    主要功能：
    - 建立一个 DashScope Omni Realtime 会话。
    - 把浏览器麦克风 PCM 转发给 Omni。
    - 把 Omni 音频、转写和响应事件转发回浏览器。

    主要属性：
    - `_websocket`：浏览器 WebSocket 连接。
    - `_conversation`：DashScope Omni Realtime 会话对象。
    - `_queue`：从 DashScope 回调线程切回 asyncio 线程的消息队列。
    """

    def __init__(self, *, websocket: Any, args: argparse.Namespace, loop: asyncio.AbstractEventLoop) -> None:
        self._websocket = websocket
        self._args = args
        self._loop = loop
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._conversation = None
        self._closed = False
        self._input_bytes = 0
        self._output_bytes = 0

    async def run(self) -> None:
        """运行浏览器和 Omni 之间的转发循环。

        主要逻辑：
        - 启动 Omni 会话。
        - 收到浏览器 `audio` 消息时追加音频。
        - 收到浏览器 `cancel_response` 消息时转发打断请求。

        返回值：
        - 无返回值，浏览器断开后自然结束。

        异常情况：
        - 浏览器消息不是 JSON 时异常会向外抛出，便于测试期暴露协议问题。
        """

        self._connect_omni()
        sender_task = asyncio.create_task(self._browser_sender())
        try:
            async for raw in self._websocket:
                message = json.loads(raw)
                if message.get("type") == "audio":
                    audio_b64 = str(message.get("audio") or "")
                    self._input_bytes += len(base64.b64decode(audio_b64)) if audio_b64 else 0
                    self._conversation.append_audio(audio_b64)
                elif message.get("type") == "cancel_response":
                    try:
                        self._conversation.cancel_response()
                        print("[relay] response.cancel forwarded")
                    except Exception as exc:  # noqa: BLE001 - 本地测试工具保持会话可继续排查
                        print(f"[relay] response.cancel failed: {exc!r}")
        finally:
            self._closed = True
            await self._queue.put(None)
            sender_task.cancel()
            try:
                await sender_task
            except asyncio.CancelledError:
                pass
            if self._conversation is not None:
                self._conversation.close()
            print(
                "[relay] browser disconnected "
                f"input_bytes={self._input_bytes} output_bytes={self._output_bytes}"
            )

    def _connect_omni(self) -> None:
        """创建并配置 Omni Realtime 会话。

        主要逻辑：
        - 使用 DashScope SDK 建立 Realtime WebSocket。
        - 注册回调，把音频 delta 和事件摘要推给浏览器。

        返回值：
        - 无返回值，结果保存在 `_conversation`。

        异常情况：
        - SDK 导入失败、鉴权失败、网络失败会直接抛出，便于本地验证时暴露。
        """

        import dashscope
        from dashscope.audio.qwen_omni import OmniRealtimeCallback, OmniRealtimeConversation

        dashscope.api_key = self._args.api_key
        relay = self

        class Callback(OmniRealtimeCallback):
            def on_open(self) -> None:
                print("[omni] websocket opened")

            def on_close(self, close_status_code: Any, close_msg: Any) -> None:
                print(f"[omni] websocket closed code={close_status_code} message={close_msg}")

            def on_event(self, message: dict[str, Any]) -> None:
                event_type = str(message.get("type") or "")
                if event_type == "response.audio.delta":
                    audio = str(message.get("delta") or "")
                    if audio:
                        relay._output_bytes += len(base64.b64decode(audio))
                        relay._push_to_browser({"type": "audio", "audio": audio})
                    return
                summary = _summarize_event(message)
                relay._push_to_browser({"type": "event", "summary": summary})
                print(f"[omni] {summary}")

        self._conversation = OmniRealtimeConversation(
            model=self._args.model,
            callback=Callback(),
            url=self._args.url.rstrip("/"),
            api_key=self._args.api_key,
        )
        self._conversation.connect()
        self._conversation.update_session(**_session_update_kwargs(self._args))

    def _push_to_browser(self, message: dict[str, Any]) -> None:
        """从 DashScope 回调线程安全地推送消息到浏览器队列。

        参数：
        - message：要发送给浏览器的 JSON 消息。

        返回值：
        - 无返回值。

        异常情况：
        - 会话已关闭时直接丢弃消息。
        """

        if self._closed:
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, message)

    async def _browser_sender(self) -> None:
        """把队列中的 Omni 事件发送给浏览器。

        返回值：
        - 无返回值，收到 `None` 哨兵后结束。

        异常情况：
        - WebSocket 发送失败时由 websockets 库抛出，调用方负责结束会话。
        """

        while True:
            message = await self._queue.get()
            if message is None:
                return
            await self._websocket.send(json.dumps(message, ensure_ascii=False))


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。

    返回值：
    - `argparse.Namespace`，包含 relay 启动和 Omni 会话配置。
    """

    parser = argparse.ArgumentParser(description="Run WebRTC full-duplex Omni migration relay")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--ws-port", type=int, default=9876)
    parser.add_argument("--http-port", type=int, default=9877)
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
    return parser.parse_args()


async def _main_async(args: argparse.Namespace) -> int:
    """启动 HTTP 和 WebSocket relay 服务。

    参数：
    - args：命令行参数对象。

    返回值：
    - 进程退出码，0 表示正常停止。

    异常情况：
    - 缺少 websockets 依赖时返回 2。
    """

    try:
        import websockets
    except ImportError:
        print("Missing websockets. Run with: uv run --with websockets --with dashscope python ...")
        return 2

    http_server = _start_http_server(args.http_port)
    print(f"[http] open http://127.0.0.1:{args.http_port}/")
    print(f"[relay] websocket ws://{args.host}:{args.ws_port}/ws")

    stop_event = asyncio.Event()

    def _stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)

    async def _handler(websocket: Any) -> None:
        print("[relay] browser connected")
        session = OmniRelaySession(websocket=websocket, args=args, loop=asyncio.get_running_loop())
        await session.run()

    async with websockets.serve(_handler, args.host, args.ws_port):
        await stop_event.wait()
    http_server.shutdown()
    return 0


def main() -> int:
    """命令行入口。

    返回值：
    - 进程退出码。

    异常情况：
    - 缺少 API Key 或页面文件时返回 2。
    """

    args = _parse_args()
    _load_env_file(Path(args.env_file))
    if not args.api_key:
        args.api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not args.api_key.strip():
        print("Missing DASHSCOPE_API_KEY. Set it in env or pass --api-key.")
        return 2
    if not HTML_FILE.exists():
        print(f"Missing HTML page: {HTML_FILE}")
        return 2
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
