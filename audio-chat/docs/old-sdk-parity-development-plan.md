# audio-chat 老 SDK 可用性对齐开发计划

更新时间：2026-05-07

## 1. 阶段目标

`next-stage-parallel-development-plan.md` 已完成并通过验收后，`audio-chat` 已经具备 server SDK 的核心骨架：设备注册、事件订阅、stream、音频会话、Agent Core、Tool / Task、Memory / Skill / MCP、Output Service、参考端侧、回放、CLI 和预检。

新阶段目标不是复刻旧 `openaiglass-sdk` 的目录、类名和协议细节，而是让 `audio-chat` 达到老 SDK 对功能开发者的同等可用性：

1. 开发者能按文档安装、同步配置、启动 server、启动参考端侧或回放设备。
2. 开发者能用相似的方式编写短动作 Tool、长流程 Task、设备通讯请求、通知和外部服务能力。
3. 老业务样板能力 `find_object`、`traffic_light`、`navigation`、`search`、`timer` 能迁移到 `audio-chat` 并跑通设备级验收。
4. Python phone mock、glass playback、web-glass、iOS phone 参考端和 ESP32-S3 参考端能支撑功能开发者进入真实联调。
5. mock provider、真实 provider、回放和真机 smoke test 都有稳定验收入口。
6. 文档和公开 API 对开发者友好，不要求理解 SDK 内部服务对象。

本阶段完成后，`audio-chat` 应能作为新项目优先使用的 SDK。旧项目仍可继续使用 `openaiglass-sdk`，但新能力开发应能在 `audio-chat` 中找到等价路径。

## 2. 对齐口径

### 2.1 不追求完全一致

以下内容不要求与老 SDK 完全一致：

1. 不要求保留 `DeviceGroupContext` 名称，`audio-chat` 使用 `UserDeviceContext`。
2. 不要求保留 glass / phone 固定设备类型，`audio-chat` 使用 event/subscription 和 stream。
3. 不要求保留 `/ws_audio`、`/ws_realtime_audio`、`sensor.camera.*` 等旧协议名称，`audio-chat` 使用 event + stream。
4. 不要求业务代码手动注册 Tool / Task，`audio-chat` 默认自动发现。
5. 不要求把 iOS / ESP32 正式端侧工程放入 Python SDK 包内，`audio-chat/endpoints-examples` 只提供参考端侧样例和契约。

### 2.2 必须达到相同开发体验

以下能力必须等价：

| 老 SDK 能力 | audio-chat 对齐目标 |
| --- | --- |
| `openaiglass.config.sync` | `audio-chat.config.sync` 能同步 server、playback、phone mock、web、iOS、ESP32 配置。 |
| `openaiglass.server.run` | `audio-chat.server.run` 支持 app-root、YAML、自动发现和 debug API。 |
| `openaiglass.phone.mock` | `audio-chat.phone.mock` 支持手机任务、视觉帧、执行器和事件日志。 |
| `openaiglass.glass.start --runtime playback` | `audio-chat.playback.glass` 支持触发音频、抓拍、视频流、传感器时间线和执行器断言。 |
| `openaiglass.phone.open` | `audio-chat.ios.open` 或文档化等价命令能打开 iOS 参考端工程。 |
| `openaiglass.glass.start` | ESP32 相关后续目标命令或文档化等价流程能完成 ESP32 配置、构建、烧录、监看。 |
| `openaiglass.sdk.preflight` / `live-check` / `package-check` | `audio-chat.dev.preflight`、`audio-chat.dev.live-check`、`audio-chat.sdk.package-check` 能解释配置、服务、端侧、provider 和 package 状态。 |
| `BaseTool` / `BaseTask` | `audio_chat.BaseTool` / `BaseTask` 保持稳定公开 API。 |
| `context.capture_photo()` | `UserDeviceContext.capture_photo()` 通过控制事件和 `sensor.rgb` stream 获取照片资产。 |
| `context.start_phone_video_link()` | 使用 `UserDeviceContext.configure_stream()` 或更友好的 `start_sensor_stream()` 打开持续 `sensor.rgb` / `sensor.depth` 上传，并由 Task 读取资产。 |
| `context.submit_notification()` | `UserDeviceContext.notify()` 或 `TaskContext.notify()` 进入 Output Service，不直接控制播放器。 |
| `context.mcp(...)` | MCP 只能通过 Tool / Task 间接调用，提供对开发者友好的 wrapper。 |
| Agent Memory | Memory Service 注入模型上下文，并暴露 `memory_search` / `manage_memory` Tool。 |
| 工具调用前置播报 | Tool 声明 progress message，Output Service 负责缓存、实时 TTS 和仲裁。 |
| Task 定时调度 | `TaskContext.schedule_event()` 和任务恢复、取消、超时可用。 |

## 3. 当前基线

进入本阶段前必须满足：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py all --keep-going \
  --report runs/acceptance/old-sdk-parity-baseline.json
```

如果真实 provider key 可用，还必须额外运行：

```bash
cd audio-chat
DASHSCOPE_API_KEY=... uv run python -m pytest tests/integration/test_dashscope_providers.py -q
```

基线验收通过后，后续所有线路都应在 `scripts/acceptance_check.py` 中新增独立 lane，最终进入 `all`。

## 4. 新增验收 Lane

建议新增以下 lane：

```text
old-sdk-parity-api
old-sdk-parity-cli
old-sdk-parity-playback
old-sdk-parity-phone
old-sdk-parity-esp32
old-sdk-parity-capabilities
old-sdk-parity-voice
old-sdk-parity-provider
old-sdk-parity-docs
old-sdk-parity-release
```

每条 lane 需要能独立运行：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py <lane> \
  --report runs/acceptance/<lane>.json
```

最终发布候选必须运行：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py all --keep-going \
  --report runs/acceptance/old-sdk-parity-full.json
```

## 5. 并行线路 A：公开 API 与开发者上下文对齐

目标：让业务开发者只依赖 `audio_chat` 顶层公开 API 和 `UserDeviceContext`，不接触 Control Service、Stream Service、Asset Service、Output Service 等内部对象。

写入范围：

```text
audio-chat/server-python/audio_chat/__init__.py
audio-chat/server-python/audio_chat/tools.py
audio-chat/server-python/audio_chat/tasks.py
audio-chat/server-python/audio_chat/context.py
audio-chat/tests/test_public_api_parity.py
audio-chat/tests/test_user_device_context_developer_api.py
audio-chat/tests/acceptance/test_no_internal_service_usage_contract.py
```

任务清单：

1. 冻结顶层公开导入：
   - `BaseTool`
   - `ToolContext`
   - `ToolResult`
   - `BaseTask`
   - `TaskContext`
   - `TaskEvent`
   - `TaskRef`
   - `UserDeviceContext`
   - `ArtifactRef`
   - `CapabilityTrace` 或等价 trace 对象
2. 补齐 `UserDeviceContext` 的开发者友好 API：
   - `capture_photo(...)`
   - `latest_asset(stream_type=...)`
   - `request_asset(stream_type=..., timeout_seconds=...)`
   - `configure_stream(stream_type=..., mode=..., rate_hz=..., duration_seconds=...)`
   - `watch_assets(stream_type=..., since=...)`
   - `notify(text=..., priority=..., ttl_seconds=...)`
   - `get_devices()`，只返回设备快照，不暴露可变连接。
3. 所有 API 底层只能发布 event 或读写 stream / asset，不新增 RPC。
4. 禁止开发者代码直接按 `device_id` 点对点发送事件。
5. 为老 SDK 常用写法提供迁移表，但不强制提供同名别名。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py old-sdk-parity-api
```

通过条件：

1. 示例 Tool / Task 只从 `audio_chat` 顶层导入。
2. `capture_photo`、持续 sensor stream、通知、资产读取都有测试。
3. 测试扫描 examples 和 migration templates，不允许 import 内部 service 模块。

## 6. 并行线路 B：CLI 与本地开发流程对齐

目标：让开发者日常使用流程接近老 SDK：安装、同步配置、启动 server、打开 iOS、启动 phone mock、启动 playback、构建 ESP32、预检、live-check、package-check。

写入范围：

```text
audio-chat/pyproject.toml
audio-chat/server-python/audio_chat/cli/
audio-chat/server-python/audio_chat/preflight.py
audio-chat/examples/basic-app/
audio-chat/examples/minimal/
audio-chat/tests/test_cli_old_sdk_parity.py
audio-chat/tests/test_live_check.py
audio-chat/tests/test_config_sync_multidevice.py
audio-chat/tests/test_package_check_release_inputs.py
```

任务清单：

1. 补齐或稳定 CLI：
   - `audio-chat.config.sync`
   - `audio-chat.server.run`
   - `audio-chat.server.start`
   - `audio-chat.server.stop`
   - `audio-chat.server.logs`
   - `audio-chat.phone.mock`
   - `audio-chat.playback.glass`
   - `audio-chat.web.open`
   - `audio-chat.ios.open`
   - `audio-chat.ios.build-sim`
   - `audio-chat.esp32.config`
   - `audio-chat.esp32.build`
   - `audio-chat.esp32.flash`
   - `audio-chat.esp32.monitor`
   - `audio-chat.dev.preflight`
   - `audio-chat.dev.live-check`
   - `audio-chat.sdk.package-check`
2. 所有命令必须支持 `--help`。
3. 真机相关命令没有本地依赖时必须给出明确诊断，不允许假成功。
4. `config.sync` 必须同步：
   - server YAML
   - phone mock YAML
   - glass playback YAML
   - web-glass YAML
   - iOS `AppConfig.json`
   - ESP32 `local.env`
5. `live-check` 必须检查：
   - server health
   - debug devices
   - 最近注册失败
   - 最近 playback 结果
   - provider key 和 fallback 状态
   - iOS / ESP32 配置是否与 server 一致

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py old-sdk-parity-cli
```

通过条件：

1. README 中所有非 roadmap 命令都能运行或在缺依赖时结构化失败。
2. `config.sync` 后各端 `server_url`、`user_id`、`device_id`、token 一致且不冲突。
3. `live-check` 能在 server 未启动和已启动两种状态下给出可操作诊断。

## 7. 并行线路 C：设备级回放能力对齐

目标：让 `audio-chat.playback.glass` 达到老 `glass-playback` 对功能开发的核心价值：用真实 server 协议回放触发音频、抓拍、视频流、传感器时间线，并记录执行器结果。

写入范围：

```text
audio-chat/endpoints-examples/python-glass/audio_chat_python_glass/playback.py
audio-chat/examples/basic-app/host/glass-playback/
audio-chat/testdata/playback/
audio-chat/testdata/contracts/scenarios/
audio-chat/tests/playback/
audio-chat/tests/acceptance/test_old_sdk_playback_parity.py
```

任务清单：

1. playback 配置支持：
   - `trigger_audio`
   - `sensor.rgb.capture`
   - `sensor.rgb.stream`
   - `sensor.depth.stream`
   - `sensor.imu.timeline`
   - `heading`
   - `location`
   - `actuator.speaker`
   - `actuator.haptic`
2. 支持 wake 后打开音频 stream，连续对话结束后释放 stream。
3. 支持响应 `stream.control.configure.requested`，按固定频率上传 `sensor.rgb` / `sensor.imu`。
4. 支持保存下行 `actuator.speaker` 音频为文件或自动完成播放回执。
5. 支持最小断言 DSL：
   - expected events
   - expected stream types
   - expected asset count
   - expected tool / task event
   - expected output chunks
6. 回放产物必须包含：
   - `events.jsonl`
   - `stream-events.jsonl`
   - `agent-events.jsonl`
   - `tool-events.jsonl`
   - `task-events.jsonl`
   - `assets.jsonl`
   - `output-decisions.jsonl`
   - `actuators.jsonl`
   - `result.json`

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py old-sdk-parity-playback
```

通过条件：

1. `look_around` 类抓拍场景能从 trigger audio 走到 `capture_photo` 工具和资产写入。
2. `continuous_rgb` 类视频场景能按频率上传多帧并由 Task 消费。
3. `timer` 类任务场景能触发通知和下行播放。
4. 失败报告能定位到注册、订阅、stream、asset、agent、tool、task、output 或 actuator。

## 8. 并行线路 D：手机端任务与视觉链路对齐

目标：用 `audio-chat` 的 event + stream 模式替代老 SDK 的 phone task / camera sink 体验，让 `find_object`、`traffic_light` 这类手机视觉能力有清晰实现路径。

写入范围：

```text
audio-chat/endpoints-examples/python-phone-mock/
audio-chat/endpoints-examples/ios-phone/
audio-chat/endpoints-examples/python-phone-mock/audio_chat_python_phone_mock/phone_mock.py
audio-chat/examples/basic-app/capabilities/
audio-chat/tests/test_phone_task_contract.py
audio-chat/tests/test_python_phone_mock_vision_task.py
audio-chat/tests/test_ios_phone_contract.py
audio-chat/tests/acceptance/test_phone_visual_task_playback.py
```

任务清单：

1. 定义 phone task 等价协议，不新增 RPC：
   - server 发布 `control.device.command.requested`
   - payload 表达 `task_type`、`task_id`、输入参数和需要的 stream。
   - phone 端通过事件上报 started / progress / completed / failed。
   - RGB / depth / IMU 数据仍走 stream。
2. Python phone mock 支持：
   - 任务 handler 自动发现。
   - 固定事件脚本。
   - 按帧 processor。
   - 接收 `sensor.rgb` 或从配置读取视频帧。
   - 上报任务事件。
   - 保存事件和帧日志。
3. iOS phone 参考端支持：
   - 注册、心跳、订阅。
   - 接收 command 事件。
   - Swift task registry 样板。
   - 采集或接收 RGB 帧。
   - 上报 task event。
4. 提供开发者样板：
   - `find_object_phone_task`
   - `traffic_light_phone_task`
5. 明确 Python phone mock 与真实 iOS 插件的关系：契约对应，不是代码复用。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py old-sdk-parity-phone
```

通过条件：

1. Python phone mock 能作为独立设备注册并执行视觉任务。
2. iOS 工程至少能通过 contract / simulator build 验收。
3. `find_object` 和 `traffic_light` 样板能通过 phone mock 设备级回放。
4. 文档禁止业务代码绕过 `UserDeviceContext` 直接操作 phone 连接。

## 9. 并行线路 E：ESP32-S3 参考端与真机 smoke 对齐

目标：让 ESP32-S3 参考端达到老 SDK 中“可构建、可烧录、可监看、可连 server、可做基础语音和传感器联调”的水平。

写入范围：

```text
audio-chat/endpoints-examples/esp32-s3/
audio-chat/server-python/audio_chat/cli/esp32.py
audio-chat/docs/esp32-s3-endpoint-bridge.md
audio-chat/tests/test_esp32_config_sync.py
audio-chat/tests/test_esp32_package_manifest.py
audio-chat/tests/acceptance/test_esp32_s3_endpoint_contract.py
```

任务清单：

1. ESP32 配置：
   - WiFi SSID / password。
   - server control URL。
   - stream URL。
   - `user_id`。
   - `device_id`。
   - signed token 或 static token。
   - properties 和 subscriptions。
2. ESP32 固件能力：
   - 控制连接注册和心跳。
   - 控制 WebSocket 重连。
   - wake 后打开 `sensor.mic` stream。
   - 连续对话结束后关闭 stream。
   - 接收 `actuator.speaker` stream 并播放。
   - 播放中 wake-word interrupt 上报。
   - 响应 `sensor.rgb` capture / configure 请求。
   - 上报播放失败、内存不足、stream 失败等诊断事件。
3. CLI：
   - config。
   - build。
   - flash。
   - monitor。
   - build-only。
   - monitor-only。
4. package-check 检查：
   - ESP-IDF 工程文件。
   - sdkconfig defaults。
   - component manifest。
   - README 和 local.env.example。
5. 真机 smoke 可选运行，缺硬件时自动 skip，但不能假成功。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py old-sdk-parity-esp32
```

通过条件：

1. 没有 ESP-IDF 时 package-check 给出缺依赖诊断。
2. 有 ESP-IDF 时能 build。
3. 有串口和真机时能注册、上传音频、接收音频、上报 interrupt。
4. ESP32 文档能指导开发者完成 WiFi、烧录和联调。

## 10. 并行线路 F：老业务能力迁移样板

目标：把老 `openaiglass-for-blind` 中对功能开发者最有价值的业务样板迁移到 `audio-chat`，证明新 SDK 支持同等能力开发。

写入范围：

```text
audio-chat/examples/for-blind-app/
audio-chat/examples/for-blind-app/capabilities/
audio-chat/testdata/for-blind/
audio-chat/tests/acceptance/test_for_blind_capabilities_playback.py
audio-chat/tests/acceptance/test_old_sdk_capability_migration_contract.py
```

建议目录：

```text
examples/for-blind-app/
  README.md
  config/
    server.yaml
  capabilities/
    find_object/
      tool.py
      task.py
    traffic_light/
      tool.py
      task.py
    navigation/
      tool.py
      task.py
      mcp/
    search/
      tool.py
      mcp/
    timer/
      tool.py
      task.py
  host/
    server/
    phone-mock/
    glass-playback/
```

任务清单：

1. `find_object`：
   - 支持一次性抓拍问答。
   - 支持持续 RGB 视觉任务。
   - 支持 phone mock / iOS phone task 上报找到目标。
2. `traffic_light`：
   - 支持启动视觉任务。
   - 支持红绿灯状态事件。
   - 支持任务完成通知。
3. `navigation`：
   - 支持 POI / route 准备 Tool。
   - 支持 AMap mock MCP adapter。
   - 支持导航 Task 状态推进。
   - 支持偏航、接近终点、需要视觉确认等事件样板。
4. `search`：
   - 支持 web search MCP mock。
   - 支持真实 provider 配置缺失时明确 fallback。
5. `timer`：
   - 支持创建、查询、取消和到点通知。
   - 使用 `TaskContext.schedule_event()`。
6. 所有能力必须：
   - 自动发现。
   - 不硬编码 `device_id`。
   - 不直接 import 内部服务。
   - 通过 event + stream 获取设备数据。
   - 有 playback 配置和 testdata。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py old-sdk-parity-capabilities
```

通过条件：

1. 五类样板能力至少各有一个成功路径回放。
2. `find_object`、`traffic_light` 至少覆盖 phone mock 视觉任务。
3. `navigation` 和 `search` 至少覆盖 MCP mock。
4. `timer` 覆盖 schedule、cancel、notify。
5. 回放产物足以解释 Agent、Tool、Task、MCP、asset、output 全链路。

## 11. 并行线路 G：语音主链路、连续对话和工具前置播报

目标：对齐老 SDK 的核心语音体验：半双工、Omni realtime、Text Server ASR/TTS、工具调用、工具前置播报、连续对话关闭、用户打断和播放仲裁。

写入范围：

```text
audio-chat/server-python/audio_chat/agent_core/
audio-chat/server-python/audio_chat/audio_pipeline/
audio-chat/server-python/audio_chat/output/
audio-chat/server-python/audio_chat/tools.py
audio-chat/tests/test_voice_session_modes.py
audio-chat/tests/test_progress_audio.py
audio-chat/tests/test_continuous_dialog_state.py
audio-chat/tests/test_playback_interrupt_policy.py
audio-chat/tests/acceptance/test_voice_old_sdk_parity.py
```

任务清单：

1. 配置语义：
   - `agent.mode=text|realtime|auto`。
   - `voice.server_mode=omni_server|text_server` 等价配置或迁移映射。
   - `voice.conversation_mode`。
   - `voice.session_lifecycle=persistent|per_turn|segment_turn`。
2. Text Server：
   - streaming ASR。
   - text delta。
   - streaming TTS。
   - 工具调用回填。
3. Omni Realtime：
   - persistent session。
   - provider semantic VAD。
   - audio delta 直通。
   - tool call bridge。
   - `response.audio.done` 后关闭 output stream。
4. 连续对话：
   - `control.user.dialog.close.requested`。
   - `voice.turn.ignored` 等价事件，或 `control.audio_session.turn.ignored`。
   - 模型误调用关闭工具的防护。
   - close after reply。
5. 工具前置播报：
   - Tool 声明 `progress_message`。
   - 支持多候选。
   - 支持 cached 和 realtime 两种生成模式。
   - 首输出不是工具调用时不插入提示。
6. 打断：
   - endpoint 上报 wake-word interrupt。
   - server 取消 Agent response。
   - Output Service 根据 priority 和 ttl_seconds 仲裁。
   - 被打断输出按 `drop` / `requeue` 策略处理。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py old-sdk-parity-voice
```

通过条件：

1. mock text 和 mock realtime 都能完整跑通工具调用和输出。
2. 工具前置播报只在模型首输出为 tool call 时触发。
3. 连续对话关闭和 turn ignored 不会错误关闭 persistent realtime session。
4. 播放中打断会产出 cancel / close 事件和 output decision 记录。

## 12. 并行线路 H：真实 Provider 与外部服务稳定性

目标：让真实 ASR、TTS、Text Model、Omni Realtime、MCP 外部服务的行为可诊断、可跳过、可重试，避免开发者被偶发 provider 问题阻塞。

写入范围：

```text
audio-chat/server-python/audio_chat/agent_core/providers.py
audio-chat/server-python/audio_chat/output/service.py
audio-chat/server-python/audio_chat/mcp/
audio-chat/server-python/audio_chat/preflight.py
audio-chat/tests/integration/
audio-chat/tests/test_provider_degradation_policy.py
audio-chat/tests/test_mcp_external_server_smoke.py
```

任务清单：

1. Provider smoke tests：
   - 无 key 时 skip，不失败。
   - 有 key 时跑 ASR、TTS、Text、Omni smoke。
   - 网络超时要输出 provider、model、endpoint、timeout、fallback policy。
2. 稳定性策略：
   - retry。
   - timeout。
   - fallback。
   - 禁止 fallback 时明确失败。
   - 记录 first_text、first_audio、first_tool_call 等指标。
3. MCP：
   - stdio。
   - SSE。
   - Streamable HTTP。
   - 工具 schema 同步。
   - 调用超时和错误封装。
4. preflight：
   - 检查 provider key。
   - 检查 mock fallback 是否启用。
   - 检查 MCP 配置文件存在性。
   - 检查 Skill roots 和 Memory store。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py old-sdk-parity-provider
```

通过条件：

1. `acceptance_check.py all` 不会因为缺真实 key 失败。
2. 有 key 的真实 provider 测试失败时报告可定位原因。
3. 外部 MCP server 缺依赖或启动失败时有结构化错误。
4. preflight 能解释当前环境适合 mock、本地联调还是真实 provider 联调。

## 13. 并行线路 I：文档、迁移指南和开发者样板

目标：让开发者拿到 `audio-chat` 后不需要读架构文档也能开始开发，同时有从老 SDK 迁移的明确路径。

写入范围：

```text
audio-chat/README.md
audio-chat/docs/audio-chat-sdk-architecture.md
audio-chat/docs/phase3-migration-guide.md
audio-chat/docs/old-sdk-parity-development-plan.md
audio-chat/examples/basic-app/
audio-chat/examples/for-blind-app/
audio-chat/examples/migration-templates/
audio-chat/tests/test_docs_old_sdk_parity.py
audio-chat/tests/acceptance/test_docs_current_state_contract.py
```

任务清单：

1. README 重写为开发者入口：
   - 安装。
   - 同步配置。
   - 启动 server。
   - 启动 phone mock。
   - 启动 playback。
   - 打开 web-glass。
   - iOS / ESP32 真机入口。
   - 写 Tool / Task。
   - 跑回放。
   - 看日志产物。
2. 迁移指南：
   - `BaseTool` 迁移。
   - `BaseTask` 迁移。
   - `DeviceGroupContext` 到 `UserDeviceContext`。
   - `capture_photo`。
   - phone video task。
   - MCP adapter。
   - memory。
   - notification。
   - playback config。
3. 能力样板文档：
   - find object。
   - traffic light。
   - navigation。
   - search。
   - timer。
4. 排障文档：
   - 设备未注册。
   - 订阅未匹配。
   - stream 未打开。
   - 没有资产。
   - tool 未注册。
   - task 未恢复。
   - output 被仲裁丢弃。
   - provider fallback。
   - iOS / ESP32 配置不一致。
5. 文档状态不能写“已实现”但没有测试或样板支撑。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py old-sdk-parity-docs
```

通过条件：

1. README 中命令都能被测试解析。
2. 文档提到的公开 API 都可导入。
3. 文档提到的样板目录真实存在。
4. 老 SDK 迁移表覆盖 README 和能力开发指南中的主要开发者入口。

## 14. 并行线路 J：发布候选与包边界

目标：让 `audio-chat` 不只是仓库内可跑，而是能作为 SDK 交付给其他业务项目试用。

写入范围：

```text
audio-chat/pyproject.toml
audio-chat/README.md
audio-chat/CHANGELOG.md
audio-chat/server-python/audio_chat/
audio-chat/endpoints-examples/
audio-chat/scripts/acceptance_check.py
audio-chat/tests/test_release_package.py
audio-chat/tests/test_package_boundary.py
audio-chat/tests/acceptance/test_release_candidate_gate.py
```

任务清单：

1. Python package：
   - wheel build。
   - editable install。
   - isolated venv import。
   - CLI entry points。
   - package data。
2. 包边界：
   - server SDK 不依赖 examples。
   - server SDK 不依赖 endpoints 内部真机工程。
   - endpoints 可以依赖协议契约，但不反向污染 server SDK。
3. 版本和变更：
   - `CHANGELOG.md`。
   - release candidate 标识。
   - 当前不兼容点。
4. 端侧源码包检查：
   - iOS 工程文件。
   - ESP32 工程文件。
   - web-glass 静态入口。
   - Python phone mock。
5. 发布前检查：
   - all acceptance。
   - package-check。
   - docs contract。
   - basic app playback。
   - for-blind app playback。

验收命令：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py old-sdk-parity-release
```

通过条件：

1. 新建临时项目安装 wheel 后可以 `import audio_chat`。
2. 新建临时项目可以复制 `examples/basic-app` 并跑通 playback。
3. `audio-chat.sdk.package-check` 输出 release candidate 报告。
4. 包内不包含本地私密配置和运行产物。

## 15. 推荐并行顺序

第一批建议并行推进：

1. A 公开 API 与开发者上下文对齐。
2. B CLI 与本地开发流程对齐。
3. C 设备级回放能力对齐。
4. I 文档、迁移指南和开发者样板。

原因：这四条决定开发者是否能开始迁移和自测。

第二批建议并行推进：

1. D 手机端任务与视觉链路对齐。
2. E ESP32-S3 参考端与真机 smoke 对齐。
3. F 老业务能力迁移样板。
4. G 语音主链路、连续对话和工具前置播报。

原因：这四条决定 `audio-chat` 是否能覆盖老 SDK 的核心业务能力。

第三批建议推进：

1. H 真实 Provider 与外部服务稳定性。
2. J 发布候选与包边界。

原因：这两条决定是否能从“内部可用”进入“可交给外部开发者试用”。

## 16. 本阶段完成定义

本阶段完成时，应满足：

1. `examples/basic-app` 和 `examples/for-blind-app` 都能按 README 从零启动和回放。
2. `find_object`、`traffic_light`、`navigation`、`search`、`timer` 五类能力都有 `audio-chat` 版本样板。
3. Tool / Task 开发者只使用 `audio_chat` 公开 API。
4. phone mock 能跑视觉任务。
5. glass playback 能跑触发音频、抓拍、持续 RGB、IMU 和执行器断言。
6. iOS phone 参考端能构建或通过 simulator / contract 验收。
7. ESP32-S3 参考端能 package-check，具备真机 smoke 路径。
8. mock provider 全链路稳定。
9. 真实 provider 缺 key 不失败，有 key 时能 smoke test。
10. `audio-chat.dev.live-check` 能解释 server、设备、provider、配置和最近回放状态。
11. `audio-chat.sdk.package-check` 能验证 Python 包、iOS 参考端、ESP32 参考端和 web 参考端输入。
12. 文档中所有“已实现”都有测试、样板或验收报告支撑。

最终验收：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py all --keep-going \
  --report runs/acceptance/old-sdk-parity-full.json
uv run audio-chat.sdk.package-check \
  --report runs/acceptance/old-sdk-parity-package-check.json
uv run audio-chat.dev.preflight \
  --config examples/for-blind-app/config/server.yaml \
  --report runs/acceptance/old-sdk-parity-preflight.json
uv run audio-chat.playback.glass \
  --config examples/for-blind-app/host/glass-playback/look-around.yaml
```

如果有真实 provider key：

```bash
cd audio-chat
DASHSCOPE_API_KEY=... uv run python -m pytest tests/integration/test_dashscope_providers.py -q
```

如果有 iOS / ESP32 真机条件：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py old-sdk-parity-phone
uv run python scripts/acceptance_check.py old-sdk-parity-esp32
```

## 17. 明确不阻塞本阶段的事项

以下事项不作为本阶段阻塞项，但需要在 roadmap 中保留：

1. 生产级多租户管理后台。
2. 跨机器高可用 Task Engine。
3. iOS 二进制 XCFramework 或 Swift Package binaryTarget 发布。
4. ESP-IDF component registry 正式发布。
5. 公网 / NAT peer-link 治理。
6. 真实地图服务生产配额、熔断和费用治理。
7. 多 active device set 生产策略。
8. 端侧 AEC 算法质量本身。

这些能力可以并行探索，但不能替代本阶段的老 SDK 可用性对齐目标。
