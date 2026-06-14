# Server SDK 协议行为测试

本目录是 L1 Server SDK 系统级集成测试入口。测试重点不是方法级单元行为，而是 Server SDK 在协议事件、stream chunk、fake provider 输出、设备连接和上下文 API 输入下，是否产生符合预期的返回事件、处理流程和运行产物。

## 目录范围

| 目录 | 测试目标 |
| --- | --- |
| `acceptance/` | Server SDK 开发者契约、架构边界、文档约束和系统级验收。 |
| `sdk/conversation/` | conversation runtime、Agent Core、上下文编排、tool bridge、provider fake 和恢复逻辑。 |
| `sdk/runtime/` | control、stream、audio pipeline、output、task、runs、Context API。 |
| `sdk/config/` | Server SDK 配置解析和配置同步。 |
| `sdk/extensions/` | MCP、Skill 等扩展能力。 |
| `sdk/interop/` | Server SDK 与 Device SDK 的真实 WebSocket 闭环。 |
| `helpers/` | L1 测试 harness 和辅助对象，不直接放测试用例。 |

## 回归命令

```bash
uv run python -m pytest agent-server/protocol-tests -q
uv run python -m pytest -m sdk -q
uv run python -m pytest -m interop -q
```

当变更涉及 Device SDK 互认能力时，同时运行：

```bash
uv run python -m pytest devices/python/protocol-tests -q
uv run python -m pytest -m device_sdk -q
```

## 新增测试用例

新增 Server SDK 能力时，优先从以下输入开始写测试：

1. 标准协议事件。
2. `StreamChunk`。
3. fake provider 的 transcript、tool call、delta 或 TTS 音频。
4. WebSocket control / stream 消息。

测试结果应表达：

1. SDK 返回或下发了哪些事件。
2. 是否触发 stream open / close / output / task / tool。
3. Context API 是否按协议抽象调用设备能力。
4. runs artifact 是否写出关键字段。
5. 异常路径是否有明确错误和恢复行为。

不要在 Server SDK L1 测试里硬编码具体应用后台 Tool；应用能力放到 `examples/<app>/app-tests/` 或 `replay-tests/`。
