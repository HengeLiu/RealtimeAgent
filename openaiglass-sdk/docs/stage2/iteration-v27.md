# iteration-v27：SDK v28 Agent 运行热路径拆薄

## 本轮目标

降低语音问答链路中 `AgentFacade.handle_turn(...)` 之后的运行时热路径复杂度，避免单轮 Agent 执行中混杂依赖导入、provider 创建、上下文装配、流式事件观察和拍照续跑逻辑。

本轮对应对外 SDK 版本：`sdk-v28`。

## 主要改动

1. 将 `OpenAIAgentLoopRunner.run_turn(...)` 中的单轮上下文装配拆到 `AgentTurnRuntimeFactory`。
2. 将 OpenAI Agents SDK 的导入、`MultiProvider` 缓存、`RunConfig` 创建和 `Runner` 调用收敛到 `OpenAIAgentsSdkBridge`。
3. 将流式文本增量、`capture_photo` 进度播报、抓拍图片等待和图片续跑观察逻辑拆到 `StreamedAgentTurnObserver`。
4. 新增 `OpenAIAgentLoopRunner.preload_resources()`，真实服务端通过 `build_agent_facade_from_sdk(...)` 和 `build_default_agent_facade(...)` 构建时会主动预热 Agents SDK 模块和 provider。
5. 保持单测和业务宿主可替换性：通用 runner 构造函数不强制预热，便于测试注入 fake Agents SDK 或宿主自行控制预热时机。

## 延迟边界

1. 预热阶段只做依赖入口和 provider 级资源准备，不创建每轮会话上下文。
2. 单轮热路径仍必须动态读取 active Skill、工具白名单、历史消息和设备上下文，因为这些数据随会话变化。
3. `first_token_latency_ms` 口径不变：从 ASR 完成准备进入 `AgentFacade.handle_turn(...)` 前开始，到首个模型文本增量到达 `VoiceRuntime` 为止。

## 当前边界

1. `Agent` 和 `RunConfig` 仍按轮创建，因为 system prompt、工具列表和 group_id 都可能随会话变化。
2. 拍照后的多模态图片续跑仍走当前 SDK 主链路兼容实现，后续可继续收敛成标准 Tool result + Agent loop。
3. 本轮不引入业务能力代码，不修改 `openaiglass-for-blind/capabilities`。

## 验证结果

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py -q
```

结果：24 passed。

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py \
  openaiglass-sdk/tests/unit/test_sdk_phase_two.py \
  openaiglass-sdk/tests/unit/test_server_cli.py \
  openaiglass-sdk/tests/unit/test_settings.py -q
```

结果：68 passed。

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run python -m compileall -q \
  openaiglass-sdk/server-python/agent_core/runtime/runner.py \
  openaiglass-sdk/server-python/openaiglasses/server.py
```

结果：通过。
