"""桌面环境下的手机协议调试入口。

当前文件仅用于本地协议验证与桌面联调，不作为 iOS 手机端正式实现。
正式手机端 SDK 运行时请通过业务入口 `scripts/run_phone.sh` 启动。
"""

from __future__ import annotations

import argparse
import base64
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = APP_ROOT.parent
SDK_PYTHON_DIR = REPO_ROOT / "openaiglass-sdk" / "python"
if str(SDK_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(SDK_PYTHON_DIR))

from protocol.codec.json_codec import JsonMessageCodec
from protocol.media import MediaFrame
from protocol.messages.control_message import Endpoint
from protocol.utils.message_factory import create_control_message


class SimpleWebSocketClient:
    """手机端最小 WebSocket 客户端。

    主要功能：
    1. 与服务端 `/ws/control` 建立最小 WebSocket 连接。
    2. 支持发送和接收文本消息。
    3. 不依赖额外第三方库，便于当前阶段快速联调。
    """

    def __init__(self, host: str, port: int, path: str) -> None:
        self._sock = socket.create_connection((host, port), timeout=3)
        self._sock.settimeout(2.0)
        self._perform_handshake(host, port, path)

    def close(self) -> None:
        """关闭底层连接。"""

        try:
            self._sock.close()
        except OSError:
            pass

    def send_text(self, text: str) -> None:
        """发送文本帧。"""

        payload = text.encode("utf-8")
        header = bytearray([0x81])
        length = len(payload)
        mask_key = os.urandom(4)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        masked = bytes(value ^ mask_key[index % 4] for index, value in enumerate(payload))
        self._sock.sendall(bytes(header) + mask_key + masked)

    def recv_text(self) -> str:
        """接收一条文本帧。"""

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
                raise ConnectionError("server closed websocket")
            if opcode == 0x9:
                self._sock.sendall(b"\x8A" + bytes([len(payload)]) + payload)

    def _perform_handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("utf-8")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode("utf-8"))
        response = self._recv_until(b"\r\n\r\n").decode("utf-8")
        if "101 Switching Protocols" not in response:
            raise ConnectionError(f"websocket handshake failed: {response}")

    def _recv_until(self, marker: bytes) -> bytes:
        data = bytearray()
        while marker not in data:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("socket closed before handshake completed")
            data.extend(chunk)
        return bytes(data)

    def _recv_exact(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self._sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("socket closed unexpectedly")
            data.extend(chunk)
        return bytes(data)


class CameraSinkServer:
    """手机端最小相机流接收服务。

    主要功能：
    1. 监听本地 `/ws/camera` WebSocket。
    2. 接收 `MediaFrame(camera_frame)`。
    3. 将最近一帧 JPEG 落盘到本地目录。
    """

    def __init__(self, *, bind_host: str, port: int, device_id: str) -> None:
        self._bind_host = bind_host
        self._port = port
        self._device_id = device_id
        self._server_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._output_dir = ROOT_DIR / "runs" / "phone-camera" / device_id
        self._output_dir.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> str:
        """返回接收路径。"""

        return "/ws/camera"

    def start(self) -> None:
        """启动本地接收服务。"""

        if self._thread is not None:
            return
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self._bind_host, self._port))
        self._server_sock.listen(2)
        self._server_sock.settimeout(0.5)
        self._thread = threading.Thread(target=self._serve_forever, name="phone-camera-sink", daemon=True)
        self._thread.start()
        print(
            "[phone] 相机流接收服务已启动: "
            f"bind={self._bind_host}:{self.port}{self.path}"
        )

    def stop(self) -> None:
        """停止本地接收服务。"""

        self._stop_event.set()
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)

    @property
    def port(self) -> int:
        """返回监听端口。"""

        if self._server_sock is None:
            return self._port
        return int(self._server_sock.getsockname()[1])

    def _serve_forever(self) -> None:
        assert self._server_sock is not None
        while not self._stop_event.is_set():
            try:
                conn, _addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            thread = threading.Thread(target=self._handle_connection, args=(conn,), daemon=True)
            thread.start()

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
                    continue
                if opcode == 0x9:
                    self._send_frame(conn, 0xA, payload)
                    continue
                if opcode == 0x8:
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
        latest_path = self._output_dir / "latest.jpg"
        latest_path.write_bytes(frame.payload)
        print(
            "[phone] camera frame received: "
            f"stream_id={frame.header.get('stream_id')} "
            f"seq={frame.header.get('seq')} "
            f"bytes={len(frame.payload)} "
            f"saved={latest_path}"
        )

    def _perform_handshake(self, conn: socket.socket) -> None:
        request = self._recv_until(conn, b"\r\n\r\n").decode("utf-8", errors="ignore")
        lines = request.split("\r\n")
        key = ""
        for line in lines:
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
                break
        if not key:
            raise ConnectionError("missing websocket key")
        accept = base64.b64encode(
            __import__("hashlib").sha1(f"{key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11".encode("utf-8")).digest()
        ).decode("utf-8")
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
        head = self._recv_exact(conn, 2)
        first, second = head
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
        decoded = bytes(value ^ mask_key[index % 4] for index, value in enumerate(payload))
        return fin, opcode, decoded

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


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    返回值：
    1. 命令行参数对象。
    """

    parser = argparse.ArgumentParser(description="手机端最小控制面入口")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="服务端地址")
    parser.add_argument("--port", type=int, default=8765, help="服务端端口")
    parser.add_argument("--device-id", type=str, default="phone-001", help="手机设备编号")
    parser.add_argument("--pair-token", type=str, default="pair-phone-token", help="手机配对令牌")
    parser.add_argument("--heartbeat-interval", type=float, default=3.0, help="心跳间隔（秒）")
    parser.add_argument("--duration", type=float, default=0.0, help="运行时长，0 表示持续运行")
    parser.add_argument("--camera-bind-host", type=str, default="0.0.0.0", help="相机接收服务监听地址")
    parser.add_argument("--camera-public-host", type=str, default="127.0.0.1", help="上报给服务端的可访问主机地址")
    parser.add_argument("--camera-port", type=int, default=0, help="相机接收服务监听端口，0 表示自动分配")
    parser.add_argument("--once", action="store_true", help="仅完成注册后立即退出")
    return parser.parse_args()


def main() -> None:
    """手机端主循环。

    主要逻辑：
    1. 建立控制连接并发送 `device.register(device_type=phone)`。
    2. 注册成功后进入心跳循环。
    3. 若收到 `device.binded`，打印当前绑定关系。
    """

    args = parse_args()
    codec = JsonMessageCodec()
    camera_sink_server = CameraSinkServer(
        bind_host=args.camera_bind_host,
        port=args.camera_port,
        device_id=args.device_id,
    )
    camera_sink_server.start()
    client = SimpleWebSocketClient(args.host, args.port, "/ws/control")
    phone_endpoint = Endpoint(device_id=args.device_id, device_type="phone", module="phone-api")
    server_endpoint = Endpoint(device_id="server-main", device_type="server", module="server-api")

    try:
        register_message = create_control_message(
            semantic="request",
            name="device.register",
            source=phone_endpoint,
            target=server_endpoint,
            payload={
                "device_id": args.device_id,
                "device_type": "phone",
                "firmware_version": "0.1.0",
                "camera_sink_ws_uri": f"ws://{args.camera_public_host}:{camera_sink_server.port}{camera_sink_server.path}",
                "auth": {
                    "mode": "pair_token",
                    "pair_token": args.pair_token,
                },
            },
        )
        client.send_text(codec.encode(register_message).decode("utf-8"))
        print(f"[phone] 已发送 device.register: device_id={args.device_id}")

        registered = codec.decode(client.recv_text())
        print(f"[phone] 收到: name={registered.name}")
        if registered.name != "device.registered":
            raise RuntimeError(f"unexpected first message: {registered.name}")

        if args.once:
            print("[phone] --once 模式，注册完成后退出")
            return

        started_at = time.monotonic()
        next_heartbeat_at = 0.0
        while True:
            now = time.monotonic()
            if now >= next_heartbeat_at:
                heartbeat = create_control_message(
                    semantic="notify",
                    name="device.heartbeat",
                    source=phone_endpoint,
                    target=server_endpoint,
                    payload={"device_id": args.device_id},
                )
                client.send_text(codec.encode(heartbeat).decode("utf-8"))
                print("[phone] 已发送 device.heartbeat")
                next_heartbeat_at = now + max(args.heartbeat_interval, 0.5)

            if args.duration > 0 and now - started_at >= args.duration:
                print("[phone] 已达到设定运行时长，准备退出")
                break

            try:
                raw = client.recv_text()
            except socket.timeout:
                continue
            message = codec.decode(raw)
            print(f"[phone] 收到: name={message.name}")
            if message.name == "device.binded":
                print(
                    "[phone] 当前绑定关系: "
                    f"glass_device_id={message.payload.get('glass_device_id')} "
                    f"phone_device_id={message.payload.get('phone_device_id')}"
                )
    except KeyboardInterrupt:
        print("[phone] 收到中断，手机端退出")
    finally:
        client.close()
        camera_sink_server.stop()


if __name__ == "__main__":
    main()
