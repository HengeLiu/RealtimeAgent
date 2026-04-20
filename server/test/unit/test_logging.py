"""日志脱敏单元测试。"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest

from infra.logging.logger import NOISY_LIBRARY_LOG_LEVELS, JsonFormatter, configure_root_logger, sanitize_log_message


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

    def test_configure_root_logger_writes_json_log_file(self) -> None:
        """测试目标：验证根日志器可同时写入标准输出和日志文件。

        测试方法：
        1. 创建临时日志文件路径。
        2. 调用 `configure_root_logger` 打开文件处理器。
        3. 通过根日志器写入一条日志并读取文件内容。

        预期结果：
        1. 日志文件会被自动创建。
        2. 文件中的内容仍是 JSON 单行格式。
        3. `message` 字段与实际输出一致。
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = os.path.join(temp_dir, "nested", "server.log")

            configure_root_logger("DEBUG", log_file)
            logging.getLogger("test.file").info("写入文件日志")
            logging.shutdown()

            with open(log_file, "r", encoding="utf-8") as handle:
                lines = [line.strip() for line in handle.readlines() if line.strip()]

        self.assertGreaterEqual(len(lines), 1)
        payload = json.loads(lines[-1])
        self.assertEqual(payload["logger"], "test.file")
        self.assertEqual(payload["message"], "写入文件日志")
        logging.getLogger().handlers.clear()

    def test_configure_root_logger_lowers_noisy_library_levels(self) -> None:
        """测试目标：验证高频第三方日志会被统一收敛。

        测试方法：
        1. 调用 `configure_root_logger` 完成全局日志初始化。
        2. 逐个检查约定的第三方日志器级别。

        预期结果：
        1. 高频第三方日志器级别不低于 `WARNING`。
        """

        configure_root_logger("DEBUG")

        for logger_name, level in NOISY_LIBRARY_LOG_LEVELS:
            self.assertEqual(logging.getLogger(logger_name).level, level)

        logging.getLogger().handlers.clear()


if __name__ == "__main__":
    unittest.main()
