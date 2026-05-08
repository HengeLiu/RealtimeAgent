"""`phone-mock` 相机帧接收服务。"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
import time
from pathlib import Path

from protocol.media import MediaFrame


class CameraSinkServer:
    """手机 mock 的最小相机流接收服务。

    主要功能：
    1. 监听本地 WebSocket 地址。
    2. 接收真实 glass 或 `glass-playback` 推来的 `camera_frame`。
    3. 保存最近一帧和接收事件，避免服务端视频链路指向一个不存在的地址。
    """

    def __init__(
        self,
        *,
        bind_host: str,
        port: int,
        public_host: str,
        path: str,
        save_dir: Path,
    ) -> None:
        self.bind_host = bind_host
        self.public_host = public_host
        self.path = path if path.startswith("/") else f"/{path}"
        self.save_dir = save_dir
        self._port = port
        self._server_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._event_log = save_dir / "frames.jsonl"

    @property
    def port(self) -> int:
        """返回实际监听端口。"""

        if self._server_sock is None:
            return self._port
        return int(self._server_sock.getsockname()[1])

    @property
    def ws_uri(self) -> str:
        """返回注册给服务端的相机接收地址。"""

        return f"ws://{self.public_host}:{self.port}{self.path}"

    def start(self) -> None:
        """启动 WebSocket 接收服务。"""

        if self._thread is not None:
            return
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.bind_host, self._port))
        self._server_sock.listen(4)
        self._server_sock.settimeout(0.5)
        self._thread = threading.Thread(target=self._serve_forever, name="phone-mock-camera-sink", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止 WebSocket 接收服务。"""

        self._stop_event.set()
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _serve_forever(self) -> None:
        assert self._server_sock is not None
        while not self._stop_event.is_set():
            try:
                conn, _addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_connection, args=(conn,), daemon=True).start()

    def _handle_connection(self, conn: socket.socket) -> None:
        conn.settimeout(1.0)
        try:
            self._perform_handshake(conn)
            while not self._stop_event.is_set():
                try:
                    opcode, payload = self._read_message(conn)
                except TimeoutError:
                    continue
                except (ConnectionError, OSError):
                    break
                if opcode == 0x2:
                    self._handle_binary_frame(payload)
                elif opcode == 0x9:
                    self._send_frame(conn, 0xA, payload)
                elif opcode == 0x8:
                    break
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _handle_binary_frame(self, payload: bytes) -> None:
        frame = MediaFrame.decode(payload)
        if str(frame.header.get("frame_type")) != "camera_frame":
            return
        latest_path = self.save_dir / "latest.jpg"
        latest_path.write_bytes(frame.payload)
        self._append_event(
            {
                "ts": int(time.time() * 1000),
                "stream_id": frame.header.get("stream_id"),
                "seq": frame.header.get("seq"),
                "bytes": len(frame.payload),
                "latest_path": str(latest_path),
            }
        )

    def _append_event(self, payload: dict[str, object]) -> None:
        with self._event_log.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _perform_handshake(self, conn: socket.socket) -> None:
        request = self._recv_until(conn, b"\r\n\r\n").decode("utf-8", errors="ignore")
        key = ""
        for line in request.split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
                break
        if not key:
            raise ConnectionError("missing websocket key")
        accept = base64.b64encode(hashlib.sha1(f"{key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11".encode("utf-8")).digest()).decode("utf-8")
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        )
        conn.sendall(response.encode("utf-8"))

    def _recv_until(self, conn: socket.socket, marker: bytes) -> bytes:
        data = bytearray()
        while marker not in data:
            chunk = conn.recv(4096)
            if not chunk:
                raise ConnectionError("socket closed before handshake completed")
            data.extend(chunk)
        return bytes(data)

    def _read_message(self, conn: socket.socket) -> tuple[int, bytes]:
        fin, opcode, payload = self._read_frame(conn)
        if fin:
            return opcode, payload
        chunks = bytearray(payload)
        while not fin:
            fin, continuation_opcode, continuation_payload = self._read_frame(conn)
            if continuation_opcode != 0x0:
                raise ConnectionError("invalid websocket continuation frame")
            chunks.extend(continuation_payload)
        return opcode, bytes(chunks)

    def _read_frame(self, conn: socket.socket) -> tuple[bool, int, bytes]:
        first, second = self._recv_exact(conn, 2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(conn, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(conn, 8))[0]
        if not masked:
            raise ConnectionError("client frame must be masked")
        mask_key = self._recv_exact(conn, 4)
        payload = self._recv_exact(conn, length)
        return fin, opcode, bytes(value ^ mask_key[index % 4] for index, value in enumerate(payload))

    @staticmethod
    def _send_frame(conn: socket.socket, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(127)
            header.extend(struct.pack("!Q", length))
        conn.sendall(bytes(header) + payload)

    @staticmethod
    def _recv_exact(conn: socket.socket, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            try:
                chunk = conn.recv(size - len(data))
            except socket.timeout as exc:
                raise TimeoutError from exc
            if not chunk:
                raise ConnectionError("socket closed unexpectedly")
            data.extend(chunk)
        return bytes(data)
