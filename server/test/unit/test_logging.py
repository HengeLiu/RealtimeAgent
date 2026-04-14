"""日志脱敏单元测试。"""

from __future__ import annotations

import json
import logging
import unittest

from infra.logging.logger import JsonFormatter, sanitize_log_message


class LoggingTestCase(unittest.TestCase):
    """验证日志消息中的媒体 data URL 会被脱敏。"""

    def test_sanitize_log_message_redacts_audio_data_url(self) -> None:
        """测试目标：验证音频 data URL 会被统一替换。

        测试方法：
        1. 构造包含音频 base64 data URL 的日志消息。
        2. 调用日志脱敏函数。

        预期结果：
        1. 返回值不再包含原始 base64 片段。
        2. 返回值保留 `audio/wav` MIME 类型。
        """

        raw = "request data=data:audio/wav;base64,AAAABBBBCCCC"

        sanitized = sanitize_log_message(raw)

        self.assertEqual(sanitized, "request data=data:audio/wav;base64,<redacted>")

    def test_json_formatter_redacts_image_data_url(self) -> None:
        """测试目标：验证 JSON 格式化输出会对图片 data URL 做脱敏。

        测试方法：
        1. 构造一条带图片 data URL 的 `LogRecord`。
        2. 使用 `JsonFormatter` 格式化。

        预期结果：
        1. `message` 字段保留 MIME 类型。
        2. `message` 字段不包含原始图片 base64。
        """

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.DEBUG,
            pathname=__file__,
            lineno=1,
            msg="payload=%s",
            args=("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA",),
            exc_info=None,
        )

        payload = json.loads(formatter.format(record))

        self.assertEqual(payload["logger"], "test.logger")
        self.assertEqual(payload["message"], "payload=data:image/png;base64,<redacted>")

    def test_sanitize_log_message_redacts_multiline_audio_data_url(self) -> None:
        """测试目标：验证跨行的音频 data URL 也会被完整脱敏。"""

        raw = (
            "Request options: {'data': 'data:audio/wav;base64,AAAABBBB\n"
            "CCCCDDDDEEEE', 'model': 'demo'}"
        )

        sanitized = sanitize_log_message(raw)

        self.assertIn("data:audio/wav;base64,<redacted>", sanitized)
        self.assertNotIn("AAAABBBB", sanitized)
        self.assertNotIn("CCCCDDDDEEEE", sanitized)


if __name__ == "__main__":
    unittest.main()
