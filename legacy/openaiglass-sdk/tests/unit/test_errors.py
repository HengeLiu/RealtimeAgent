"""错误模型测试。"""

from __future__ import annotations

import unittest

from infra.errors import ErrorCode, build_error


class ErrorModelTestCase(unittest.TestCase):
    """错误模型测试类。

    主要功能：
    1. 验证统一错误结构的输出字段完整性。
    """

    def test_error_to_dict(self) -> None:
        """测试目标：验证错误对象可以转为标准字典。

        测试方法：
        1. 构造错误对象。
        2. 调用 `to_dict`。

        预期结果：
        1. 返回结果包含 `code/message/retryable/details`。
        """

        error = build_error(
            ErrorCode.TIMEOUT,
            "模型调用超时",
            retryable=True,
            details={"timeout_ms": 10000},
        )
        payload = error.to_dict()

        self.assertEqual(payload["code"], "TIMEOUT")
        self.assertEqual(payload["message"], "模型调用超时")
        self.assertTrue(payload["retryable"])
        self.assertEqual(payload["details"]["timeout_ms"], 10000)


if __name__ == "__main__":
    unittest.main()
