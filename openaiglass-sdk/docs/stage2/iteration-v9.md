# iteration-v9：最小 Skill Runtime

对应 SDK 版本：sdk-v10

## 背景

前几轮 SDK 已经提供 Tool、Task、MCP、设备组、通知和多设备组织能力。复合业务仍缺少一个“告诉模型如何组合这些能力”的正式扩展面，因此本轮补齐最小 Skill Runtime。

## 本轮改动

1. `SkillManifest` 增加 `allowed_tools` 和 `allowed_mcp_methods`。
2. 新增 `SkillRuntime`，维护 Skill 注册、会话 active Skill、prompt 片段和运行态快照。
3. 新增内置 `read_skill` Tool，读取 Skill 正文后激活当前会话 Skill。
4. `ToolRegistry` 支持 Skill Runtime 注入和按会话过滤模型可见工具。
5. `ToolGateway` 在执行前校验当前会话 Skill 工具白名单。
6. `OpenAIAgentLoopRunner` 注入 Skill 摘要或 active Skill 正文，并在 `model_request` 中记录 active Skill 和工具白名单。
7. `OpenAIGlassesSDK` 增加 `register_skill` 和 `register_skill_manifest`。
8. 控制运行态快照增加 `skills` 节点。
9. 更新开发指南、支持情况说明和 Skill Runtime 设计文档。

## 验收

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_agent_core.py openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q
```

覆盖点：

1. `read_skill` 可读取 Skill 并激活当前会话。
2. active Skill 正文进入 system prompt。
3. active Skill 工具白名单会过滤模型可见工具。
4. `ToolGateway` 会拒绝白名单外工具调用。
5. SDK 注册的 Skill 可注入基于 SDK 构建的 `AgentFacade`。

## 后续边界

本轮不是远程 Skill 平台。目录扫描、审批、风险等级、远程注册、复杂会话恢复和多 Skill 冲突策略后续再做。
