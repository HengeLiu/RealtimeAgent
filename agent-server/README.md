# agent-server

`agent-server` 是 `realtime-agent` 的 Python Server SDK 和服务端运行时目录。它负责设备注册、控制事件、stream 生命周期、Agent Core、Tool、模型 provider、输出播放仲裁和运行产物记录。

应用开发者通常不需要直接修改这里的核心代码。新的业务能力优先放在应用自己的 `agent-server/capabilities/` 包中，通过 `BaseTool` 和 `ToolContext` 接入。

## 目录结构

```text
agent-server/
  realtime_agent/          # Python SDK 主体，导入名 realtime_agent
    conversation/          # 正式 Omni / VL conversation runtime、Agent Core 和 provider adapter
    agent_core/            # 历史导入兼容 shim
    asset/                 # 图片、音频等资产服务
    audio_pipeline/        # 上行音频规范化和质量诊断入口
    cli/                   # realtime-agent.* 命令入口
    control/               # 设备注册、控制事件和事件路由
    memory/                # 长期记忆和会话摘要
    output/                # 输出服务和播放仲裁
    prompts/               # 内置模型提示词
    spec/                  # 随包协议 schema、AsyncAPI 和错误码
    stream/                # stream 生命周期和 chunk 处理
    context.py             # ToolContext
    tools.py               # BaseTool / ToolResult / ToolSpec
    tool_run.py            # Tool Run 状态机、存储与后台执行器
  config/                  # server 配置样例
  docs/                    # Server SDK 设计、运行产物和 Context API 文档
  unit-tests/              # Server SDK 单元测试
  protocol-tests/          # Server SDK L1 协议行为测试
  model-provider-tests/    # 真实模型 provider 集成测试
```

## 常用入口

- [Server SDK 文档](docs/README.md)
- [Context 与设备 API 设计说明](docs/reference/上下文设备接口设计.md)
- [runs 目录产物说明](docs/how-to/运行产物排查说明.md)
- [通讯协议](../protocol/docs/protocol.md)

## 常用命令

启动当前推荐示例 server：

```bash
uv run realtime-agent.server.run --config examples/simple-agent-server/server.yaml
```

运行 Server SDK 相关测试：

```bash
uv run python -m pytest agent-server/unit-tests agent-server/protocol-tests -q
```

运行真实模型 provider 测试：

```bash
uv run python -m pytest agent-server/model-provider-tests -q
```

真实 provider 测试依赖外部服务和对应 API Key。只验证协议和 SDK 行为时，优先运行 unit-tests / protocol-tests。
