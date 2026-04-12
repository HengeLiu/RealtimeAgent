"""服务端基础路由冒烟测试。"""

from __future__ import annotations

import json
import unittest
from urllib.request import urlopen

from api.http_server import build_server_handle
from infra.config import ServerSettings


class HttpSmokeTestCase(unittest.TestCase):
    """服务端冒烟测试类。

    主要功能：
    1. 验证服务端可启动。
    2. 验证基础路由可访问。
    """

    def setUp(self) -> None:
        """测试前准备。

        测试目标：
        1. 启动一个绑定随机端口的服务端实例。

        测试方法：
        1. 端口设置为 0，由系统自动分配。

        预期结果：
        1. 服务成功启动并可接受请求。
        """

        settings = ServerSettings(host="127.0.0.1", port=0)
        self.handle = build_server_handle(settings)
        self.handle.start()

    def tearDown(self) -> None:
        """测试后清理。

        测试目标：
        1. 关闭服务端线程。

        测试方法：
        1. 调用句柄 `stop`。

        预期结果：
        1. 服务停止，端口释放。
        """

        self.handle.stop()

    def test_health_route(self) -> None:
        """测试目标：验证 `/api/health` 可用。

        测试方法：
        1. 发起 HTTP GET。
        2. 解析 JSON。

        预期结果：
        1. `status=ok` 且 `service=server-api`。
        """

        url = f"http://127.0.0.1:{self.handle.port}/api/health"
        with urlopen(url, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["service"], "server-api")


if __name__ == "__main__":
    unittest.main()
