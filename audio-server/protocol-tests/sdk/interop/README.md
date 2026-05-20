# interop 测试

本目录覆盖 Server SDK 与 Device SDK 的真实传输互操作，不接真实模型和真实硬件。

| 文件 | 测试目标和范围 |
| --- | --- |
| `test_server_device_loopback.py` | 启动真实 HTTP/WebSocket server，并用 Python Device SDK 完成注册、RGB stream、command 和 output stream 闭环。 |
