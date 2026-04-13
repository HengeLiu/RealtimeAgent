#!/usr/bin/env python3
"""本地 Phase C 语音闭环模拟客户端。"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import time
import wave
from urllib.parse import urlencode
from urllib.request import urlopen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase C 本地语音链路模拟客户端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--device-id", default="glass-001")
    parser.add_argument("--pair-token", default="pair-demo-token")
    parser.add_argument("--wav", default=None, help="可选，本地 16k/mono/16bit wav 文件")
    parser.add_argument("--save-reply", default=None, help="可选，保存下行 wav 到指定路径")
    return parser.parse_args()


class WsClient:
    def __init__(self, host: str, port: int, path: str) -> None:
        self.sock = socket.create_connection((host, port), timeout=3)
        self.sock.settimeout(3.0)
        self._handshake(host, port, path)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def send_binary(self, payload: bytes) -> None:
        self._send_frame(0x2, payload)

    def recv_text(self) -> str:
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            payload = self._recv_exact(length)
            if opcode == 0x1:
                return payload.decode("utf-8")
            if opcode == 0x8:
                raise ConnectionError("websocket closed")

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        head = bytearray()
        head.append(0x80 | opcode)
        length = len(payload)
        mask_key = os.urandom(4)
        if length < 126:
            head.append(0x80 | length)
        elif length < 65536:
            head.append(0x80 | 126)
            head.extend(struct.pack("!H", length))
        else:
            head.append(0x80 | 127)
            head.extend(struct.pack("!Q", length))
        masked = bytes(value ^ mask_key[index % 4] for index, value in enumerate(payload))
        self.sock.sendall(bytes(head) + mask_key + masked)

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("utf-8")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("utf-8"))
        response = self._recv_until(b"\r\n\r\n").decode("utf-8")
        if "101 Switching Protocols" not in response:
            raise RuntimeError(f"websocket handshake failed: {response}")

    def _recv_until(self, marker: bytes) -> bytes:
        buf = bytearray()
        while marker not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("socket closed")
            buf.extend(chunk)
        return bytes(buf)

    def _recv_exact(self, size: int) -> bytes:
        buf = bytearray()
        while len(buf) < size:
            chunk = self.sock.recv(size - len(buf))
            if not chunk:
                raise ConnectionError("socket closed")
            buf.extend(chunk)
        return bytes(buf)


def now_ms() -> int:
    return int(time.time() * 1000)


def build_endpoint(device_id: str, device_type: str, module: str) -> dict[str, str]:
    return {"device_id": device_id, "device_type": device_type, "module": module}


def build_message(name: str, semantic: str, source: dict, target: dict, payload: dict, *, session_id: str | None = None, stream_id: str | None = None) -> str:
    message = {
        "version": "v1",
        "message_id": f"msg_{now_ms()}_{os.urandom(4).hex()}",
        "channel": "control",
        "semantic": semantic,
        "name": name,
        "source": source,
        "target": target,
        "ts": now_ms(),
        "payload": payload,
        "meta": {},
    }
    if session_id:
        message["session_id"] = session_id
    if stream_id:
        message["stream_id"] = stream_id
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def load_pcm_chunks(path: str | None) -> list[bytes]:
    if path is None:
        return [b"\x20\x00" * 320 for _ in range(20)]

    with wave.open(path, "rb") as wav_file:
        if wav_file.getframerate() != 16000 or wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2:
            raise ValueError("仅支持 16kHz/mono/16bit wav")
        data = wav_file.readframes(wav_file.getnframes())
    return [data[offset : offset + 640] for offset in range(0, len(data), 640) if data[offset : offset + 640]]


def build_media_frame(stream_id: str, segment_id: str, seq: int, payload: bytes) -> bytes:
    header = {
        "version": "v1",
        "stream_id": stream_id,
        "segment_id": segment_id,
        "frame_type": "audio_chunk",
        "seq": seq,
        "ts_ms": now_ms(),
        "codec": "pcm16le",
        "sample_rate": 16000,
        "channels": 1,
        "payload_size": len(payload),
        "final": False,
    }
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    return len(header_bytes).to_bytes(4, "big") + header_bytes + payload


def main() -> None:
    args = parse_args()
    control = WsClient(args.host, args.port, "/ws/control")
    source = build_endpoint(args.device_id, "glass", "glass-api")
    target = build_endpoint("server-main", "server", "server-api")

    try:
        control.send_text(
            build_message(
                "device.register",
                "request",
                source,
                target,
                {
                    "device_id": args.device_id,
                    "device_type": "glass",
                    "firmware_version": "phase-c-sim",
                    "auth": {"mode": "pair_token", "pair_token": args.pair_token},
                },
            )
        )
        registered = json.loads(control.recv_text())
        print(f"[client] recv: {registered['name']}")
        opened = json.loads(control.recv_text())
        print(f"[client] recv: {opened['name']} session_id={opened.get('session_id')}")
        session_id = opened["session_id"]

        control.send_text(
            build_message(
                "voice.session.opened",
                "notify",
                source,
                target,
                {"device_id": args.device_id},
                session_id=session_id,
            )
        )

        audio = WsClient(args.host, args.port, f"/ws_audio?{urlencode({'device_id': args.device_id})}")
        try:
            stream_id = f"stream_{os.urandom(4).hex()}"
            segment_id = f"seg_{os.urandom(4).hex()}"
            chunks = load_pcm_chunks(args.wav)

            control.send_text(
                build_message(
                    "sensor.audio.segment.started",
                    "notify",
                    source,
                    target,
                    {
                        "device_id": args.device_id,
                        "stream_id": stream_id,
                        "segment_id": segment_id,
                        "sample_rate": 16000,
                        "channels": 1,
                        "codec": "pcm16",
                    },
                    session_id=session_id,
                )
            )
            time.sleep(0.05)

            total_bytes = 0
            for index, chunk in enumerate(chunks):
                audio.send_binary(build_media_frame(stream_id, segment_id, index, chunk))
                total_bytes += len(chunk)
                time.sleep(0.02)

            control.send_text(
                build_message(
                    "sensor.audio.segment.finished",
                    "notify",
                    source,
                    target,
                    {
                        "device_id": args.device_id,
                        "stream_id": stream_id,
                        "segment_id": segment_id,
                        "duration_ms": len(chunks) * 20,
                        "bytes": total_bytes,
                        "finish_reason": "endpoint_detected",
                    },
                    session_id=session_id,
                )
            )

            play = json.loads(control.recv_text())
            play_stream_id = play.get("stream_id") or play["payload"]["stream_id"]
            print(f"[client] recv: {play['name']} stream_id={play_stream_id}")

            with urlopen(
                f"http://{args.host}:{args.port}/stream.wav?{urlencode({'device_id': args.device_id, 'stream_id': play_stream_id})}",
                timeout=10,
            ) as response:
                wav_bytes = response.read()
            print(f"[client] downloaded reply wav bytes={len(wav_bytes)}")
            if args.save_reply:
                with open(args.save_reply, "wb") as file:
                    file.write(wav_bytes)
                print(f"[client] saved reply to {args.save_reply}")

            control.send_text(
                build_message(
                    "actuator.audio.started",
                    "notify",
                    source,
                    target,
                    {"device_id": args.device_id, "stream_id": play_stream_id},
                    session_id=session_id,
                    stream_id=play_stream_id,
                )
            )
            control.send_text(
                build_message(
                    "actuator.audio.finished",
                    "notify",
                    source,
                    target,
                    {"device_id": args.device_id, "stream_id": play_stream_id},
                    session_id=session_id,
                    stream_id=play_stream_id,
                )
            )
            print("[client] round completed")
        finally:
            audio.close()
    finally:
        control.close()


if __name__ == "__main__":
    main()
