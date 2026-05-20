# model_provider 测试

本目录是 L2 真实模型 provider 测试入口。测试依赖 API Key、网络、额度和 provider 服务状态，不作为默认本地快速回归的稳定前提。

| 文件 | 测试目标和范围 |
| --- | --- |
| `test_dashscope_providers.py` | 验证 DashScope ASR、TTS、Vision、Vision tool calling 和 Qwen Omni Realtime smoke，并写出 provider artifact。 |
| `artifacts.py` | L2 测试专用 artifact 辅助，负责写 `result.json` 和 WAV。 |
