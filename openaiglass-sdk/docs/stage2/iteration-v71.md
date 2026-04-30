# SDK 迭代记录：工具前置播报随机候选

对应对外 SDK 版本：`sdk-v71`。

## 背景

`sdk-v70` 已经把工具前置播报做成本地静态音频缓存，但每个工具仍然只有一条固定提示语。真实语音交互中，固定句子高频重复会显得机械；业务 Tool 更适合声明 3 到 5 条口语化候选，由 SDK 在每次调用前随机选择。

## 本轮变更

1. `ToolSpec.progress_message` 从单字符串扩展为 `str | list[str] | None`，旧单句写法继续兼容。
2. 公开 SDK `BaseTool.progress_message` 同步支持字符串列表。
3. `AgentToolContext.announce_tool_progress()` 会规范化候选文案，并在工具执行前随机选择一条播报。
4. 同一轮同一工具仍然只播报一次，避免工具循环或重试导致重复提示。
5. `ToolRegistry.list_progress_messages()` 会展开所有候选文案，供启动阶段静态音频缓存预生成。
6. SDK 内置设备状态、任务状态、取消任务、抓拍、手机视频连接、Skill 读取和长期记忆工具改为 3 条默认候选。
7. 更新 `普通文本流式与TTS首包延迟优化设计.md`、`SDK安装与能力开发指南.md` 和 `sdk-version`。

## 业务开发边界

业务 Tool 仍然只声明 `progress_message`，不要自行随机、调用 TTS、管理音频文件或写播放控制消息。建议候选句保持短、口语化、不中断用户理解，不要包含工具执行后的最终结论。

## 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
3. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q`

本轮未执行设备级回放。后续真实链路验证时应重点观察同一工具多次调用时前置播报是否来自候选集合，以及 `工具前置播报音频缓存预加载完成` 中的缓存数量是否覆盖所有候选句。
