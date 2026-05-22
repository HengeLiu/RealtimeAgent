# 大模型接入测试

本目录是 L2 真实模型 provider 测试入口。测试依赖 API Key、网络、额度和 provider 服务状态，不作为默认本地快速回归的稳定前提。

| 文件 | 测试目标和范围 |
| --- | --- |
| `test_dashscope_providers.py` | 验证 DashScope ASR、TTS、Vision、Vision tool calling 和 Qwen Omni Realtime smoke，并写出 provider artifact。 |
| `artifacts.py` | L2 测试专用 artifact 辅助，负责写 `result.json` 和 WAV。 |

回归命令：

```bash
uv run python -m pytest agent-server/model-provider-tests -q
uv run python -m pytest -m model_provider -q
```

新增用例规则：

- 必须使用真实 provider，禁止 mock fallback。
- API Key 或 SDK 依赖缺失时用 `skipif` 明确跳过原因。
- 失败 artifact 中应记录 provider、model、endpoint、timeout、fallback policy 和错误信息。
- 有音频输出时写出 WAV，便于复查。
