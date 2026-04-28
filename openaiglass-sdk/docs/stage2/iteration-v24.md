# iteration-v24：SDK v25 撤销模型硬编码黑名单

## 本轮目标

修正上一轮把 `qwen-turbo`、`qwen-plus`、`qwen-max` 写入 SDK 内置不兼容模型集合的问题。该判断来自联调现象和推断，不应作为 SDK 规则硬编码。

本轮对应对外 SDK 版本：`sdk-v25`。

## 主要改动

1. 移除 `OpenAIAgentLoopRunner` 中的 `incompatible_models` 硬编码集合。
2. 移除对应的 `qwen-turbo` 启动前拦截单元测试。
3. 保留上一轮新增的 Agent 调用前 INFO 日志、agent-core ERROR 日志和流式 Agent 超时保护。
4. 更新开发指南，说明模型兼容性应通过真实错误日志、超时配置和 `model_request` 诊断，不靠 SDK 黑名单。

## 当前边界

1. 当前语音链路仍默认使用流式 Agent，并把 SDK Tools 暴露给模型。
2. 如果某个模型实际不支持当前组合，应由模型接口返回错误或由 SDK 超时保护暴露，而不是预设模型名黑名单。
3. 后续可以增加显式运行模式，例如 `AGENT_RUN_MODE=stream_tools` / `non_stream_tools`，再按模式和模型能力做配置校验。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py -q
```
