# audio-chat 下一阶段并行开发计划

更新时间：2026-05-07

## 1. 当前基线

上一阶段并行开发已经完成，当前 `audio-chat` 已完成第一批和第二批并行线路，可以进入第三批并行开发。

已确认通过的基线验收：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py all --keep-going \
  --report runs/acceptance/current-full-review.json
```

当前已具备：

1. 协议、事件命名、stream chunk 编码、注册订阅和 filter 匹配。
2. `Device` 运行态对象、`DeviceSnapshot`、active device set、心跳离线标记。
3. `StreamService`、`AssetService`、`AudioPipeline` 最小链路。
4. `TextAgentCore`、`RealtimeAudioAgentCore`、provider adapter、mock provider。
5. `ToolGateway`、`BaseTool`、`BaseTask`、`TaskEngine`、自动发现和 `UserDeviceContext`。
6. `OutputService`、流式 TTS 入口、原生 audio delta 入口、播放仲裁。
7. aiohttp server、Python playback、browser-glass 基础端侧、preflight 和 acceptance 脚本。
8. 开发者可用入口、示例 App、能力回放、下一阶段 lane 注册、Python phone mock 和 browser-glass 最小闭环。
9. 音频会话生命周期、轻量音频处理器链、正式设备注册鉴权、signed_token、Task Engine 生产化、Provider 工具桥和输出实时性。

下一阶段目标先不追求完整复刻老版 SDK，而是把 SDK 从“协议骨架可运行”推进到“功能开发者可用”。这里的可用不是指内部模块存在，而是指开发者能安装 SDK、创建应用目录、编写 Tool / Task、启动 mock 端侧、跑一次回放验收，并能从日志和运行产物判断能力是否真的生效。

## 2. 本阶段主目标：功能开发者可用

老版 SDK 对功能开发者真正有价值的地方，是提供了一条完整开发闭环：

```text
安装 SDK
  -> 同步本地配置
  -> 启动 server
  -> 启动 phone mock / glass playback
  -> 编写 Tool / Task
  -> 自动发现和注册
  -> 跑设备级回放
  -> 查看日志、事件、资产和输出音频
  -> 判断能力是否可用
```

新的 `audio-chat` 不需要复用老版 SDK 的目录、命令和协议细节，但短期必须提供等价的开发体验。本阶段的主验收目标如下：

1. 开发者可以用一组文档命令完成安装、配置、启动和停止，不需要理解内部服务对象。
2. 开发者可以复制示例 app-root，新建一个 Tool 或 Task 后无需修改 `app.py` 即可被自动发现。
3. 开发者可以用 Python phone mock、Python glass playback 或 browser-glass 完成设备级回放。
4. 开发者可以在 Tool / Task 中只通过事件和 stream 使用设备通讯能力，不接触 `device_id` 点对点发送细节。
5. 回放结束后必须产出可检查文件，包括事件、stream、agent、tool、task、asset、output 和最终结果。
6. 文档里标记为“已实现”的开发命令和公开 API 必须有验收脚本覆盖。

本阶段暂不把以下能力作为阻塞项：

1. iOS 端完整 App。
2. ESP32-S3 真机完整固件。
3. 生产级多租户管理后台。
4. 完整 Skill / MCP 生态。
5. 所有老版 SDK 业务能力迁移。

这些能力可以继续并行推进，但不能替代“功能开发者可用闭环”。

## 3. 下一阶段原则

1. 继续保持 server 与 endpoint 边界：server 只接受和下发 stream / event，不录音、不播放。
2. Tool / Task 仍然只能通过 `UserDeviceContext` 使用设备通讯能力，不能按 `device_id` 点对点发送。
3. MCP、Skill、Memory 不允许直接持有 `UserDeviceContext`；需要设备通讯能力时必须封装为 Tool 或 Task。
4. 音频主链路优先级最高，先解决实时性、生命周期、关闭和打断。
5. 端侧参考实现优先顺序：Python playback、browser-glass、Python phone mock、iOS、ESP32-S3。
6. 所有线路必须补自动验收。验收脚本可以先按 lane 增量扩展，但最终必须进入 `acceptance_check.py all`。

## 4. 下一阶段验收脚本扩展

建议先由一名开发人员扩展 `scripts/acceptance_check.py`，新增下一阶段 lane。这个改动很小，但能让后续各组独立验收。

新增 lane 名称：

```text
developer-usability
capability-template-playback
audio-session-lifecycle
auth-device-management
memory-skill-mcp
task-engine-production
provider-output-runtime
endpoint-reference
esp32-s3-endpoint
developer-experience
next-docs-contract
```

建议脚本结构保持上一阶段风格：

```python
CHECKS["audio-session-lifecycle"] = (
    CheckCommand(
        "audio_pipeline_session_tests",
        (
            "uv", "run", "python", "-m", "pytest",
            "tests/test_audio_session_lifecycle.py",
            "tests/test_audio_pipeline_processors.py",
            "-q",
        ),
    ),
)

CHECKS["developer-usability"] = (
    CheckCommand(
        "developer_usability_tests",
        (
            "uv", "run", "python", "-m", "pytest",
            "tests/test_cli_developer_workflow.py",
            "tests/test_config_sync.py",
            "tests/test_docs_commands.py",
            "-q",
        ),
    ),
)

CHECKS["capability-template-playback"] = (
    CheckCommand(
        "capability_template_playback_tests",
        (
            "uv", "run", "python", "-m", "pytest",
            "tests/acceptance/test_capability_template_playback.py",
            "tests/acceptance/test_auto_discovery_developer_contract.py",
            "-q",
        ),
    ),
)

CHECKS["auth-device-management"] = (
    CheckCommand(
        "auth_device_tests",
        (
            "uv", "run", "python", "-m", "pytest",
            "tests/test_device_registration_management.py",
            "tests/test_signed_token_auth.py",
            "-q",
        ),
    ),
)

CHECKS["memory-skill-mcp"] = (
    CheckCommand(
        "memory_skill_mcp_tests",
        (
            "uv", "run", "python", "-m", "pytest",
            "tests/test_memory_service.py",
            "tests/test_skill_service.py",
            "tests/test_mcp_gateway.py",
            "tests/acceptance/test_indirect_device_context_contract.py",
            "-q",
        ),
    ),
)
```

每条线路完成后都必须能单独运行：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py <lane> --report runs/acceptance/<lane>.json
```

最终合并前运行：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py all --keep-going \
  --report runs/acceptance/next-stage-full.json
```

## 5. 前置线路 P0-A：开发者可用验收入口

目标：先冻结“开发者可用”的验收口径，让后续所有并行线路都能围绕同一条闭环交付，而不是只证明内部模块存在。

当前实现状态：

1. `scripts/acceptance_check.py` 已新增 `developer-usability` lane。
2. `pyproject.toml` 已补齐 P0-A 要求的开发者入口命令。
3. README、entry point 和 `audio_chat` 顶层公开 API 已加入自动一致性检查。
4. `app-examples/basic-app` 提供最小 app-root、Tool 样板和 Task 样板，用于冻结开发者闭环的最低门槛。
5. `testdata/contracts/run_artifacts.schema.json` 记录 playback 运行产物的最小 schema。

写入范围：

```text
scripts/acceptance_check.py
tests/test_cli_developer_workflow.py
tests/test_config_sync.py
tests/test_docs_commands.py
tests/acceptance/test_developer_usable_gate.py
docs/next-stage-parallel-development-plan.md
```

任务清单：

1. 新增 `developer-usability` lane。
2. lane 必须覆盖以下命令是否存在、是否能输出帮助信息、是否能在测试目录下生成预期文件：
   - `audio-chat.config.sync`
   - `audio-chat.server.start`
   - `audio-chat.server.stop`
   - `audio-chat.server.logs`
   - `audio-chat.phone.mock`
   - `audio-chat.playback.glass`
   - `audio-chat.dev.preflight`
   - `audio-chat.sdk.package-check`
3. 增加 docs command check：
   - README 中的非 roadmap 命令必须能被测试解析。
   - 文档中出现的 entry point 必须存在于 `pyproject.toml`。
   - 文档中出现的公开类必须能从 `audio_chat` 导入。
4. 增加开发者可用 gate：
   - 没有 app-root 示例时失败。
   - 没有至少一个 Tool 样板和一个 Task 样板时失败。
   - 没有设备级 playback 验收时失败。
   - 没有运行产物 schema 时失败。
5. 旧 lane 继续保留，不允许删除上一阶段验收能力。

验收命令：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py developer-usability \
  --report runs/acceptance/developer-usability.json
```

通过条件：

1. 新增 lane 能独立运行并生成 JSON 报告。
2. 报告中能明确标出开发者闭环缺失项。
3. README、pyproject entry point、公开 API 三者不会互相矛盾。

## 6. 前置线路 P0-B：示例 App 与能力回放闭环

目标：提供一个功能开发者可以复制的最小应用目录，并用自动验收证明“新增能力 -> 自动发现 -> 设备回放 -> 产物可检查”的闭环成立。

写入范围：

```text
app-examples/basic-app/
audio-chat-sdk/audio_chat/cli/
tests/fixtures/basic_app/
tests/acceptance/test_capability_template_playback.py
tests/acceptance/test_auto_discovery_developer_contract.py
README.md
```

建议目录：

```text
app-examples/basic-app/
  README.md
  config/
    server.yaml
    phone.mock.yaml
    glass.playback.yaml
  capabilities/
    capture_photo/
      tool.py
    timer/
      task.py
    continuous_rgb_analyze/
      task.py
  host/
    server/
      main.py
    phone-mock/
      config.yaml
    glass-playback/
      playback.yaml
  testdata/
    audio/
    images/
    streams/
```

任务清单：

1. 示例 Tool：`capture_photo`
   - 只通过控制事件请求设备上传 `sensor.rgb` stream 或资产。
   - 如果资产缓存已有新照片，直接读取资产。
   - 如果没有资产，发送控制事件请求端侧采集。
   - 不允许硬编码 `device_id`。
2. 示例 Task：`timer`
   - 展示后台任务启动、等待、取消、完成和输出通知。
   - 输出必须走 Output Service，不直接写端侧连接。
3. 示例 Task：`continuous_rgb_analyze`
   - 展示持续 sensor stream 场景。
   - Task 发送控制事件请求端侧按频率上传 `sensor.rgb`。
   - Task 从 Asset Service 逐帧读取。
   - cancel 时发送停止上传控制事件。
4. 自动发现：
   - 扫描 app-root 下的 `capabilities/**/tool.py` 和 `capabilities/**/task.py`。
   - 抽象基类和 `_` 开头内部类不注册。
   - 重复名称 fail fast。
5. 回放产物：
   - `runs/audio-chat/sessions/<session_id>/events.jsonl`
   - `runs/audio-chat/sessions/<session_id>/stream-events.jsonl`
   - `runs/audio-chat/sessions/<session_id>/agent-events.jsonl`
   - `runs/audio-chat/sessions/<session_id>/tool-events.jsonl`
   - `runs/audio-chat/sessions/<session_id>/task-events.jsonl`
   - `runs/audio-chat/sessions/<session_id>/assets.jsonl`
   - `runs/audio-chat/sessions/<session_id>/output-decisions.jsonl`
   - `runs/audio-chat/sessions/<session_id>/result.json`

验收命令：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py capability-template-playback \
  --report runs/acceptance/capability-template-playback.json
```

通过条件：

1. 新增 Tool / Task 不修改 server 内部代码即可自动注册。
2. playback 能触发 `capture_photo`、`timer`、`continuous_rgb_analyze` 中至少两个样板。
3. 运行产物可以解释设备注册、订阅匹配、控制事件、stream 上传、资产写入、工具或任务执行、输出仲裁全过程。
4. 失败时报告能定位是注册、订阅、stream、asset、tool、task、agent 还是 output 问题。

## 7. 前置线路 P0-C：下一阶段验收入口

目标：只扩展验收入口，不改业务实现，避免后续多人各自写临时脚本。

写入范围：

```text
scripts/acceptance_check.py
tests/acceptance/test_next_stage_lane_registry.py
docs/next-stage-parallel-development-plan.md
```

任务清单：

1. 增加下一阶段 lane。
2. 每个 lane 初始允许引用尚不存在的测试文件，但 P0 自己要有测试检查 lane 注册完整。
3. `all` 必须包含下一阶段 lane。
4. 报告里保留 `lane`、`command`、`cwd`、`stdout_tail`、`stderr_tail`。
5. 文档中列出的 lane 名称必须和脚本一致。

实现口径：

1. 下一阶段 lane 统一注册在 `NEXT_STAGE_CHECKS`，上一阶段 lane 保留在 `FOUNDATION_CHECKS`。
2. `CHECKS` 是两组注册表的合并结果，因此 `all` 会自然覆盖下一阶段 lane。
3. P0-C 只冻结入口和报告结构，不要求这些 lane 引用的后续测试文件现在就存在。
4. `tests/acceptance/test_next_stage_lane_registry.py` 负责校验文档 lane、脚本注册表和报告字段。

验收命令：

```bash
# 在项目根目录执行
uv run python -m pytest tests/acceptance/test_next_stage_lane_registry.py -q
uv run python scripts/acceptance_check.py p0-foundation
```

通过条件：

1. 旧 lane 不被破坏。
2. 新 lane 注册表存在。
3. 文档和脚本 lane 名称一致。

## 8. 并行线路 A：Audio Pipeline 与会话生命周期

目标：把音频主链路从“格式校验”升级为可真实联调的会话生命周期和轻量预处理链路。

写入范围：

```text
audio-chat-sdk/audio_chat/audio_pipeline/
audio-chat-sdk/audio_chat/app.py
audio-chat-sdk/audio_chat/stream/
audio-chat-sdk/audio_chat/control/
audio-chat-sdk/audio_chat/preflight.py
tests/test_audio_pipeline_processors.py
tests/test_audio_session_lifecycle.py
tests/acceptance/test_audio_session_contract.py
```

任务清单：

1. 音频处理器链：
   - `AudioProcessor` 抽象。
   - `FormatValidator`。
   - `Pcm16Resampler`，优先使用成熟库；如果依赖不可用，明确降级。
   - `VolumeProbe`，只做质量统计，不改变音频。
   - `QualityVadProbe`，用于诊断静音和链路健康，不替代 Agent Core turn boundary。
2. 会话生命周期：
   - wake 后发布 `control.audio_session.open.requested`。
   - endpoint 回 `control.audio_session.opened` 后才打开 Agent Core session。
   - 连续对话结束后发布 `control.audio_session.close.requested`。
   - endpoint 回 `control.audio_session.closed` 后释放 active session。
   - 支持 `close_now` 和 `close_after_reply`。
3. 后台清理：
   - heartbeat timeout sweeper。
   - stream idle sweeper。
   - audio session max duration sweeper。
   - sweeper 必须在 server 启动时注册，测试中可手动触发。
4. 打断语义：
   - 用户打断时取消当前 Agent response。
   - 取消当前 output stream。
   - 不关闭整个 audio session，除非事件 payload 明确要求。
5. preflight：
   - 如果 resample / VAD 仍未启用，要在报告中写出准确降级原因。
   - 不允许配置声明已启用但代码静默跳过。

验收命令：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py audio-session-lifecycle
```

通过条件：

1. 不再只有 `FormatNormalizer` 最小校验。
2. `close_after_reply` 能等待当前 output stream 结束后再关闭 session。
3. idle stream 和 heartbeat 超时能由 server 后台任务触发。
4. preflight 能准确报告启用、降级和未实现项。

## 9. 并行线路 B：正式设备注册、鉴权与绑定管理

目标：把设备注册从本地联调能力升级为可部署的注册、鉴权、绑定和管理方案。

写入范围：

```text
audio-chat-sdk/audio_chat/control/
audio-chat-sdk/audio_chat/config.py
audio-chat-sdk/audio_chat/server.py
audio-chat-sdk/audio_chat/errors.py
app-examples/basic-app/server.yaml
tests/test_device_registration_management.py
tests/test_signed_token_auth.py
testdata/contracts/events/
```

任务清单：

1. `signed_token`：
   - token 包含 `user_id`、`device_id`、`expires_at`、`nonce`。
   - 使用 `auth.signed_token_secret_env` 读取密钥。
   - 校验签名、过期时间、user_id 和 device_id。
   - 支持 `token_clock_skew_seconds`。
2. 设备绑定：
   - 一个 `device_id` 只能绑定一个 `user_id`。
   - 同 user 下同 device 重连覆盖旧连接。
   - 不同 user 抢占同 device 必须失败，并记录原因。
3. 注册管理：
   - 注册失败快照。
   - 最近错误。
   - connection 替换日志。
   - active device set policy 当前实现 `single`，多 active set 先拒绝或明确未支持。
4. Debug API：
   - `/api/debug/devices`。
   - `/api/debug/devices/{device_id}`。
   - `/api/debug/users/{user_id}`。
   - 增加 auth/binding 诊断字段，但不泄露 token。
5. 配对预留：
   - 可先不做完整配对服务。
   - 但要定义 `PairingTokenIssuer` 接口和测试 fake issuer。

验收命令：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py auth-device-management
```

通过条件：

1. `signed_token` 不再返回 `signed_token_not_implemented`。
2. 过期 token、错误 user、错误 device、错误签名都有明确失败事件。
3. 设备绑定冲突不会破坏已有在线设备。
4. Debug API 能解释注册失败和离线原因。

## 10. 并行线路 C：Memory / Skill / MCP 能力面

目标：实现设计文档中仍停留在配置层的 Memory Service、Skill Service 和 MCP Gateway，并且全部通过 Tool 间接接入 Agent。

当前实现状态：

1. `MemoryService` 已提供 `MemoryRecord`、jsonl store、`write/search/delete` 和 `memory_search` / `manage_memory` 内置 Tool。
2. `SkillService` 已支持从配置 roots 读取 `SKILL.md` / metadata，输出 description、tool allowlist 和 prompt snippets，并通过 `read_skill` Tool 暴露给 Agent。
3. `McpGateway` 已支持从 yaml/json 配置读取 MCP tool 描述、本地 mock 调用、默认超时和 `mcp_call` Tool。
4. `ToolContextFactory` 已注入 memory / skills / mcp；三者不持有 `UserDeviceContext`，设备通讯能力仍只能经普通 Tool / Task 使用。
5. `memory-skill-mcp` lane 已有独立自动验收。

写入范围：

```text
audio-chat-sdk/audio_chat/memory/
audio-chat-sdk/audio_chat/skills/
audio-chat-sdk/audio_chat/mcp/
audio-chat-sdk/audio_chat/tools.py
audio-chat-sdk/audio_chat/app.py
audio-chat-sdk/audio_chat/config.py
tests/test_memory_service.py
tests/test_skill_service.py
tests/test_mcp_gateway.py
tests/acceptance/test_indirect_device_context_contract.py
app-examples/basic-app/server.yaml
```

任务清单：

1. Memory Service：
   - `MemoryRecord`。
   - `MemoryStore`，第一版支持 filesystem/jsonl。
   - `search(user_id, query, limit)`。
   - `write(user_id, content, metadata)`。
   - `delete` 可先不开放给模型，只留内部接口。
2. Skill Service：
   - 从 `skill.roots` 读取技能目录。
   - `read_skill(name)`。
   - skill metadata：name、description、tool allowlist、prompt snippets。
   - 读取失败有结构化错误。
3. MCP Gateway：
   - 读取 `mcp.config_path`。
   - 管理 MCP tool 描述和调用。
   - 默认超时。
   - 不允许 MCP 直接接收 `UserDeviceContext`。
4. 内置 Tool：
   - `memory_search`。
   - `manage_memory`。
   - `read_skill`。
   - `mcp_call` 或按 MCP server 自动生成 tool wrapper。
5. ToolGateway 集成：
   - ToolContextFactory 注入 memory、skills、mcp。
   - Skill 可影响 tool allowlist，但不绕过 ToolPolicy。
   - MCP / Skill 需要设备通讯能力时，必须封装为普通 Tool 或 Task。

验收命令：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py memory-skill-mcp
```

通过条件：

1. `memory.enabled=false` 时不影响现有工具。
2. `memory.enabled=true` 时内置 memory 工具可被自动暴露。
3. `read_skill` 只能读取配置 roots 下的 skill。
4. 测试证明 MCP 和 Skill 不能直接拿 `UserDeviceContext`。
5. Agent 的 provider schema 中能看到内置能力工具。

## 11. 并行线路 D：Task Engine 生产化

目标：把 Task 从进程内最小状态机推进到可长期运行、可恢复、可超时、可并发限制的后台任务系统。

写入范围：

```text
audio-chat-sdk/audio_chat/tasks.py
audio-chat-sdk/audio_chat/task_store/
audio-chat-sdk/audio_chat/app.py
audio-chat-sdk/audio_chat/observability.py
audio-chat-sdk/audio_chat/config.py
tests/test_task_engine_persistence.py
tests/test_task_engine_scheduler.py
tests/test_task_event_bridge.py
tests/acceptance/test_task_device_stream_contract.py
```

任务清单：

1. TaskSpec：
   - `task_type`。
   - `version`。
   - `timeout_seconds`。
   - `cancel_supported`。
   - `max_running_per_user`。
2. TaskStore：
   - memory store 保留。
   - jsonl store 落地。
   - sqlite store 可选，若实现要配置开关。
3. TaskScheduler：
   - 创建后调度运行。
   - 超时转 `timeout`。
   - cancel 调 `on_cancel`。
   - server 重启后可恢复未完成任务快照。
4. TaskEventBridge：
   - `requires_agent_decision` 写入 Agent 待处理队列或上下文事件。
   - `allow_direct_notify` 进入 Output Service。
   - 写入 `TaskRef`、`ArtifactRef`、runs 产物。
5. 设备 stream 场景：
   - 任务发布 `stream.control.configure.requested` 请求连续上传。
   - 任务通过 `watch_assets()` 逐帧读取。
   - cancel 时发布 stop configure event。

验收命令：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py task-engine-production
```

通过条件：

1. Task 重启恢复测试通过。
2. 超时、取消、失败、完成状态流转都有测试。
3. 同一 user 超过并发限制会拒绝或排队，行为由配置决定。
4. 连续 sensor.rgb 任务只通过 event + stream 工作，不引入第三种通讯方式。

## 12. 并行线路 E：Provider、工具调用与输出实时性

目标：解决真实 provider 联调时最容易暴露的问题：工具调用回填、同步阻塞、TTS 首包抖动和 realtime audio 输出。

写入范围：

```text
audio-chat-sdk/audio_chat/agent_core/
audio-chat-sdk/audio_chat/output/
audio-chat-sdk/audio_chat/tools.py
tests/test_text_agent_tool_loop_async.py
tests/test_realtime_provider_tool_bridge.py
tests/test_streaming_tts_runtime.py
tests/integration/test_dashscope_providers.py
```

任务清单：

1. TextAgentCore 工具循环：
   - 避免在 aiohttp event loop 内使用 `asyncio.run()`。
   - ToolGateway 调用提供同步和异步两种安全入口，或把 AgentCore 执行迁到 worker。
   - OpenAI-compatible tool call delta 聚合。
   - ToolResult 回填后继续模型循环。
2. Realtime provider 工具桥：
   - 解析 provider 的 function call / tool call 事件。
   - 聚合 arguments delta。
   - 调用 ToolGateway。
   - 把结果回填 provider。
   - 没有 provider API 支持时明确 degradation。
3. Streaming TTS：
   - DashScope TTS 改成后台队列驱动，避免 `synthesize_delta()` 同步等待 3 秒窗口。
   - 记录 first_text_at、first_audio_at、first_chunk_latency_ms。
   - provider 失败降级 mock 时写 system degradation。
4. Native audio output：
   - Omni audio delta 按输出格式拆 chunk。
   - 支持 24k 到端侧声明 sample_rate 的转换或明确要求端侧消费 24k。
   - final done 后关闭 stream。
5. 真实 provider 集成测试：
   - 默认无 key 时 skip。
   - 有 key 时跑 ASR、TTS、text model、realtime mock 或真实 smoke。

验收命令：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py provider-output-runtime
DASHSCOPE_API_KEY=... uv run python -m pytest tests/integration/test_dashscope_providers.py -q
```

通过条件：

1. 网络 WebSocket 请求中触发 tool call 不会报 `asyncio.run() cannot be called from a running event loop`。
2. 文本 delta 能持续进入 TTS，首包延迟有指标。
3. RealtimeAudioAgentCore 能记录 provider tool call 或明确降级原因。
4. 真实 provider 缺 key 不失败，配置禁止 fallback 时必须失败且错误明确。

## 13. 并行线路 F：参考端侧与多端联调

目标：让开发者可以用不同端侧快速验证同一套 server SDK，而不是只依赖 in-process playback。

写入范围：

```text
device-examples/python-glass/
device-examples/browser-glass/
device-examples/python-phone/
device-examples/native-ios-phone/
device-examples/native-esp32-glass/
app-examples/basic-app/
docs/esp32-s3-endpoint-bridge.md
tests/test_web_glass_endpoint.py
tests/test_python_phone_mock_endpoint.py
tests/test_endpoint_config_sync.py
```

任务清单：

1. browser-glass：
   - 完成 control ws、stream ws。
   - getUserMedia 采集 PCM。
   - WebRTC AEC / NS / AGC 配置。
   - AudioWorklet 或等价方案稳定发送 `sensor.mic`。
   - 播放 `actuator.speaker`。
   - 支持 Realtime 模式不发送 final，由 provider turn detection 判断。
2. Python phone mock：
   - 模拟手机端传感器和执行器。
   - 支持 RGB 上传、haptic、speaker、通知。
   - 支持多设备同 user 注册。
3. iOS phone 参考端：
   - 第一阶段可以只提交目录、README、协议配置和最小 Swift 客户端骨架。
   - 后续补 simulator build。
4. ESP32-S3：
   - 保留 AEC bridge。
   - 等 browser-glass 全双工稳定后再做真机验收。
   - 固件必须遵守 wake 后才打开音频长连接。
5. Config sync：
   - 生成 server、web、python mock、iOS、ESP32 的本地配置。
   - 避免手改 public_url、user_id、device_id、token。

当前落地状态：

1. `browser-glass` 已有静态页面和协议检查，覆盖 control ws、stream ws、WebRTC
   AEC / NS / AGC、持续 `sensor.mic`、speaker 播放回执、用户打断和 Realtime
   模式不发送 final。
2. `python-phone` 已有网络 endpoint，能通过真实 `/ws/control` 注册，按
   event/subscription 接收 `sensor.rgb` 采集请求，使用 `/ws/stream` 上传 RGB
   资产，并消费 `actuator.speaker` / `actuator.haptic` 输出 stream。
3. `ios-phone` 和 `esp32-s3` 已提交目录、README 和最小配置样例，作为后续端侧小组
   的协议锚点；本阶段不阻塞 simulator build 或真机固件。
4. `audio-chat.config.sync` 已生成 server、browser-glass、python phone mock、glass
   playback、iOS 和 ESP32-S3 本地配置，统一 `server_url`、`user_id` 和可选 token，
   并保证各端 `device_id` 不重复。
5. `endpoint-reference` lane 已覆盖上述最小闭环：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py endpoint-reference \
  --report runs/acceptance/endpoint-reference.json
```

验收命令：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py endpoint-reference
```

跨端人工联调顺序：

```bash
# 在项目根目录执行
uv run audio-chat.server.run --config app-examples/basic-app/server-omni.yaml
# 另一个终端或浏览器打开 browser-glass
# 触发页面注册、唤醒、麦克风上传、speaker 播放
curl http://127.0.0.1:8765/api/debug/devices
curl http://127.0.0.1:8765/api/debug/playback
```

通过条件：

1. browser-glass 能完成真实浏览器注册、唤醒、麦克风上传和 speaker 播放。
2. Python phone mock 能作为独立网络 endpoint 运行。
3. 多设备订阅分发由注册策略决定，不按设备类型硬编码。
4. 配置同步后各端使用同一组 server_url、user_id、device_id 和 token。

### 13.1 F-iOS：iOS phone 参考端可运行客户端

目标：把 `device-examples/native-ios-phone` 从协议锚点、README 和配置样例推进到可在
Simulator 或真机上运行的参考客户端。该客户端不是生产 App，但必须能验证
`audio-chat` 的设备注册、事件订阅、stream 打开、基础传感器上传和执行器消费。

写入范围：

```text
device-examples/native-ios-phone/
device-examples/native-ios-phone/AppConfig.example.json
device-examples/native-ios-phone/README.md
audio-chat-sdk/audio_chat/cli/config.py
tests/test_ios_phone_endpoint_contract.py
tests/test_endpoint_config_sync.py
```

任务清单：

1. iOS 项目骨架：
   - 提供可打开的 Xcode project 或 Swift Package。
   - 配置读取 `AppConfig.json`，字段与 `audio-chat.config.sync` 生成结果一致。
   - README 写清楚 Simulator 和真机的启动方式。
2. Control WebSocket：
   - 连接 `/ws/control`。
   - 发送 `control.device.register.requested`。
   - 携带 `user_id`、`device_id`、properties、subscriptions 和 auth。
   - 接收并处理 `control.device.registered`、`stream.output.*`、`stream.control.*`。
3. Stream WebSocket：
   - 支持上传 `sensor.mic` 或测试 PCM stream。
   - 支持上传 `sensor.rgb` 测试图片或相册图片。
   - 支持消费 `actuator.speaker` 输出 stream，至少能写入本地 buffer 并上报播放完成。
4. 端侧能力：
   - 声明 `streams.produce` 和 `streams.consume`。
   - 唤醒、AEC、相机、播放器能力先以 capability 声明和可替换接口表达。
   - 真实麦克风、相机和播放器接入可以分阶段推进，但不能改变协议。
5. 配置同步：
   - `audio-chat.config.sync` 能生成 iOS 使用的 `AppConfig.json`。
   - 多端配置中的 `device_id` 不重复。
   - signed_token 开启时能写入 token 或明确提示开发者生成方式。

验收命令：

```bash
# 在项目根目录执行
uv run python -m pytest \
  tests/test_ios_phone_endpoint_contract.py \
  tests/test_endpoint_config_sync.py \
  -q
```

如果本地安装 Xcode，还需要补充并运行：

```bash
cd device-examples/native-ios-phone
xcodebuild -scheme AudioChatPhone -destination 'platform=iOS Simulator,name=iPhone 16' build
```

通过条件：

1. iOS 配置 schema 与 server、browser-glass、python-phone 使用同一套字段语义。
2. iOS 注册事件符合协议 golden，不引入 iOS 专用 RPC。
3. iOS 可订阅并处理输出 stream 事件，能消费或模拟消费 `actuator.speaker`。
4. iOS 可上传至少一种输入 stream，优先 `sensor.rgb` 或测试 PCM `sensor.mic`。
5. 无 Xcode 环境时，contract test 必须仍能校验配置、协议和文档；有 Xcode 环境时，build 不应失败。

当前落地状态：

1. `device-examples/native-ios-phone/AudioChatPhone.xcodeproj` 已提供最小 SwiftUI App。
2. `AudioChatEndpointRuntime` 已实现 `/ws/control` 注册、`/ws/stream` 连接、`sensor.rgb`
   测试 JPEG 上传、测试 `sensor.mic` PCM 上传和 `actuator.speaker` buffer 消费回执。
3. `testdata/contracts/endpoints/ios_phone_register_requested.json` 已记录 iOS 注册 golden。
4. `tests/test_ios_phone_endpoint_contract.py` 和 `tests/test_endpoint_config_sync.py` 覆盖无 Xcode
   环境下的配置、协议和文档验收；有 Xcode 时仍需补跑上面的 `xcodebuild` 命令。

### 13.2 F-ESP32：ESP32-S3 真机桥接与验收

目标：把 `device-examples/native-esp32-glass` 从 AEC bridge、README 和配置样例推进到可真机联调的
参考端。ESP32-S3 必须遵守端侧职责边界：端侧负责唤醒、AEC、麦克风采集和喇叭播放，
server 只通过 event 和 stream 管理会话、输入、输出和关闭。

写入范围：

```text
device-examples/native-esp32-glass/
device-examples/native-esp32-glass/local.env.example
docs/esp32-s3-endpoint-bridge.md
device-examples/native-esp32-glass/audio_chat_esp32_s3/esp32_aec.py
audio-chat-sdk/audio_chat/cli/config.py
tests/test_esp32_s3_endpoint_contract.py
tests/test_endpoint_config_sync.py
```

任务清单：

1. 固件协议适配：
   - 连接 `/ws/control` 完成设备注册。
   - wake 后才打开 `/ws/stream` 上传 `sensor.mic`。
   - 连续对话关闭后释放音频 stream 连接。
   - 接收 `actuator.speaker` 输出 stream 并写入播放器。
2. AEC bridge：
   - 保留并整理现有 ESP32-S3 AEC 测试经验。
   - 明确端侧 playback reference、采样率、chunk 时长和延迟配置。
   - server preflight 只诊断能力声明，不假装替代端侧 AEC。
3. 会话生命周期：
   - 处理 `control.audio_session.open.requested`。
   - 回传 `control.audio_session.opened`。
   - 处理 `control.audio_session.close.requested`。
   - 回传 `control.audio_session.closed`。
   - 支持用户打断时取消当前播放，但不默认关闭整个 audio session。
4. 配置同步：
   - `audio-chat.config.sync` 能生成 ESP32-S3 使用的本地配置或 `.env`。
   - 包含 server URL、user_id、device_id、auth 和音频格式。
   - 不要求开发者手写与 server 不一致的 device_id。
5. 诊断与验收产物：
   - 固件或 bridge 输出注册、订阅、stream open、chunk count、播放完成、关闭原因。
   - server runs 产物能对应同一 session_id。
   - 失败时能区分 Wi-Fi、auth、control ws、stream ws、音频格式和播放回执问题。

验收命令：

```bash
# 在项目根目录执行
uv run python -m pytest \
  tests/test_esp32_s3_endpoint_contract.py \
  tests/test_endpoint_config_sync.py \
  -q

uv run python scripts/acceptance_check.py esp32-s3-endpoint \
  --report runs/acceptance/esp32-s3-endpoint.json
```

有真机环境时补充运行硬件 smoke：

```bash
# 在项目根目录执行
uv run audio-chat.server.run --config app-examples/basic-app/server-omni.yaml
# 另一个终端刷写或启动 ESP32-S3，并保留串口日志和 runs/audio-chat/sessions/<session_id>/ 产物
```

通过条件：

1. ESP32-S3 注册事件、订阅和 capability 与协议契约一致。
2. wake 前不建立持续音频 stream；wake 后上传 `sensor.mic`；连续对话结束后释放连接。
3. `actuator.speaker` 输出能被端侧消费，并能上报播放完成或失败。
4. AEC 相关能力由端侧声明和实现，server 只做诊断和会话控制。
5. 无硬件环境时，contract test 必须覆盖协议、配置和文档；有硬件环境时，真机 smoke 需要保留串口日志与 server runs 产物。

## 14. 并行线路 G：开发者体验、CLI 与发布前检查

目标：让 SDK 具备开发者可安装、可启动、可预检、可排障的完整体验。

写入范围：

```text
pyproject.toml
README.md
audio-chat-sdk/audio_chat/cli/
audio-chat-sdk/audio_chat/preflight.py
scripts/acceptance_check.py
tests/test_cli_server_process.py
tests/test_package_boundary.py
tests/test_docs_commands.py
```

任务清单：

1. CLI，以下命令是下一阶段目标：
   - developer-experience 线路补齐下列 CLI，其中 P0-A 已落地
     `server.*`、`config.sync` 和 `phone.mock`，本线路补齐 `web.open` 的无副作用检查入口。
   - `audio-chat.server.start`。
   - `audio-chat.server.stop`。
   - `audio-chat.server.logs`。
   - `audio-chat.config.sync`。
   - `audio-chat.phone.mock`。
   - `audio-chat.web.open`。
2. package check：
   - editable install。
   - wheel build。
   - import public API。
   - endpoint 参考实现不泄漏到顶层公开包。
3. preflight：
   - live server。
   - config validation。
   - contract tests。
   - recent playback。
   - provider key check。
   - endpoint config check。
4. README：
   - 只保留真实可运行命令。
   - 单机 mock、网络 playback、browser-glass、provider smoke 分开写。
5. 发布前检查：
   - 全 lane 验收。
   - docs command check。
   - package check。
   - playback smoke。

验收命令：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py developer-experience
```

通过条件：

1. README 中的命令都有测试或 docs command check 覆盖。
2. server start/stop 不残留后台进程。
3. package check 能从干净环境导入公开 API。
4. preflight 报告能直接定位配置、协议、server 和 endpoint 问题。

## 15. 并行线路 H：文档、契约和迁移样板

目标：让设计文档、实现、测试和业务迁移入口保持一致，为后续业务能力迁移做准备。

当前落地状态：

1. 架构文档已补充真实状态矩阵，按已实现、部分实现、未实现区分当前能力和 roadmap。
2. `phase3-migration-guide.md` 已提供旧 SDK 到 `audio-chat` 的概念映射、迁移约束、样板路径和联调观察点。
3. `testdata/contracts` 已补齐 auth 注册 golden、task lifecycle golden 和 output arbitration golden；事件 golden 与 stream chunk golden 继续沿用上一阶段契约。
4. `app-examples/for-blind-app/templates` 已提供 `find_object` Tool、`continuous_rgb_analyze` Task 和 `notification_task` Task 样板，均只通过 `UserDeviceContext` 使用设备通讯能力。
5. `next-docs-contract` lane 已接入文档、契约和迁移样板自动验收。

写入范围：

```text
docs/audio-chat-sdk-architecture.md
docs/phase3-migration-guide.md
docs/next-stage-parallel-development-plan.md
testdata/contracts/
tests/acceptance/
app-examples/
```

任务清单：

1. 更新架构文档：
   - 标注已实现、部分实现、未实现。
   - 删除过时的后续目标或移动到 roadmap。
   - 保持术语统一：Control Service、Stream Service、Audio Pipeline、Asset Service、Agent Core、Output Service、Task Engine、ToolGateway。
2. 契约补齐：
   - 内置事件 golden。
   - stream chunk golden。
   - auth 注册 golden。
   - task lifecycle golden。
   - output arbitration golden。
3. 迁移样板：
   - `find_object` Tool 样板。
   - `continuous_rgb_analyze` Task 样板。
   - `notification_task` 样板。
   - 只使用 `UserDeviceContext`。
4. 文档验收：
   - 文档中出现的 CLI 必须在 pyproject entry point 或 roadmap 表中。
   - 文档中出现的 public class 必须能 import。
   - 文档中标记为已实现的功能必须有测试或验收记录。

验收命令：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py next-docs-contract
```

通过条件：

1. 架构文档不再把未实现能力写成已完成。
2. 每个下一阶段核心能力都有契约或测试。
3. 迁移样板能作为开发者复制起点。

## 16. 推荐并行顺序

已完成的第一批：

1. P0-A 开发者可用验收入口。
2. P0-B 示例 App 与能力回放闭环。
3. P0-C 下一阶段验收入口。
4. G 开发者体验、CLI 与发布前检查。
5. F 参考端侧与多端联调中的 Python playback、Python phone mock 和 browser-glass 最小闭环。

第一批完成后，已冻结开发者可用基线。需要复核时运行：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py developer-usability --keep-going \
  --report runs/acceptance/developer-usability-baseline.json
uv run python scripts/acceptance_check.py capability-template-playback --keep-going \
  --report runs/acceptance/capability-template-playback-baseline.json
```

这两个报告通过前，不建议把团队主要精力投入 iOS、ESP32 真机或复杂 provider 适配，因为功能开发者还没有稳定入口。当前这部分已经通过，可作为后续开发入口。

已完成的第二批：

1. A Audio Pipeline 与会话生命周期。
2. B 正式设备注册、鉴权与绑定管理。
3. E Provider、工具调用与输出实时性。
4. D Task Engine 生产化。
5. H 文档、契约和迁移样板。

第三批，当前应立即推进：

1. C Memory / Skill / MCP 能力面。
2. F 线后续子任务：iOS phone 参考端从协议骨架推进到可运行客户端。
3. F 线后续子任务：ESP32-S3 真机桥接从 AEC bridge 和配置样例推进到真机验收。
4. 业务能力从老 SDK 迁移到 `audio-chat` 示例项目。
5. 全量验收稳定性，尤其是 `acceptance_check.py all --keep-going` 中 C 线缺失测试和真实 provider 集成测试的可重复性。

依赖关系：

1. C 依赖 ToolGateway 当前稳定契约，但不强依赖真实端侧。
2. D 已完成，后续 C 和业务迁移可以依赖 Task Engine 的持久化、恢复、取消、超时和任务事件桥。
3. E 已完成，后续真实 provider 问题应优先补集成测试稳定性和诊断，而不是重新设计 Agent Core 工具桥。
4. F 的 Python playback / Python phone mock / browser-glass 最小闭环已完成；iOS phone 和 ESP32-S3 在 F 线中只完成了协议锚点、配置样例和文档骨架，下一步端侧重点转向可运行客户端与真机验收。
5. H 已完成第一轮，后续每条线路仍必须同步更新文档、契约和迁移样板。

## 17. 合并要求

每条线路提交时必须包含：

1. 代码改动。
2. 对应测试。
3. 对应 lane 验收报告。
4. 涉及协议时更新 golden。
5. 涉及配置时更新 `app-examples/basic-app/server.yaml`。
6. 涉及跨端时更新启动顺序和观察点。

最终合并前必须通过：

```bash
# 在项目根目录执行
uv run python scripts/acceptance_check.py all --keep-going \
  --report runs/acceptance/next-stage-full.json
```

如果有真实 provider key，还需要额外运行：

```bash
# 在项目根目录执行
DASHSCOPE_API_KEY=... uv run python -m pytest tests/integration/test_dashscope_providers.py -q
```

如果有 browser-glass 人工联调条件，还需要保留：

```text
runs/audio-chat/sessions/<session_id>/events.jsonl
runs/audio-chat/sessions/<session_id>/stream-events.jsonl
runs/audio-chat/sessions/<session_id>/agent-events.jsonl
runs/audio-chat/sessions/<session_id>/tool-events.jsonl
runs/audio-chat/sessions/<session_id>/task-events.jsonl
runs/audio-chat/sessions/<session_id>/assets.jsonl
runs/audio-chat/sessions/<session_id>/output-decisions.jsonl
runs/audio-chat/sessions/<session_id>/result.json
浏览器 console 日志
server DEBUG 日志
```

## 18. 本阶段完成定义

下一阶段完成时，应达到：

1. 功能开发者可以按照 README 从零安装、同步配置、启动 server、启动 mock 设备并完成一次回放。
2. 功能开发者可以复制 `app-examples/basic-app` 新增 Tool / Task，且无需修改 SDK 内部代码即可自动发现。
3. Tool / Task 能通过 `UserDeviceContext` 使用事件和 stream 控制设备、读取资产和输出结果。
4. Python playback、Python phone mock 和 browser-glass 至少有两个端侧参考实现可跑通。
5. TextAgentCore 和 RealtimeAudioAgentCore 在 mock provider 下都能跑通工具发现、工具调用和输出链路。
6. Output Service 能处理 text delta 流式 TTS、audio delta 直通、优先级仲裁和打断产物记录。
7. CLI 和 preflight 足以支持普通开发者安装、启动、回放、排障。
8. 文档中“已实现”的能力都有代码、测试或验收报告支撑。

本阶段不要求：

1. 所有老版 SDK 能力迁移完成。
2. iOS 和 ESP32 真机达到生产可用。
3. Memory / Skill / MCP 达到完整生态能力。
4. 设备配对和多 active device set 达到生产级管理后台能力。
