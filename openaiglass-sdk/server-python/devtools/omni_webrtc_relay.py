"""Browser WebRTC AEC to DashScope Omni Realtime relay.

This devtool serves a local browser page that uses WebRTC getUserMedia audio
processing, then relays PCM16 microphone frames to Omni Realtime. Omni audio is
sent back to the same browser page for playback so the browser can use its
native echo-cancellation path.
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
DEVTOOLS_DIR = Path(__file__).resolve().parent
HTML_FILE = DEVTOOLS_DIR / "omni_webrtc_aec.html"


def _load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE config lines into environment variables."""

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
    """Return a compact event summary for the browser log."""

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


class _DevtoolRequestHandler(SimpleHTTPRequestHandler):
    """Serve the WebRTC AEC test page from the devtools directory."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(DEVTOOLS_DIR), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - inherited API name
        print(f"[http] {self.address_string()} {format % args}")


def _start_http_server(port: int) -> ThreadingHTTPServer:
    """Start a tiny HTTP server for the browser test page."""

    server = ThreadingHTTPServer(("127.0.0.1", port), _DevtoolRequestHandler)
    thread = threading.Thread(target=server.serve_forever, name="omni-webrtc-http", daemon=True)
    thread.start()
    return server


class OmniRelaySession:
    """One browser-to-Omni relay session.

    Main responsibilities:
    1. Open one DashScope Omni Realtime WebSocket.
    2. Forward browser microphone PCM to Omni.
    3. Forward Omni audio/text events back to the browser.
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
        """Run the relay until browser disconnects or Omni fails."""

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
                    except Exception as exc:  # noqa: BLE001 - devtool should keep browser session alive
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
        """Create and configure the Omni Realtime session."""

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
                if event_type == "response.audio.delta":
                    audio = str(message.get("delta") or "")
                    if audio:
                        relay._output_bytes += len(base64.b64decode(audio))
                        relay._push_to_browser({"type": "audio", "audio": audio})
                    return
                relay._push_to_browser({"type": "event", "summary": _summarize_event(message)})
                print(f"[omni] {_summarize_event(message)}")

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

    def _push_to_browser(self, message: dict[str, Any]) -> None:
        """Push a message from DashScope callback thread to asyncio queue."""

        if self._closed:
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, message)

    async def _browser_sender(self) -> None:
        """Send queued Omni events to the browser."""

        while True:
            message = await self._queue.get()
            if message is None:
                return
            await self._websocket.send(json.dumps(message, ensure_ascii=False))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run browser WebRTC AEC Omni relay")
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
    """Start HTTP and WebSocket relay servers."""

    try:
        import websockets
    except ImportError:
        print("Missing websockets. Run with: uv run --with websockets --with dashscope python ...")
        return 2

    http_server = _start_http_server(args.http_port)
    print(f"[http] open http://127.0.0.1:{args.http_port}/omni_webrtc_aec.html")
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
