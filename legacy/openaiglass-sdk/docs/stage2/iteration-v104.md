# sdk-v104 Omni 工具桥与 Text Agent Adapter 拆分

更新时间：2026-05-04

## 背景

Omni Realtime 工具调用和 Text 链路的 Agent Turn 组装原先仍直接嵌在 `VoiceRuntime` 或 Omni Realtime 客户端回调里。随着 `voice.server_mode=omni_server|text_server` 的边界逐步成型，这两段逻辑需要归入各自模态模块，避免后续继续把新能力写回 `voice_runtime.py`。

本轮继续推进 Omni Server / Text Server 拆分：

1. Omni Realtime function calling 执行与结果回填迁入 `runtime.omni.tool_bridge`。
2. Text ASR 链路进入 Agent Core 前的 `AgentTurn` 构造迁入 `runtime.text.text_agent_adapter`。
3. 增加 import 边界测试，防止 Omni 和 Text 模块互相依赖。

## 变更

1. 新增 `runtime/omni/tool_bridge.py`。
   - `OmniToolBridge` 负责调用 SDK Tool 网关、回填 `function_call_output`、处理 `capture_photo` 图片追加，并在工具完成后继续创建文本和音频响应。
   - `read_capture_photo_tool_image(...)` 单独处理工具结果中的本地图片读取。
2. 新增 `runtime/text/text_agent_adapter.py`。
   - `TextAgentAdapter` 负责保存转写产物，并把语音段音频和用户文本封装为 `AgentTurn`。
   - `VoiceRuntime` 迁移期只保留委托调用，不再内联组装这段 Text Agent 数据结构。
3. `test_voice_server_boundaries.py` 增加 AST import 边界断言。
   - `runtime.omni.tool_bridge` 不得依赖 `runtime.text.*` 或 `runtime.voice_runtime`。
   - `runtime.text.text_agent_adapter` 不得依赖 `runtime.omni.*` 或 `runtime.voice_runtime`。
4. package-check 增加 `runtime.omni.tool_bridge` 和 `runtime.text.text_agent_adapter` 导入覆盖。

## 效果

`voice_runtime.py` 从 3912 行下降到 3874 行。Omni 工具调用和 Text Agent Turn 构造已经拥有独立模块，后续可以继续把会话生命周期、turn recorder 和 Text Server 编排从 `VoiceRuntime` 迁出。

## 验证

已执行：

```bash
uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_voice_server_boundaries.py \
  openaiglass-sdk/tests/unit/test_task_event_runtime.py \
  openaiglass-sdk/tests/unit/test_settings.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q

uv run --python 3.11 --with pytest --with setuptools --with wheel \
  openaiglass.sdk.package-check --repo-root .
```

结果：相关单测 134 条通过，package-check 返回 `ok: true`。

## 对业务开发者的影响

业务代码不需要修改。视觉问答仍由 Omni 模型按需调用 `capture_photo`；Text Server 仍按 `ASR -> TextDialogStateMachine -> Agent -> TTS` 链路运行。业务侧继续通过 `BaseTool`、`BaseTask`、Skill 和 MCP 暴露能力，不需要导入本轮新增的内部适配器。
