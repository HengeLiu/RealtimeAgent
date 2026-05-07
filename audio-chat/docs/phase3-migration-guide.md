# audio-chat Phase 3 业务迁移指南

更新时间：2026-05-07

本文面向从旧 `openaiglass-sdk` 迁移业务能力到 `audio-chat` 的开发人员。目标不是复刻旧目录，而是把能力改写到新的“用户语音会话 + event + stream”协议模型上。

## 1. 迁移边界

`audio-chat` server 只负责设备注册绑定、事件订阅分发、stream 生命周期、资产缓存、Agent Core、Tool / Task 扩展、Output Service、播放仲裁、回放验收和开发者工具链。

端侧继续负责麦克风录音、喇叭播放、唤醒词、AEC、摄像头驱动和本地硬件控制。server 不新增隐藏 RPC，也不直接控制某个端侧硬件。

## 2. 旧 SDK 概念映射

| 旧概念 | audio-chat 概念 | 迁移说明 |
| --- | --- | --- |
| `DeviceGroupContext` | `UserDeviceContext` | 以 `user_id` 的 active device set 为边界，按 capability 和 subscription 选择端侧。 |
| 抓拍工具 | `BaseTool` + `request_asset("sensor.rgb")` | 控制事件只请求采集策略，图片字节必须通过 `sensor.rgb` stream 上传。 |
| 长流程 Task | `BaseTask` + `TaskEventBridge` | Task 只维护 server 侧状态，通过 event 和 stream 驱动端侧。 |
| 语音播报 | `Output Service` + `submit_text()` | 业务只提交文本、优先级和 TTL，不直接写播放器。 |
| 手机任务 | 端侧 capability + subscription | 端侧声明能处理哪些事件，server 不新增 `start_phone_task` RPC。 |
| 媒体帧 | `StreamChunk` | 大字节统一走 stream，不放进控制事件 payload。 |

## 3. 强制约束

1. Tool / Task 只能通过 `UserDeviceContext` 使用设备能力。
2. 不允许硬编码 device_id 做点对点发送。
3. 设备通讯只能使用 event 和 stream，不新增隐藏 RPC。
4. 大字节媒体必须走 stream，控制事件 payload 只放语义、配置、关联 ID 和小型状态。
5. MCP、Skill、Memory 不允许直接持有设备上下文；需要设备能力时必须封装成 Tool 或 Task。
6. 后台任务通知必须进入 Output Service 或 TaskEventBridge，不直接操作播放队列。

## 4. 可复制样板

当前仓库提供三类迁移起点：

| 样板 | 文件 | 适用场景 |
| --- | --- | --- |
| 找物 Tool | `examples/migration-templates/find_object/tool.py` | 一次性请求 RGB 资产，再由模型或视觉处理器分析。 |
| 连续 RGB Task | `examples/migration-templates/continuous_rgb_analyze/task.py` | 通过事件配置连续上传，并通过 `watch_assets()` 消费多帧图片。 |
| 通知 Task | `examples/migration-templates/notification_task/task.py` | 后台任务到点、状态变化或异常提醒。 |

复制到业务 app-root 时，建议目录如下：

```text
my-app/
  capabilities/
    find_object/
      tool.py
    continuous_rgb_analyze/
      task.py
    notification_task/
      task.py
  host/
    server/
      main.py
  config/
    server.yaml
```

`server.yaml` 中打开自动发现后，Tool / Task 不需要改 SDK 内部代码：

```yaml
tools:
  discover:
    enabled: true
    recursive: true
    packages:
      - capabilities
tasks:
  discover:
    enabled: true
    recursive: true
    packages:
      - capabilities
```

## 5. 迁移流程

1. 先跑当前 SDK 基线：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py developer-usability \
  --report runs/acceptance/developer-usability.json
uv run python scripts/acceptance_check.py capability-template-playback \
  --report runs/acceptance/capability-template-playback.json
```

2. 从 `examples/migration-templates` 复制最接近的样板。
3. 把旧代码中的直接设备调用改为 `context.devices.publish_event()`、`request_asset()`、`watch_assets()`、`open_output_stream()` 或 `submit_text()`。
4. 把图片、音频、视频等大字节移到 `sensor.*` 或 `actuator.*` stream。
5. 为能力补一个独立回放或契约测试。
6. 跑 H 线路文档契约验收：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py next-docs-contract \
  --report runs/acceptance/next-docs-contract.json
```

## 6. 联调观察点

迁移后的能力至少观察这些产物：

1. `runs/audio-chat/.../events.jsonl`：注册、订阅匹配、任务状态和控制事件。
2. `runs/audio-chat/.../stream-events.jsonl`：输入和输出 stream 生命周期。
3. `runs/audio-chat/.../assets.jsonl`：`sensor.rgb` 等资产缓存引用。
4. `runs/audio-chat/.../tool-events.jsonl`：Tool 入参、结果和错误。
5. `runs/audio-chat/.../task-events.jsonl`：Task 状态、事件和通知决策。
6. `runs/audio-chat/.../output-decisions.jsonl`：Output Service 和播放仲裁结果。
7. `runs/audio-chat/.../result.json`：回放最终摘要。

如果这些产物缺失，应优先补回放和记录器，而不是只看日志文本。
