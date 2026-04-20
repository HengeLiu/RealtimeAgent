"""最小 WebSocket 传输实现。"""

from __future__ import annotations

import base64
import hashlib
import socket
import struct
from typing import Final

from api.ws.control_runtime import ControlRuntime
from infra.errors import AppError
from infra.logging import LogContext, get_logger, log_debug
from protocol.media import MediaFrame

GUID: Final[str] = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OPCODE_TEXT: Final[int] = 0x1
OPCODE_BINARY: Final[int] = 0x2
OPCODE_CONTINUATION: Final[int] = 0x0
OPCODE_CLOSE: Final[int] = 0x8
OPCODE_PING: Final[int] = 0x9
OPCODE_PONG: Final[int] = 0xA
LOGGER = get_logger("server.websocket")


class WebSocketProtocolError(RuntimeError):
    """WebSocket 协议异常。"""


def handle_control_websocket(handler, runtime: ControlRuntime) -> None:
    """处理 `/ws/control` WebSocket 请求。"""

    sock = _perform_handshake(handler)
    peer = f"{handler.client_address[0]}:{handler.client_address[1]}"

    def _send_text(text: str) -> None:
        _send_frame(sock, OPCODE_TEXT, text.encode("utf-8"))

    def _close_transport(code: int, reason: str) -> None:
        payload = struct.pack("!H", code)
        if reason:
            payload += reason.encode("utf-8")
        try:
            _send_frame(sock, OPCODE_CLOSE, payload)
        except OSError:
            pass
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    connection = runtime.open_connection(
        peer=peer,
        send_text=_send_text,
        close_transport=_close_transport,
    )

    try:
        while not connection.closed:
            try:
                opcode, payload = _read_message(sock)
            except TimeoutError:
                continue
            except (ConnectionError, OSError, WebSocketProtocolError):
                break

            if opcode == OPCODE_TEXT:
                try:
                    runtime.handle_text(connection, payload.decode("utf-8"))
                except AppError as exc:
                    log_debug(
                        LOGGER,
                        (
                            "控制消息处理失败，关闭当前控制连接: "
                            f"code={exc.code} message={exc.message} details={exc.details}"
                        ),
                        LogContext(device_id=connection.device_id, session_id=connection.session_id),
                    )
                    break
                continue
            if opcode == OPCODE_PING:
                _send_frame(sock, OPCODE_PONG, payload)
                continue
            if opcode == OPCODE_CLOSE:
                break
    finally:
        runtime.on_transport_closed(connection)
        try:
            sock.close()
        except OSError:
            pass


def handle_audio_websocket(handler, runtime: ControlRuntime, query: dict[str, list[str]]) -> None:
    """处理 `/ws_audio` WebSocket 请求。"""

    device_id = (query.get("device_id") or [""])[0].strip()
    if not device_id:
        handler.send_response(400)
        handler.end_headers()
        return

    sock = _perform_handshake(handler)
    peer = f"{handler.client_address[0]}:{handler.client_address[1]}"
    runtime.voice_runtime.on_audio_connection_opened(device_id=device_id, peer=peer)

    try:
        while True:
            try:
                opcode, payload = _read_message(sock)
            except TimeoutError:
                continue
            except (ConnectionError, OSError, WebSocketProtocolError):
                break

            if opcode == OPCODE_BINARY:
                try:
                    frame = MediaFrame.decode(payload)
                    runtime.voice_runtime.on_audio_frame(device_id=device_id, frame=frame)
                except AppError as exc:
                    log_debug(
                        LOGGER,
                        f"丢弃异常音频 WebSocket 帧: code={exc.code} message={exc.message} details={exc.details}",
                        LogContext(device_id=device_id),
                    )
                    continue
                continue
            if opcode == OPCODE_PING:
                _send_frame(sock, OPCODE_PONG, payload)
                continue
            if opcode == OPCODE_CLOSE:
                break
    finally:
        runtime.voice_runtime.on_audio_connection_closed(device_id=device_id)
        try:
            sock.close()
        except OSError:
            pass


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        try:
            part = sock.recv(size - len(chunks))
        except socket.timeout as exc:
            raise TimeoutError from exc
        if not part:
            raise ConnectionError("socket closed")
        chunks.extend(part)
    return bytes(chunks)


def _read_frame(sock: socket.socket) -> tuple[bool, int, bytes]:
    head = _recv_exact(sock, 2)
    first, second = head
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F

    if length == 126:
        length = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(sock, 8))[0]

    if not masked:
        raise WebSocketProtocolError("client frame must be masked")

    mask_key = _recv_exact(sock, 4)
    payload = _recv_exact(sock, length)
    decoded = bytes(value ^ mask_key[index % 4] for index, value in enumerate(payload))
    return fin, opcode, decoded


def _read_message(sock: socket.socket) -> tuple[int, bytes]:
    """读取一条完整 WebSocket 消息。

    主要逻辑：
    1. 先读取首帧。
    2. 若首帧已经结束，则直接返回。
    3. 若消息被分片，则持续读取 continuation 帧并拼接 payload。

    返回值：
    1. 完整消息的 opcode 和拼接后的 payload。

    异常情况：
    1. 分片序列非法时抛出 `WebSocketProtocolError`。
    """

    fin, opcode, payload = _read_frame(sock)
    if fin:
        return opcode, payload
    if opcode in {OPCODE_CLOSE, OPCODE_PING, OPCODE_PONG}:
        raise WebSocketProtocolError("控制帧不允许使用分片")

    chunks = bytearray(payload)
    while not fin:
        fin, continuation_opcode, continuation_payload = _read_frame(sock)
        if continuation_opcode != OPCODE_CONTINUATION:
            raise WebSocketProtocolError("WebSocket continuation 帧序列非法")
        chunks.extend(continuation_payload)
    return opcode, bytes(chunks)


def _perform_handshake(handler) -> socket.socket:
    key = handler.headers.get("Sec-WebSocket-Key")
    upgrade = (handler.headers.get("Upgrade") or "").lower()
    if not key or upgrade != "websocket":
        handler.send_response(400)
        handler.end_headers()
        raise ConnectionError("invalid websocket handshake")

    accept = base64.b64encode(hashlib.sha1(f"{key}{GUID}".encode("utf-8")).digest()).decode("utf-8")
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept)
    handler.end_headers()
    handler.close_connection = True

    sock = handler.connection
    sock.settimeout(1.0)
    return sock


def _send_frame(sock: socket.socket, opcode: int, payload: bytes) -> None:
    head = bytearray()
    head.append(0x80 | opcode)
    length = len(payload)
    if length < 126:
        head.append(length)
    elif length < 65536:
        head.append(126)
        head.extend(struct.pack("!H", length))
    else:
        head.append(127)
        head.extend(struct.pack("!Q", length))
    sock.sendall(bytes(head) + payload)
