"""配置模块测试。"""

from __future__ import annotations

import os
import unittest

from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode


class ServerSettingsTestCase(unittest.TestCase):
    """`ServerSettings` 测试类。

    主要功能：
    1. 验证配置读取、校验、映射解析的关键行为。

    主要方法：
    1. `test_from_env_success`：验证正常读取。
    2. `test_invalid_port_raises`：验证非法端口。
    3. `test_parse_device_token_map_success`：验证配对映射解析。

    主要属性：
    1. `os.environ`：测试中用于注入环境变量。
    """

    def setUp(self) -> None:
        """测试前准备。

        测试目标：
        1. 保存原环境变量，避免污染外部环境。

        测试方法：
        1. 使用 `dict(os.environ)` 复制当前环境。

        预期结果：
        1. 每个用例都能在干净环境中执行。
        """

        self._old_env = dict(os.environ)

    def tearDown(self) -> None:
        """测试后清理。

        测试目标：
        1. 恢复环境变量。

        测试方法：
        1. 清空后回写备份。

        预期结果：
        1. 不影响后续测试或本地环境。
        """

        os.environ.clear()
        os.environ.update(self._old_env)

    def test_from_env_success(self) -> None:
        """测试目标：验证环境变量可被正确读取并生成配置。

        测试方法：
        1. 注入合法环境变量。
        2. 调用 `ServerSettings.from_env()`。
        3. 断言关键字段值。

        预期结果：
        1. 返回配置对象且字段与输入一致。
        """

        os.environ["SERVER_HOST"] = "127.0.0.1"
        os.environ["SERVER_PORT"] = "9001"
        os.environ["LOG_LEVEL"] = "debug"
        settings = ServerSettings.from_env()

        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 9001)
        self.assertEqual(settings.log_level, "DEBUG")

    def test_invalid_port_raises(self) -> None:
        """测试目标：验证非法端口会触发结构化配置错误。

        测试方法：
        1. 注入非数字端口。
        2. 调用 `from_env` 并捕获异常。

        预期结果：
        1. 抛出 `AppError`，错误码为 `INVALID_CONFIG`。
        """

        os.environ["SERVER_PORT"] = "not-int"

        with self.assertRaises(AppError) as ctx:
            ServerSettings.from_env()

        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_CONFIG)

    def test_parse_device_token_map_success(self) -> None:
        """测试目标：验证设备令牌映射可被解析。

        测试方法：
        1. 直接构造配置对象并写入映射字符串。
        2. 调用 `parse_device_token_map`。

        预期结果：
        1. 返回正确的 `device_id -> token` 字典。
        """

        settings = ServerSettings(device_token_map="glass-001=token-a,glass-002=token-b")
        token_map = settings.parse_device_token_map()

        self.assertEqual(token_map["glass-001"], "token-a")
        self.assertEqual(token_map["glass-002"], "token-b")


if __name__ == "__main__":
    unittest.main()
