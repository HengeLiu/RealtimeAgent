"""MediaFrame 编解码测试。"""

from __future__ import annotations

import unittest

from infra.errors import AppError, ErrorCode
from protocol.media import MediaFrame


class MediaFrameCodecTestCase(unittest.TestCase):
    """媒体帧编解码测试类。"""

    def test_round_trip_success(self) -> None:
        """测试目标：验证媒体帧往返编解码一致。

        测试方法：
        1. 构造合法帧头与负载。
        2. 编码后再解码。

        预期结果：
        1. 解码结果中的帧头与负载保持一致。
        """

        payload = b"12345678"
        frame = MediaFrame(
            header={
                "version": "v1",
                "stream_id": "stream-001",
                "frame_type": "audio_chunk",
                "seq": 1,
                "ts_ms": 1744262400000,
                "codec": "pcm16le",
                "payload_size": len(payload),
                "final": False,
            },
            payload=payload,
        )
        raw = frame.encode()
        restored = MediaFrame.decode(raw)

        self.assertEqual(restored.header["stream_id"], "stream-001")
        self.assertEqual(restored.payload, payload)

    def test_invalid_payload_size_raises(self) -> None:
        """测试目标：验证负载长度不一致会被拦截。

        测试方法：
        1. 构造 `payload_size` 与真实长度不一致的对象。
        2. 执行编码并捕获异常。

        预期结果：
        1. 抛出 `AppError(INVALID_MESSAGE)`。
        """

        frame = MediaFrame(
            header={
                "version": "v1",
                "stream_id": "stream-001",
                "frame_type": "audio_chunk",
                "seq": 1,
                "ts_ms": 1744262400000,
                "codec": "pcm16le",
                "payload_size": 999,
                "final": False,
            },
            payload=b"123",
        )

        with self.assertRaises(AppError) as ctx:
            frame.encode()

        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_MESSAGE)


if __name__ == "__main__":
    unittest.main()
