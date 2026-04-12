"""ControlMessage 编解码测试。"""

from __future__ import annotations

import unittest

from infra.errors import AppError, ErrorCode
from protocol.codec import JsonMessageCodec
from protocol.messages import Endpoint
from protocol.utils import create_control_message


class ControlMessageCodecTestCase(unittest.TestCase):
    """控制消息编解码测试类。"""

    def setUp(self) -> None:
        """测试前准备。

        测试目标：
        1. 创建编解码器与示例端点。

        测试方法：
        1. 在 `setUp` 中统一初始化。

        预期结果：
        1. 用例代码聚焦断言逻辑。
        """

        self.codec = JsonMessageCodec()
        self.source = Endpoint(device_id="glass-001", device_type="glass", module="glass-api")
        self.target = Endpoint(device_id="server-main", device_type="server", module="server-api")

    def test_round_trip_success(self) -> None:
        """测试目标：验证控制消息往返编解码一致。

        测试方法：
        1. 构造消息对象。
        2. 先编码，再解码。

        预期结果：
        1. 解码后关键字段与原始值一致。
        """

        message = create_control_message(
            semantic="request",
            name="device.register",
            source=self.source,
            target=self.target,
            payload={"device_id": "glass-001"},
            trace_id="trace-001",
        )
        raw = self.codec.encode(message)
        restored = self.codec.decode(raw)

        self.assertEqual(restored.name, "device.register")
        self.assertEqual(restored.semantic, "request")
        self.assertEqual(restored.payload["device_id"], "glass-001")
        self.assertEqual(restored.trace_id, "trace-001")

    def test_invalid_semantic_raises(self) -> None:
        """测试目标：验证非法语义会被拦截。

        测试方法：
        1. 构造非法 `semantic` 消息。
        2. 调用编码逻辑。

        预期结果：
        1. 抛出 `AppError(INVALID_MESSAGE)`。
        """

        message = create_control_message(
            semantic="request",
            name="device.register",
            source=self.source,
            target=self.target,
            payload={},
        )
        message.semantic = "invalid"

        with self.assertRaises(AppError) as ctx:
            self.codec.encode(message)

        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_MESSAGE)


if __name__ == "__main__":
    unittest.main()
