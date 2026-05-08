"""WebSocket 传输层单元测试。"""

from __future__ import annotations

import unittest

from api.ws.websocket_transport import OPCODE_TEXT, _read_message


def _build_masked_frame(*, fin: bool, opcode: int, payload: bytes, mask_key: bytes = b"\x01\x02\x03\x04") -> bytes:
    """构造一段最小客户端 WebSocket 帧。

    主要逻辑：
    1. 按客户端到服务端方向设置 mask 位。
    2. 支持构造分片帧，用于验证 continuation 拼接逻辑。

    参数：
    1. `fin`：当前帧是否结束。
    2. `opcode`：帧操作码。
    3. `payload`：原始负载。
    4. `mask_key`：掩码字节。

    返回值：
    1. 可直接喂给 `_read_message(...)` 的原始帧字节。
    """

    first = opcode | (0x80 if fin else 0x00)
    length = len(payload)
    if length >= 126:
        raise ValueError("测试帧长度过大，请保持在 125 字节以内")
    masked_payload = bytes(value ^ mask_key[index % 4] for index, value in enumerate(payload))
    return bytes([first, 0x80 | length]) + mask_key + masked_payload


class _FakeSocket:
    """用于模拟 socket.recv 的最小假对象。"""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0

    def recv(self, size: int) -> bytes:
        if self._offset >= len(self._payload):
            return b""
        end = min(len(self._payload), self._offset + size)
        chunk = self._payload[self._offset : end]
        self._offset = end
        return chunk

    def settimeout(self, _value: float) -> None:
        return None


class WebSocketTransportTestCase(unittest.TestCase):
    """验证控制 WebSocket 的基础消息拼装。"""

    def test_read_message_returns_single_text_frame(self) -> None:
        """测试目标：验证单帧文本消息可以被完整读取。

        测试方法：
        1. 构造一帧完整的客户端文本帧。
        2. 调用 `_read_message(...)` 读取。

        预期结果：
        1. 返回的 opcode 为文本帧。
        2. payload 与原始文本一致。
        """

        raw = _build_masked_frame(fin=True, opcode=OPCODE_TEXT, payload=b'{"name":"ping"}')
        sock = _FakeSocket(raw)

        opcode, payload = _read_message(sock)  # noqa: SLF001 - 单测覆盖内部实现

        self.assertEqual(opcode, OPCODE_TEXT)
        self.assertEqual(payload, b'{"name":"ping"}')

    def test_read_message_reassembles_fragmented_text_frame(self) -> None:
        """测试目标：验证被分片的文本消息会在服务端重组后再交给上层。

        测试方法：
        1. 把一条 JSON 文本拆成“首帧 + continuation 结束帧”。
        2. 调用 `_read_message(...)`。

        预期结果：
        1. 服务端返回一条完整文本消息。
        2. 不会把半截 JSON 提前交给解码器。
        """

        part_one = _build_masked_frame(fin=False, opcode=OPCODE_TEXT, payload=b'{"name":"sensor.camera.captured",')
        part_two = _build_masked_frame(fin=True, opcode=0x0, payload=b'"payload":{"image_base64":"abc"}}')
        sock = _FakeSocket(part_one + part_two)

        opcode, payload = _read_message(sock)  # noqa: SLF001 - 单测覆盖内部实现

        self.assertEqual(opcode, OPCODE_TEXT)
        self.assertEqual(payload, b'{"name":"sensor.camera.captured","payload":{"image_base64":"abc"}}')


if __name__ == "__main__":
    unittest.main()
