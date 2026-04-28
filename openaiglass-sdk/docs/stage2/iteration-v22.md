# iteration-v22：SDK v23 服务端配置收口

## 本轮目标

根据 2026-04-28 的 SDK 使用反馈，把服务端模型、ASR、TTS、系统提示词和音频上限等运行时配置显式暴露到 `openaiglass-for-blind/config/local_server.env`，避免业务开发者改代码或依赖临时 shell 环境变量。

本轮对应对外 SDK 版本：`sdk-v23`。

## 主要改动

1. `local_server.env.example` 和当前 `local_server.env` 显式列出 `DASHSCOPE_API_KEY`、`VOICE_MODEL_BASE_URL`、`VOICE_ASR_MODEL_NAME`、`AGENT_MODEL_NAME`、`VOICE_MODEL_NAME`、`VOICE_MODEL_VOICE`、`TTS_MODEL_NAME`、`TTS_VOICE`、`TTS_WEBSOCKET_API_URL`、`TTS_SAMPLE_RATE_HZ`、`VOICE_MODEL_TIMEOUT_MS`、`VOICE_SYSTEM_PROMPT` 和 `MAX_SEGMENT_AUDIO_BYTES`。
2. 服务端 CLI 默认环境补齐上述配置项，保持本地启动、远程启动和 `ServerSettings.from_env()` 使用同一组环境变量。
3. 远程启动环境导出不再只透传少量白名单变量，而是透传 SDK 服务端默认配置集合和必要的地址派生变量。
4. 增加 CLI 单元测试，确认 `local_server.env` 中的模型配置会进入服务端子进程环境。

## `ServerSettings` 的职责

`ServerSettings` 不只是配置值容器。它是服务端运行时的类型化配置边界，负责：

1. 从环境变量读取 SDK 运行时配置。
2. 对端口、日志级别、心跳、模型名、TTS、语音会话模式和音频上限做启动前校验。
3. 为 HTTP 健康检查、运行时摘要和日志输出提供脱敏后的配置摘要。
4. 为 `ControlRuntime`、`VoiceRuntime`、`AgentFacade`、MCP 和设备组运行时提供一致配置对象。

## 当前边界

1. 后台启动时 CLI 仍会把子进程 `LOG_FILE` 置空，因为 stdout/stderr 已经重定向到启动器日志文件，避免同一条日志写两次。
2. `DASHSCOPE_API_KEY=""` 会覆盖 shell 中已有的同名变量；本地联调应把真实 key 写入 `local_server.env` 或删除该行后改用 shell 环境。
3. 模型供应商仍按当前 DashScope/OpenAI-compatible 接口适配，其他供应商需要后续通过模型 Adapter 扩展。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_server_cli.py \
  openaiglass-sdk/tests/unit/test_settings.py -q
```
