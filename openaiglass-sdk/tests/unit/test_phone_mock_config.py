"""phone-mock 配置解析测试。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
PHONE_MOCK_ROOT = ROOT_DIR / "openaiglass-sdk/phone-mock"
if str(PHONE_MOCK_ROOT) not in sys.path:
    sys.path.insert(0, str(PHONE_MOCK_ROOT))

from openaiglass_phone_mock.config import PhoneMockConfig, derive_http_base_url


class PhoneMockConfigTest(unittest.TestCase):
    """测试 `phone-mock` 配置加载行为。"""

    def test_loads_task_handlers(self) -> None:
        """测试目标：确认任务处理器和事件配置能被加载。

        测试方法：创建临时 JSON 配置并调用 `PhoneMockConfig.load`。
        预期结果：设备编号、配对令牌、任务类型和事件载荷都保持配置值。
        """

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "phone.mock.json"
            config_path.write_text(
                json.dumps(
                    {
                        "device_id": "phone-001",
                        "pair_token": "pair-phone-token",
                        "control_ws_url": "ws://127.0.0.1:8765/ws/control",
                        "task_handlers": {
                            "find_object_phone_task": {
                                "events": [
                                    {
                                        "event_name": "phone.vision.find_object.result",
                                        "payload": {"found": True},
                                        "delay_ms": 20,
                                    }
                                ]
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = PhoneMockConfig.load(config_path, repo_root=ROOT_DIR)

        self.assertEqual(config.device_id, "phone-001")
        self.assertEqual(config.pair_token, "pair-phone-token")
        self.assertTrue(config.camera_sink.enabled)
        self.assertEqual(config.camera_sink.public_host, "127.0.0.1")
        handler = config.task_handlers["find_object_phone_task"]
        self.assertEqual(handler.events[0].event_name, "phone.vision.find_object.result")
        self.assertEqual(handler.events[0].payload["found"], True)
        self.assertEqual(handler.events[0].delay_ms, 20)

    def test_derive_http_base_url(self) -> None:
        """测试目标：确认控制 WebSocket 地址能推导出 HTTP API 根地址。

        测试方法：分别传入 ws 和 wss 地址。
        预期结果：ws 转成 http，wss 转成 https，路径被清空。
        """

        self.assertEqual(derive_http_base_url("ws://127.0.0.1:8765/ws/control"), "http://127.0.0.1:8765")
        self.assertEqual(derive_http_base_url("wss://example.com/ws/control"), "https://example.com")


if __name__ == "__main__":
    unittest.main()
