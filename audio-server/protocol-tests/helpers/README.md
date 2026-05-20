# helpers

本目录只放测试辅助代码，不放被 pytest 直接执行的测试用例。

| 文件 | 测试目标和范围 |
| --- | --- |
| `server_sdk_harness.py` | Server SDK 系统级测试 harness，提供 recording endpoint、脚本化 ASR/Vision provider 和设备注册 helper。 |
| `__init__.py` | 让 `helpers.*` 可以在 L1 测试中稳定导入。 |
