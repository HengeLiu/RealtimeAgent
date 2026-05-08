# audio-chat 并行开发计划

更新时间：2026-05-07

## 1. 当前代码状态判断

结论：当前 `audio-chat` 已经可以进入“先 P0、再并行”的开发节奏，但不适合直接全量并行。

已具备的基础：

1. 已有最小 server SDK 包，入口在 `server-python/audio_chat`。
2. 已有 Control Service、Stream Service、Asset Service、Output Service、TextAgentCore、RealtimeAudioAgentCore、Python playback endpoint 和 Web endpoint 样板。
3. 已有 `audio-chat.server.run`、`audio-chat.dev.preflight`、`audio-chat.playback.glass` 三个最小 CLI。
4. 从 `audio-chat/` 目录运行 `uv run python -m pytest tests -q` 当前通过 64 个测试。
5. 已有设计验收测试、协议验收测试、网络 playback 测试和 provider/output 测试。

不能直接全量并行的原因：

1. 从仓库根目录运行 `uv run python -m pytest audio-chat/tests -q` 当前会因为测试相对路径失败，说明统一验收入口还没有冻结。
2. 设计文档中的 `ToolResult`、`TaskEvent`、自动发现、TaskEventBridge、Notification Coordinator、Turn Recorder 等公共契约比当前代码更完整，需要先对齐公开 API。
3. ToolGateway 已存在，但 TextAgentCore 和 RealtimeAudioAgentCore 还没有真正通过 ToolGateway 完成工具发现、调用和结果回填。
4. 自动发现当前只扫描直接模块类，不支持递归包扫描、重复名称校验、fail_fast 和抽象类排除等设计要求。
5. preflight 当前只做很薄的协议和 server health 检查，还没有 package-check、boundary-check、contract-tests 和 recent playback 聚合。

因此建议先用 1 条 P0 前置线路推进到“公共契约冻结”，再开启 6 条互不重叠的并行开发线路。

## 2. 自动验收入口

新增统一验收脚本：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py p0-foundation
uv run python scripts/acceptance_check.py protocol-control
uv run python scripts/acceptance_check.py stream-asset
uv run python scripts/acceptance_check.py tool-task-agent
uv run python scripts/acceptance_check.py output-observability
uv run python scripts/acceptance_check.py endpoint-playback
uv run python scripts/acceptance_check.py docs-contract
uv run python scripts/acceptance_check.py all --keep-going
```

脚本会输出 JSON 报告到：

```text
audio-chat/runs/acceptance/acceptance-result.json
```

每条开发线路完成时，开发人员必须提交：

1. 代码和测试。
2. 对应线路的 `acceptance_check.py <lane>` 报告。
3. 如果涉及跨端协议，补充 golden 或 playback 配置。
4. 如果涉及端侧或网络链路，补充启动顺序和观察点。

## 3. P0 前置线路：公共契约冻结

目标：把当前代码推进到可以多人并行的稳定状态。

写入范围：

```text
audio-chat/server-python/audio_chat/__init__.py
audio-chat/server-python/audio_chat/config.py
audio-chat/server-python/audio_chat/tools.py
audio-chat/server-python/audio_chat/tasks.py
audio-chat/server-python/audio_chat/preflight.py
audio-chat/tests/acceptance/
audio-chat/tests/test_phase2_assets_and_endpoint.py
audio-chat/scripts/acceptance_check.py
```

任务清单：

1. 修复测试运行目录问题，确保从仓库根目录和 `audio-chat/` 目录运行测试都通过。
2. 对齐公开对象：
   - `ToolResult.success()` / `ToolResult.failed()`。
   - `ToolResult.ok/data/message/assets/artifacts/tasks/meta/error`。
   - `ToolError.code/message/retryable/details`。
   - `TaskEvent.requires_agent_decision`。
   - `TaskEvent.allow_direct_notify`。
   - `TaskRef`、`AssetRef`、`ArtifactRef` 在 `audio_chat.__init__` 中稳定导出。
3. 自动发现补齐：
   - 递归扫描 package。
   - 抽象基类和以下划线开头内部类不注册。
   - Tool `name` / Task `task_type` 重复时 fail fast。
   - import 失败时按 `fail_fast` 决定中止或记录错误。
4. 配置补齐：
   - `tools.discover.recursive`。
   - `tools.discover.fail_fast`。
   - `tasks.discover.recursive`。
   - `tasks.discover.fail_fast`。
   - `dev_checks.report_path`。
   - `dev_checks.require_recent_playback_ok`。
5. preflight 聚合：
   - contract tests。
   - package import check。
   - boundary check。
   - require-server live check。
   - recent playback result check。
6. 为 P0 增加验收测试，覆盖公开对象字段、自动发现和根目录运行。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py p0-foundation
```

通过条件：

1. `unit_all_from_audio_chat` 通过。
2. `unit_all_from_repo_root` 通过。
3. `preflight` 生成 JSON，且状态为 ok。
4. P0 新增测试能明确证明公开契约已经冻结。

## 4. 并行线路 A：Protocol / Control / Device

目标：把事件协议、设备注册、订阅分发和 Device 抽象做成稳定底座。

写入范围：

```text
audio-chat/server-python/audio_chat/protocol.py
audio-chat/server-python/audio_chat/control/
audio-chat/server-python/audio_chat/errors.py
audio-chat/tests/test_protocol_contracts.py
audio-chat/tests/test_control_service.py
audio-chat/tests/acceptance/test_protocol_routing_acceptance.py
audio-chat/testdata/contracts/
```

任务清单：

1. Event 信封校验补齐：
   - event_name 必须符合命名规范。
   - producer_id 只表示生产者，不允许 target/source_device 字段进入公共事件。
   - payload 中媒体大字节拒绝进入控制事件。
2. 订阅 filter 补齐：
   - 支持事件信封字段。
   - 支持 `payload.*`。
   - 支持 `capabilities.*`。
   - 数组包含匹配。
   - 明确不支持脚本、正则和复杂表达式。
3. Device 抽象补齐：
   - `Device` 内部对象。
   - `DeviceSnapshot` 只读快照。
   - active device set。
   - 心跳超时标记离线。
   - 最近错误和注册失败原因。
4. 注册绑定补齐：
   - static_token。
   - signed_token 可先留接口和失败提示。
   - device_id 不能绑定多个 user_id。
   - 重新连接覆盖旧连接。
5. debug API 补齐：
   - `/api/debug/devices/{device_id}`。
   - `/api/debug/users/{user_id}` 包含订阅和最近错误。
6. 契约 golden：
   - 注册成功。
   - 注册失败。
   - 订阅命中。
   - filter 不命中。
   - 设备重连。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py protocol-control
```

通过条件：

1. 不允许业务代码按 device_id 点对点发布事件。
2. 所有分发都由注册订阅策略解析。
3. golden JSON 覆盖成功和失败路径。
4. debug snapshot 能解释设备为什么收到或没有收到事件。

## 5. 并行线路 B：Stream / Asset

目标：把 stream 生命周期、二进制 chunk、资产缓存和连续传感器资产处理做稳定。

写入范围：

```text
audio-chat/server-python/audio_chat/stream/
audio-chat/server-python/audio_chat/asset/
audio-chat/server-python/audio_chat/audio_pipeline/
audio-chat/tests/test_stream_and_audio_pipeline.py
audio-chat/tests/test_phase2_assets_and_endpoint.py
audio-chat/testdata/contracts/streams/
```

任务清单：

1. StreamChunk 契约：
   - header 长度。
   - payload_size。
   - seq。
   - timestamp。
   - final。
   - metadata。
   - golden bin 测试。
2. Stream 生命周期：
   - input opened / closed / failed。
   - output open requested / close requested / cancelled / closed。
   - idle timeout。
   - max_chunk_bytes。
3. 输出 stream consumer 冻结：
   - 打开 output stream 时冻结 consumer_device_ids。
   - 后续 chunk 和 close/cancel 只发给冻结消费者。
   - Tool / Task 仍不能传 device_id。
4. Asset Service：
   - `request_asset()` 缓存命中和未命中。
   - `stream.control.configure.requested` 触发端侧上传。
   - request_id / correlation_id 防串包。
   - `watch_assets()` 连续帧读取。
   - TTL 和 max_asset_bytes。
5. Audio Pipeline：
   - 明确 AEC 只做 endpoint_only。
   - 质量诊断事件。
   - 重采样和音量归一如果未实现，preflight 必须明确降级。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py stream-asset
```

通过条件：

1. sensor.rgb 连续帧可以按 correlation_id 被 Task 逐帧读取。
2. StreamChunk golden 可跨端复用。
3. 资产请求不会把图片字节塞进控制事件 payload。
4. output stream 插播或关闭不会复用旧 stream 语义。

## 6. 并行线路 C：Tool / Task / Agent Tool Loop

目标：让业务开发者只实现 `BaseTool` / `BaseTask`，并让 Agent Core 真正通过 ToolGateway 调用工具。

写入范围：

```text
audio-chat/server-python/audio_chat/tools.py
audio-chat/server-python/audio_chat/tasks.py
audio-chat/server-python/audio_chat/agent_core/
audio-chat/server-python/audio_chat/app.py
audio-chat/tests/acceptance/test_protocol_native_tool_task_contract.py
audio-chat/tests/test_agent_core_router.py
```

任务清单：

1. ToolGateway：
   - list_tools。
   - provider schema build。
   - input_model JSON Schema。
   - allowlist / denylist。
   - timeout。
   - structured ToolTrace。
   - progress_message 一轮最多播一次。
2. TextAgentCore 工具循环：
   - TextModelAdapter 支持 tool_call delta 或 mock tool_call。
   - ToolGateway 调用。
   - ToolResult 回填 messages。
   - 工具结果继续模型循环。
3. RealtimeAudioAgentCore 工具桥：
   - RealtimeToolBridge。
   - provider tool schema。
   - realtime tool call 参数聚合。
   - ToolResult 回填 provider。
4. TaskEngine：
   - create/query/cancel。
   - TaskStore。
   - 状态机。
   - TaskEventBridge。
   - `requires_agent_decision` 回流 Agent。
   - `allow_direct_notify` 进入 Output Service。
5. 内置工具：
   - `request_asset`。
   - `configure_asset_stream`。
   - `publish_device_command`。
   - `query_device_state`。
   - `query_task_status`。
   - `cancel_task`。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py tool-task-agent
```

通过条件：

1. 业务 Tool 不 import Control Service / Stream Service。
2. Tool / Task 只能通过 UserDeviceContext 使用设备能力。
3. Agent Core 不直接 import 业务 Tool。
4. Mock text model 能触发一次 Tool 调用，并把 ToolResult 回填后继续生成回复。
5. TaskEvent 能写入 runs、消息历史，并按配置进入 Output Service 或 Agent Core。

## 7. 并行线路 D：Output / Notification / Observability

目标：稳定所有 server 到端侧的可听输出、通知协调、播放仲裁和运行产物。

写入范围：

```text
audio-chat/server-python/audio_chat/output/
audio-chat/server-python/audio_chat/observability.py
audio-chat/server-python/audio_chat/tasks.py
audio-chat/tests/test_phase2_providers_output.py
audio-chat/tests/test_realtime_audio_agent_core.py
```

任务清单：

1. Output Router：
   - native audio delta 透传。
   - text_delta 流式进入 Streaming TTS。
   - cached prompt audio 接口。
   - provider 不支持 streaming 时记录降级。
2. Notification Coordinator：
   - dedupe_key 去重。
   - 合并策略。
   - TaskEvent priority 映射。
   - direct notify。
   - requires_agent_context_sync 记录。
3. Playback Arbiter：
   - priority。
   - on_interrupted drop / requeue。
   - on_blocked queue / drop。
   - ttl_seconds。
   - recent decisions snapshot。
   - `/api/debug/playback`。
4. 插播边界：
   - 旧 stream 下发 cancel。
   - 新 stream 打开。
   - 不把新音频写入旧 stream。
5. Turn Recorder：
   - input wav。
   - transcript artifact。
   - model request。
   - output wav。
   - tool traces。
   - task events。
   - playback decisions。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py output-observability
```

通过条件：

1. 文本 delta 到 TTS 到 speaker chunk 是流式链路。
2. native audio 不经过 TTS。
3. 高优先级输出会取消旧 output stream 并打开新 stream。
4. 被打断音频按 on_interrupted 策略处理。
5. runs 产物足以复盘一次交互。

## 8. 并行线路 E：Agent Core / Provider

目标：让 TextAgentCore 和 RealtimeAudioAgentCore 分别稳定工作，并提供 provider adapter 扩展点。

写入范围：

```text
audio-chat/server-python/audio_chat/agent_core/
audio-chat/server-python/audio_chat/audio_pipeline/
audio-chat/tests/test_agent_core_router.py
audio-chat/tests/test_realtime_audio_agent_core.py
audio-chat/tests/integration/test_dashscope_providers.py
```

任务清单：

1. AgentCore 接口统一：
   - open。
   - append_audio_event。
   - commit_input。
   - interrupt。
   - close。
   - events。
2. AgentCoreRouter：
   - text。
   - realtime_audio。
   - auto。
   - custom factory。
3. TextAgentCore：
   - ASR pipeline。
   - turn boundary。
   - MessageBuilder。
   - TextModelAdapter。
   - TextToolLoop。
   - TextOutputAdapter。
4. RealtimeAudioAgentCore：
   - RealtimeProviderAdapter。
   - SessionManager。
   - InputAdapter。
   - RealtimeToolBridge。
   - OutputAdapter。
   - provider session close_after_reply / close_now。
5. Provider 集成：
   - mock provider 必须稳定。
   - DashScope / Qwen 集成测试默认跳过，缺 key 时有明确 skip 或 degradation。
   - 不允许 provider 异常刷屏。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py output-observability
uv run python -m pytest tests/integration/test_dashscope_providers.py -q
```

通过条件：

1. `agent.mode=text` 和 `agent.mode=realtime_audio` 都能通过 mock 链路。
2. provider adapter 异常会写入 system error 和 agent event。
3. 用户 interrupt 会取消模型响应和当前输出。
4. close session 会释放 provider session 和低优先级输出。

## 9. 并行线路 F：Endpoint / Playback / CLI

目标：让参考端侧和回放成为每个能力开发者的高频自测入口。

写入范围：

```text
audio-chat/server-python/audio_chat/endpoints/
audio-chat/endpoints/web-glass/
audio-chat/examples/minimal/
audio-chat/server-python/audio_chat/server.py
audio-chat/server-python/audio_chat/preflight.py
audio-chat/tests/test_network_server_playback.py
audio-chat/tests/playback/test_python_playback.py
audio-chat/tests/test_web_glass_endpoint.py
```

任务清单：

1. Python playback endpoint：
   - 真实 control ws。
   - 真实 stream ws。
   - 注册、心跳、唤醒。
   - sensor.mic 上传。
   - sensor.rgb 按控制事件上传。
   - actuator.speaker 记录和回执。
   - result.json。
2. Python mock multi-device：
   - 多设备同 user_id 注册。
   - 一个设备只产 sensor.rgb。
   - 一个设备只消费 actuator.speaker。
   - 订阅策略可配置。
3. Web endpoint：
   - getUserMedia。
   - Web Audio 播放。
   - 注册能力和订阅。
   - 模拟唤醒。
   - 可配置 server_url。
4. CLI：
   - 后续目标，当前未落地：`audio-chat.server.start/stop/logs`。
   - 后续目标，当前未落地：`audio-chat.config.sync`。
   - 后续目标，当前未落地：`audio-chat.mock.phone`。
   - 后续目标，当前未落地：`audio-chat.web.open`。
5. 网络联调：
   - server 启动后 playback CLI 能跑通。
   - require-server preflight 能读 health 和 debug devices。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py endpoint-playback
```

通过条件：

1. playback endpoint 是独立设备进程或独立网络客户端，不是 server 内部 mock。
2. 回放 result.json 包含断言、输出字节、事件链和失败原因。
3. 多设备 mock 能证明订阅策略分发，而不是硬编码设备类型。
4. Web endpoint 能完成最小注册和唤醒流程。

## 10. 并行线路 G：Docs / Contracts / Release Check

目标：保持设计、代码、测试和开发者入口一致。

写入范围：

```text
audio-chat/docs/
audio-chat/testdata/contracts/
audio-chat/tests/acceptance/
audio-chat/README.md
audio-chat/pyproject.toml
audio-chat/scripts/acceptance_check.py
```

任务清单：

1. 设计文档和代码术语对齐。
2. 每个公开对象都有对应 acceptance 测试。
3. 每个内置事件都有 golden JSON。
4. 每个 stream 类型有契约测试。
5. README 只写真实可运行命令。
6. release check：
   - `acceptance_check.py all --keep-going`。
   - package check。
   - boundary check。
   - playback smoke。
   - 文档链接检查。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py docs-contract
```

通过条件：

1. 文档中没有已删除的 Request/Intent/RPC 概念。
2. 文档中的 CLI 都能找到对应 entry point，或明确标注为后续目标。
3. 设计新增公共字段时，测试和配置模板同步更新。

## 11. 并行协作规则

1. P0 完成前，不建议多人同时改公开对象签名。
2. 每条线路只改自己的写入范围，跨范围改动先在文档或 issue 中说明。
3. 新增公共事件、stream 类型、ToolResult 字段、TaskEvent 字段时，必须同步契约测试。
4. 所有线路都不能引入业务硬编码 device_id。
5. Tool / Task 不允许 import Control Service、Stream Service、Output Service 内部类。
6. 端侧参考实现只依赖协议，不依赖 server 内部对象。
7. 最终合并前至少运行：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py all --keep-going
```

## 12. 推荐执行顺序

第一批：

1. P0 公共契约冻结。

第二批并行：

1. A Protocol / Control / Device。
2. B Stream / Asset。
3. D Output / Notification / Observability。
4. G Docs / Contracts / Release Check。

第三批并行：

1. C Tool / Task / Agent Tool Loop。
2. E Agent Core / Provider。
3. F Endpoint / Playback / CLI。

原因：

1. Tool / Task 依赖 P0 的公开对象，且依赖 A/B/D 的底座稳定。
2. Agent Provider 可以先用 mock 并行，但工具桥接需要 C 的 ToolGateway 语义。
3. Endpoint / Playback 可以早做，但完整验收依赖 A/B/D 的协议和输出语义。
