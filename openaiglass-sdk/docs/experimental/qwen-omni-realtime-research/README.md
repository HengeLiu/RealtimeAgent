# Qwen-Omni-Realtime 功能调研代码

本目录用于保存 `qwen3.5-omni-plus-realtime` 的离线调研样例。当前脚本不连接真实百炼服务，不读取 API Key，只验证：

1. `session.update` 中工具定义与 `turn_detection` 配置的事件格式。
2. 使用仓库已有真实 wav 样例时，Realtime 上行音频的分片统计。
3. `response.function_call_arguments.done` 到 `conversation.item.create`、`response.create` 的工具调用闭环。

运行：

```bash
uv run python openaiglass-sdk/docs/experimental/qwen-omni-realtime-research/realtime_tool_probe.py
```

输出文件：

```text
openaiglass-sdk/docs/experimental/qwen-omni-realtime-research/artifacts/probe_result.json
```

真实联调时，需要在这个离线闭环基础上补齐 WebSocket 连接、裸 PCM 分片发送、服务端事件循环和本地工具分发；不要把 API Key 写入代码或样例文件。
