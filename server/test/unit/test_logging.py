"""日志脱敏单元测试。"""

from __future__ import annotations

import logging
import os
import tempfile
import unittest

from infra.logging.logger import LineFormatter, LogContext, NOISY_LIBRARY_LOG_LEVELS, configure_root_logger, sanitize_log_message


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

    def test_line_formatter_redacts_image_data_url(self) -> None:
        """测试目标：验证单行日志格式化输出会对图片 data URL 做脱敏。

        测试方法：
        1. 构造一条带图片 data URL 的 `LogRecord`。
        2. 使用 `LineFormatter` 格式化。

        预期结果：
        1. 日志行保留 MIME 类型。
        2. 日志行不包含原始图片 base64。
        """

        formatter = LineFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.DEBUG,
            pathname=__file__,
            lineno=1,
            msg="payload=%s",
            args=("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA",),
            exc_info=None,
        )

        line = formatter.format(record)

        self.assertIn("-DEBUG-test.logger---payload=data:image/png;base64,<redacted>", line)
        self.assertNotIn("iVBORw0KGgoAAAANSUhEUgAAAAUA", line)

    def test_line_formatter_keeps_structured_extra_fields(self) -> None:
        """测试目标：验证结构化上下文字段会进入单行日志。

        测试方法：
        1. 构造带有 `LogContext.fields` 的日志记录。
        2. 使用 `LineFormatter` 格式化。

        预期结果：
        1. 常规链路字段存在。
        2. 自定义业务字段也存在。
        """

        formatter = LineFormatter()
        extra = LogContext(
            device_id="glass-001",
            message_id="conn-001",
            fields={
                "connection_id": "conn-001",
                "heartbeat_age_ms": 16000,
                "nested": {"ok": True},
            },
        ).to_dict()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="设备心跳超时，关闭连接",
            args=(),
            exc_info=None,
        )
        for key, value in extra.items():
            setattr(record, key, value)

        line = formatter.format(record)

        self.assertIn("-WARNING-test.logger-conn-001-设备心跳超时，关闭连接", line)
        self.assertIn("device_id=glass-001", line)
        self.assertIn("connection_id=conn-001", line)
        self.assertIn("heartbeat_age_ms=16000", line)
        self.assertIn('nested={"ok":true}', line)

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

    def test_configure_root_logger_writes_line_log_file(self) -> None:
        """测试目标：验证根日志器可同时写入标准输出和日志文件。

        测试方法：
        1. 创建临时日志文件路径。
        2. 调用 `configure_root_logger` 打开文件处理器。
        3. 通过根日志器写入一条日志并读取文件内容。

        预期结果：
        1. 日志文件会被自动创建。
        2. 文件中的内容是单行文本格式。
        3. 日志行包含实际输出内容。
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = os.path.join(temp_dir, "nested", "server.log")

            configure_root_logger("DEBUG", log_file)
            logging.getLogger("test.file").info("写入文件日志")
            logging.shutdown()

            with open(log_file, "r", encoding="utf-8") as handle:
                lines = [line.strip() for line in handle.readlines() if line.strip()]

        self.assertGreaterEqual(len(lines), 1)
        self.assertIn("-INFO-test.file---写入文件日志", lines[-1])
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
