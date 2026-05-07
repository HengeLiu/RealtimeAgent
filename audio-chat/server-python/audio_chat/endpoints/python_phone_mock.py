from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from audio_chat.endpoints.python_playback import NetworkPythonPlaybackEndpoint
from audio_chat.protocol import Event, StreamChunk


class NetworkPythonPhoneMockEndpoint(NetworkPythonPlaybackEndpoint):
    """基于真实网络协议的 Python 手机参考端。

    主要功能：
    1. 模拟一台同 user 下的手机端设备。
    2. 通过控制 WebSocket 注册 capability 和 subscription。
    3. 通过 stream WebSocket 上传 `sensor.rgb`，并消费 `actuator.speaker`
       / `actuator.haptic` 下行 stream。

    主要方法：
    1. `run_until_registered()`：只注册设备，供多端联调测试组合使用。
    2. `run_once()`：执行一次唤醒、音频输入、输出消费闭环。

    主要属性：
    1. `sensor_events`：收到的传感器采集控制事件摘要。
    2. `actuator_streams`：收到的执行器 stream 摘要。

    异常情况：
    1. server 未启动、注册失败或 WebSocket 协议异常时向上抛出 RuntimeError。
    """

    def __init__(
        self,
        *,
        server_url: str,
        user_id: str,
        device_id: str,
        runs_root: str = "runs/audio-chat",
        auth: dict[str, Any] | None = None,
        device_name: str = "python-phone-mock",
        client_type: str = "python-phone-mock",
        capabilities: dict[str, Any] | None = None,
        subscriptions: list[dict[str, Any]] | None = None,
        rgb_payload: bytes | None = None,
    ) -> None:
        super().__init__(
            server_url=server_url,
            user_id=user_id,
            device_id=device_id,
            runs_root=runs_root,
            auth=auth,
            device_name=device_name,
            client_type=client_type,
            capabilities=capabilities
            or {
                "streams.produce": ["sensor.rgb", "sensor.depth", "sensor.imu"],
                "streams.consume": ["actuator.speaker", "actuator.haptic"],
                "sensor.rgb": True,
                "sensor.depth": True,
                "sensor.imu": True,
                "actuator.haptic": True,
            },
            subscriptions=subscriptions
            or [
                {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}},
                {"event": "stream.control.*", "filter": {"stream_type": "sensor.depth"}},
                {"event": "stream.control.*", "filter": {"stream_type": "sensor.imu"}},
                {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
                {"event": "stream.output.*", "filter": {"stream_type": "actuator.haptic"}},
                {"event": "control.device.command.*"},
            ],
            rgb_payload=rgb_payload,
        )
        self.sensor_events: list[dict[str, Any]] = []
        self.actuator_streams: list[dict[str, Any]] = []

    async def _control_loop(self, control_ws, stream_ws, audio_payload: bytes | None) -> None:
        async for message in control_ws:
            if message.type.name != "TEXT":
                continue
            event = Event.from_dict(json.loads(message.data))
            self.received_events.append(event)
            if event.event_name == "stream.control.configure.requested":
                self.sensor_events.append(
                    {
                        "event_name": event.event_name,
                        "stream_type": event.stream_type,
                        "request_id": event.payload.get("request_id"),
                    }
                )
                if event.stream_type == "sensor.rgb":
                    await self._open_and_send_rgb_asset(control_ws, stream_ws, event)
            elif event.event_name == "stream.output.close.requested":
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.finished",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=event.session_id,
                        stream_id=event.stream_id,
                        stream_type=event.stream_type,
                        payload={"stream_type": event.stream_type},
                    ),
                )
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.closed",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=event.session_id,
                        stream_id=event.stream_id,
                        stream_type=event.stream_type,
                        payload={"stream_type": event.stream_type, "reason": "phone_mock_closed"},
                    ),
                )
                self._output_closed.set()
            elif event.event_name == "stream.output.cancel.requested":
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.cancelled",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=event.session_id,
                        stream_id=event.stream_id,
                        stream_type=event.stream_type,
                        payload={"stream_type": event.stream_type, "reason": "phone_mock_cancelled"},
                    ),
                )
            elif event.event_name == "control.audio_session.close.requested":
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="control.audio_session.closed",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=event.session_id,
                        payload={"reason": "phone_mock_closed"},
                    ),
                )
                self._session_closed.set()

    async def _stream_loop(self, control_ws, stream_ws) -> None:
        async for message in stream_ws:
            if message.type.name != "BINARY":
                continue
            from audio_chat.protocol import StreamChunkCodec

            chunk = StreamChunkCodec.decode(message.data)
            if chunk.stream_id not in self._started_output_streams:
                self._started_output_streams.add(chunk.stream_id)
                await self._send_event(
                    control_ws,
                    Event(
                        event_name="stream.output.started",
                        user_id=self.user_id,
                        producer_id=self.device_id,
                        session_id=chunk.session_id,
                        stream_id=chunk.stream_id,
                        stream_type=chunk.stream_type,
                        payload={"stream_type": chunk.stream_type},
                    ),
                )
            self.output_chunks.append(chunk)
            self.actuator_streams.append(_chunk_summary(chunk))

    def _build_result(self) -> dict[str, Any]:
        result = super()._build_result()
        result["endpoint"] = "python-phone-mock"
        result["sensor_events"] = list(self.sensor_events)
        result["actuator_streams"] = list(self.actuator_streams)
        result["capabilities"] = dict(self.capabilities)
        result["subscriptions"] = list(self.subscriptions)
        return result


def _chunk_summary(chunk: StreamChunk) -> dict[str, Any]:
    """生成执行器 stream 诊断摘要。"""

    return {
        "stream_id": chunk.stream_id,
        "stream_type": chunk.stream_type,
        "seq": chunk.seq,
        "payload_size": len(chunk.payload),
        "final": chunk.final,
    }


async def run_network_phone_mock(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """按配置运行一次 Python phone mock 网络闭环。"""

    config = config or {}
    endpoint = NetworkPythonPhoneMockEndpoint(
        server_url=config.get("server_url", "http://127.0.0.1:8765"),
        user_id=config.get("user_id", "user-phone-mock-001"),
        device_id=config.get("device_id", "dev-python-phone-mock-001"),
        runs_root=config.get("runs_root", "runs/audio-chat"),
        auth=dict(config.get("auth") or {"mode": "disabled"}),
    )
    if config.get("mode", "register_only") in {"register_only", "network_register"}:
        from aiohttp import ClientSession

        async with ClientSession() as session:
            control_ws = await endpoint.run_until_registered(session=session)
            try:
                await asyncio.sleep(float(config.get("hold_seconds", 0.05)))
            finally:
                await control_ws.close()
        result = endpoint._build_result()
        result["passed"] = "control.device.registered" in result["event_names"]
        result["mode"] = "register_only"
        return result
    return await endpoint.run_once()


def main(argv: list[str] | None = None) -> None:
    """Python phone mock CLI 入口。"""

    parser = argparse.ArgumentParser(prog="audio-chat.phone.mock", description="启动 audio-chat Python phone mock")
    parser.add_argument("--config", default="")
    args = parser.parse_args(argv)
    config: dict[str, Any] = {}
    if args.config:
        text = Path(args.config).read_text(encoding="utf-8")
        if text.strip().startswith("{"):
            config = json.loads(text)
        else:
            import yaml

            config = yaml.safe_load(text) or {}
    result = asyncio.run(run_network_phone_mock(config))
    if not result.get("passed", False):
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
