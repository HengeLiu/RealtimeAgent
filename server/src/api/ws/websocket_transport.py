"""最小 WebSocket 传输实现。"""

from __future__ import annotations

import base64
import hashlib
import socket
import struct
from typing import Final

from api.ws.control_runtime import ControlRuntime

GUID: Final[str] = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OPCODE_TEXT: Final[int] = 0x1
OPCODE_CLOSE: Final[int] = 0x8
OPCODE_PING: Final[int] = 0x9
OPCODE_PONG: Final[int] = 0xA


class WebSocketProtocolError(RuntimeError):
    """WebSocket 协议异常。"""


def handle_control_websocket(handler, runtime: ControlRuntime) -> None:
    """处理 `/ws/control` WebSocket 请求。"""

    key = handler.headers.get("Sec-WebSocket-Key")
    upgrade = (handler.headers.get("Upgrade") or "").lower()
    if not key or upgrade != "websocket":
        handler.send_response(400)
        handler.end_headers()
        return

    accept = base64.b64encode(hashlib.sha1(f"{key}{GUID}".encode("utf-8")).digest()).decode("utf-8")
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept)
    handler.end_headers()
    handler.close_connection = True

    sock = handler.connection
    sock.settimeout(1.0)
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
                opcode, payload = _read_frame(sock)
            except TimeoutError:
                continue
            except (ConnectionError, OSError, WebSocketProtocolError):
                break

            if opcode == OPCODE_TEXT:
                runtime.handle_text(connection, payload.decode("utf-8"))
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


def _read_frame(sock: socket.socket) -> tuple[int, bytes]:
    head = _recv_exact(sock, 2)
    first, second = head
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
    return opcode, decoded


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
