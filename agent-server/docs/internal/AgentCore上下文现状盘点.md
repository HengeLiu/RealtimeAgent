# Agent Core 大模型上下文现状盘点

本文记录当前 `realtime-agent` 中所有会进入大模型视野的内容来源，作为后续上下文管理重构的基线。

当前核对对象是 `examples/simple-agent-server/server.yaml` 默认配置：`agent.mode=omni`、`memory.enabled=true`、`tools.denylist=[capture_photo, interpret_image, interpret_current_view]`。

## 主链路上下文入口

| 类别 | 来源 | 进入模型的位置 | 当前默认是否生效 | 模型可见内容 | 备注 |
| --- | --- | --- | --- | --- | --- |
| Omni 主系统提示 | `examples/simple-agent-server/server.yaml` 的 `agent.omni.prompt` | `RealtimeAgentConfig.from_yaml()` 写入 `omni_prompt`，再传入 `RealtimeProviderConfig.prompt` | 是，默认主链路 | 助手名称“乐鑫”、盲人眼镜身份、视觉问题处理规则、找物任务规则、禁止朗读工具名/参数/JSON、非视觉问题不要描述图片 | 这是当前最重要的主 Agent 指令。 |
| Vision 主系统提示 | `server.yaml` 的 `agent.vision.prompt` | `VisionModelProviderConfig.prompt`，最终作为 Chat Completions system message | 配置存在；默认不走 vision 模式 | 助手名称、盲人眼镜身份、找物任务规则、简短中文回答、必要时调用工具 | vision 模式启用时生效。 |
| 默认系统提示 | `RealtimeAgentConfig.vision_prompt` / `omni_prompt` 默认值 | 未配置 YAML 时作为 fallback | 默认被示例配置覆盖 | “你是中文语音助手。请用简短口语回答用户。” | SDK 默认值，不包含业务语义。 |
| Memory 使用规则 | `realtime_agent.app.MEMORY_AGENT_INSTRUCTIONS`，通过 `_with_memory_instructions()` 追加 | `from_yaml()` 和直接构造配置时追加到 vision/omni prompt | 是，因 `memory.enabled=true` | 长期记忆规则、何时调用 `memory_search` / `manage_memory`、不要保存敏感信息等 | 追加规则使用字符串包含“长期记忆规则”避免重复追加。 |
| Omni 工具调用语音规则 | `REALTIME_TOOL_CALL_PROMPT_RULE` | `OmniRealtimeAgentCore._build_prompt()` 追加到 Omni instructions | 是 | 需要工具/任务时直接调用工具；工具结果前不要先播报；不要朗读工具名、参数、JSON、schema | 当前只是 prompt 约束，不是服务端确定性音频 gate。 |
| 长期记忆片段 | `MemoryService.build_prompt_fragment()` | `VisionRealtimeAgentCore._build_prompt()` / `OmniRealtimeAgentCore._build_prompt()` 追加到 system prompt | 有记忆时生效 | “以下是已保存的用户信息...” + basic / personalized 记忆条目 | 每类最多 6 条；personalized 仍建议必要时调用 `memory_search` 精查。 |
| 更早历史摘要片段 | `ConversationMemory.build_summary_prompt_fragment()` | vision/omni prompt 末尾追加 | 有压缩摘要时生效 | “以下是更早历史对话的压缩摘要，回答时应保持一致：...” | active messages 超阈值压缩后才出现。 |
| Active 历史消息 | `messages.jsonl` 中筛选后的 user/assistant 文本 | Vision: `_build_runtime_messages()`；Realtime: `_load_runtime_messages()` 的等价请求视图 | 有历史时生效 | 最近 user/assistant 文本消息 | tool 消息不作为孤立历史回灌，只用于审计和当前工具循环内回填。 |
| 当前用户输入 | Vision ASR final_text / Omni PCM stream | Vision 作为最后一条 user message；Omni 在 `model-request.json` 中表达为 `input_audio_stream`，真实 provider 收 PCM | 是 | Vision 看见 ASR 文本；Omni 接收音频流 | Realtime 的 `input_audio_stream` 是排障等价视图，不是实际 Chat Completions payload。 |
| Tool schema | `ToolGateway.provider_schemas()` | Vision 模型请求 `tools`；Omni `session.update.tools` | 是 | 工具名、description、Pydantic 字段 JSON schema | 只暴露通过 allowlist/denylist/Skill policy 的工具。 |
| Tool 调用结果回填 | Vision `_provider_tool_result_message()`；Realtime `_submit_tool_result()` | Vision 当前工具循环内作为 `role=tool`；Omni 创建 `function_call_output` | 工具被调用后生效 | 结构化 `ToolResult`：ok/data/message/assets/artifacts/tasks/meta/error | Vision 下一轮模型能直接读到；Realtime 由 provider tool result injection 继续生成。 |
| Task 启动结果指令 | `TaskRunResult.instructions` | 作为 Task 工具结果的一部分返回给主 Agent | 任务启动后生效 | 例如“请只告诉用户已经开始寻找...不要说已经找到...” | 依赖模型遵守工具结果内容。 |
| Omni tool follow-up instructions | `_tool_result_followup_instructions()` / `_capture_photo_response_instructions()` | Qwen Omni `create_response(instructions=...)` 或图片追加后 provider 自动响应 | 工具结果后生效 | 工具失败事实、禁止声称成功；capture_photo 后只基于新照片回答等 | 这里包含 `capture_photo` 具名特例。 |
| 视觉解读子 Agent prompt | `_interpret_asset_with_vision_model()` | 图片解读 Tool 内部调用 OpenAI-compatible Chat Completions | 只有 `interpret_image` / `interpret_current_view` 被调用时生效；当前默认 denylist 不暴露给主 Agent | “你是盲人眼镜的视觉解读助手。只基于图片回答...” + 用户问题 + image_url | 这是 Tool 内部子模型上下文，不是主 Agent 上下文。 |
| 会话摘要子 Agent prompt | `_message_summary_prompt()` | `LlmMessageSummarizer` 调用摘要模型 | 触发历史压缩时生效 | 只输出中文结构化摘要，不输出 JSON，不解释过程；固定标题 | 生成结果未来会进入主 Agent system prompt。 |
| 记忆管理子 Agent prompt | `_memory_manager_prompt()` | `MemoryManagementAgent.plan()` 调用模型 | `manage_memory` 被调用时生效 | 只输出 JSON；决定 add/update/delete；不要保存敏感信息 | 生成结果用于落盘长期记忆，未来可能再注入主 Agent。 |
| Skill 文档 | `SkillService.read_skill()` | 只有模型调用 `read_skill` Tool 后进入工具结果 | 当前 `skill.enabled=false`，默认不可用 | Skill 的 name/description/content/tool_allowlist/prompt_snippets/metadata | `prompt_snippets` 当前不会自动注入主 prompt。 |
| MCP 工具信息 | `mcp_call` Tool schema 与 MCP 配置中的 tool_name/description/parameters | 主 Agent 只看见 `mcp_call` schema；具体 MCP tool 名通过 `tool_name` 参数传入 | 当前 `mcp.enabled=false`，默认不可用 | `mcp_call` 的泛化描述；若业务工具内部调用 MCP，结果由 ToolResult 暴露 | 当前主 Agent 不直接获得 MCP tool 列表。 |

## 当前默认模型可见工具

通过当前 `server.yaml` 构造 `RealtimeAgentApp` 后，`ToolGateway.provider_schemas()` 实际暴露 9 个工具。

| 工具名 | 来源 | 模型可见说明摘要 | 模型可见字段 | 备注 |
| --- | --- | --- | --- | --- |
| `query_device_state` | SDK 内置 | 查询当前用户在线设备、设备名称、能力、连接状态或播放状态 | `include_properties` | 低风险，偏调试/状态查询。 |
| `task_runtime_manager` | SDK 内置 | 查询、取消、列出 Task；启动任务必须调用具体 `start_*` Tool | `action`, `task_id`, `include_terminal` | 和 TaskStartTool 配套。 |
| `close_audio_session` | SDK 内置 | 用户明确要求结束语音会话时关闭当前连续对话 | `reason`, `user_close_phrase` | 必须提供用户原话中的明确关闭短语。 |
| `search_web` | SDK 内置 | 使用 Bocha 查询公开网页资料 | `query`, `limit`, `freshness`, `summary`, `timeout_seconds` | 依赖 `BOCHA_SEARCH_API_KEY`，未配置时返回 fallback。 |
| `query_route_plan` | SDK 内置 | 通过 AMap MCP 查询路线，目的地不明确先确认 | `destination`, `origin`, `timeout_seconds` | 优先使用应用 MCP；未配置时读取 `AMAP_MCP_*` 环境变量。 |
| `memory_search` | SDK 内置 | 读取已保存长期记忆详情；不用于维护记忆 | `topic`, `topics` | `memory.enabled=false` 时可见但返回权限错误。 |
| `manage_memory` | SDK 内置 | 用户要求记住、更新、忘记、删除，或自然提供值得保存的信息时调用 | `memory_context` | `memory.enabled=true` 时会触发记忆管理子 Agent。 |
| `search_conversation_history` | SDK 内置 | 检索 runs 中的历史对话记录 | `query`, `session_id`, `limit` | 只读扫描当前用户 `messages.jsonl`。 |
| `start_timer_task` | TaskStartTool 自动生成 | 启动计时器后台任务，到点通过 speaker 播报提醒 | `seconds`, `message`, `auto_fire` | `auto_fire` 字段当前模型可见。 |

## 当前存在但默认不可见的工具提示词

| 工具名 | 来源 | 为什么不可见 | 仍可能何时进入模型 |
| --- | --- | --- | --- |
| `capture_photo` | external-business-app Tool | `server.yaml` tools.denylist 显式禁用；Realtime 也会过滤 inline vision tools | 如果配置移除 denylist，Vision 链路可见；Realtime 仍被 `REALTIME_INLINE_VISION_TOOLS` 过滤。 |
| `interpret_image` | external-business-app Tool | 同上 | 如果配置移除 denylist，Vision 链路可见；Tool 内部会调用视觉子 Agent。 |
| `interpret_current_view` | external-business-app Tool | 同上 | 如果配置移除 denylist，Vision 链路可见；Tool 内部会调用视觉子 Agent。 |
| `read_skill` | SDK 内置 | 当前 `skill.enabled=false` 且 Skill policy 会限制工具可见性 | 启用 Skill 并允许工具策略后可见。 |
| `mcp_call` | SDK 内置 | 当前 `mcp.enabled=false` 或策略限制 | 启用 MCP 后可见。 |

## Message 拼接规则

| 链路 | system | active history | 当前输入 | tool message | 记录位置 |
| --- | --- | --- | --- | --- | --- |
| VisionRealtimeAgentCore | `vision_prompt + memory instructions + memory fragment + summary fragment` | 最近 `max_context_messages` 条 `user/assistant` 文本 | ASR final_text 作为最后 user message | 当前工具循环内追加 assistant tool_calls 和 tool result；历史 tool 消息不回灌 | `model-request.json` 记录完整等价请求 |
| OmniRealtimeAgentCore | `omni_prompt + memory instructions + omni tool rule + memory fragment + summary fragment` | 打开 provider session 时读取 `user/assistant` 文本，写入等价 `model-request.messages` | 实际发送 PCM；排障视图是 `input_audio_stream` | provider function_call_output 注入；同时写 `messages.jsonl` 审计 | `model-request.json` 是等价请求视图，真实 payload 是 Omni session/update/audio |
| 视觉子 Agent | 固定视觉解读 system prompt | 无主 Agent 历史 | `用户问题：...` + image_url | 无 | ToolResult 回到主 Agent |
| 会话摘要子 Agent | 固定摘要 system prompt | previous_summary | archived_messages JSON | 无 | 摘要文本落盘，未来注入主 Agent |
| 记忆管理子 Agent | 固定记忆管理 system prompt | existing_memories JSON | memory_context JSON | 无 | 动作计划执行后写 memory store，未来注入主 Agent |

## 主要风险点

| 风险 | 位置 | 影响 |
| --- | --- | --- |
| 主 Agent prompt、Memory 规则、Realtime 工具规则分散拼接 | `app.py`、`vision.py`、`omni.py`、`memory/__init__.py` | 后续优化难以确认最终 system prompt 内容，容易重复或冲突。 |
| Omni 对工具前音频主要靠 prompt 约束 | `REALTIME_TOOL_CALL_PROMPT_RULE` | provider 仍可能先吐 audio delta；需要服务端确定性 gate 才能稳定验收。 |
| Omni 中存在具名视觉工具过滤和 `capture_photo` follow-up 特例 | `REALTIME_INLINE_VISION_TOOLS`、`_capture_photo_response_instructions()` | SDK/core 对业务工具名有耦合，后续应改成配置或 hook。 |
| TaskStartTool description 自动拼接很长 | `_task_start_tool_description()` | 每个 Task 工具都重复一段通用规则，工具 schema 变长且可能挤压关键业务描述。 |
| 部分 Task description 含实现细节 | `TrafficLightTask.description` | “YOLO mock”等内部实现可能干扰模型理解真实能力。 |
| 视觉工具代码存在但默认不可见 | `server.yaml` denylist + Omni inline filter | 排查时容易误以为模型能调用图片工具，必须看实际 provider schema。 |
| Tool progress message 不是 provider schema，但会产生用户可听输出 | `ToolSpec.progress_message` / `emit_progress_once()` | 上下文重构时需要和播放仲裁一起看，避免工具前播报和模型音频冲突。 |
