# 跨端设备直连视频任务开发计划

## 1. 计划目标

本文基于 [跨端设备直连视频任务设计](peer-video-link-task-design.md)，把找物和红绿灯任务从“server 请求单帧图片并 mock”推进到“server Task 编排 browser-glass 与 Python phone 建立视频连接，phone 端逐帧 YOLO mock 处理并回报状态”。

本轮开发目标：

1. server Task 使用 `TaskContext.devices.commands.start()` 编排 phone receiver 和 glass sender。
2. Python phone 实现 `peer.video.receiver.start`，显示视频回显，逐帧 fork YOLO mock，并回报状态。
3. browser-glass 实现 `peer.video.sender.start`，向 phone receiver 发送 JPEG 帧。
4. 端侧状态仍使用现有 `command.accepted/progress/completed/failed`，不新增系统级协议。
5. SDK 或参考端提供状态回报 helper，开发者只定义状态名、业务 payload 和结果 schema。
6. 用单元测试、端侧契约测试和本地联调流程覆盖关键链路。

## 2. 开发边界

### 2.1 本计划会改

| 范围 | 目录 / 文件 | 说明 |
| --- | --- | --- |
| for-blind Task | `examples/for-blind-app/audio-server/capabilities/tasks.py` | 改造 `find_object_task` 和 `traffic_light_task` 的启动、状态消费、取消逻辑。 |
| Python phone | `examples/dev-support/devices/python-phone/audio_chat_python_phone_mock/` | 增加 peer receiver、状态 reporter、YOLO mock、超时和关闭处理。 |
| browser-glass | `examples/dev-support/devices/browser-glass/index.html` | 增加 peer sender 命令处理、帧采集、发送和 stop。 |
| 设备配置 | `examples/dev-support/devices/browser-glass/device.audio-chat.yaml`、`examples/dev-support/devices/python-phone/phone.preview.yaml` | 增加 `device_role`、peer video properties 和默认 user 对齐。 |
| 测试 | `audio-server/tests/`、`examples/for-blind-app/tests/`、`examples/dev-support/tests/` | 覆盖 Task 编排、状态回报、端侧 receiver/sender 行为。 |
| 文档 | `examples/for-blind-app/docs/devices/`、`examples/dev-support/devices/python-phone/README.md`、`examples/dev-support/devices/browser-glass/README.md` | 补充启动、观察点和端侧开发说明。 |

### 2.2 本计划不改

1. 不迁移真实 YOLO 模型。
2. 不新增独立于 `command.*` 的控制协议。
3. 不把视频帧传回 server 做推理。
4. 不强制 WebRTC；首版使用简单可调试的局域网 WebSocket/JPEG 帧通道即可。
5. 不把 `RemoteTaskReporter` 当作跨语言协议对象；它只是参考端 helper。

## 3. 阶段拆分

## Phase 0：准备和现状清理

目标：确认当前基础能力、测试入口和设备配置，避免后续实现时混淆旧 phone task 和新 peer video task。

改动：

1. 检查 `find_object_task`、`traffic_light_task` 当前 schema，标记 `mock_found`、`mock_confidence`、`mock_state` 为过渡字段。
2. 检查 Python phone 当前 `phone.preview.yaml` 是否和 browser-glass 使用同一 `user_id`。
3. 检查 browser-glass 注册 payload 是否带 `properties`，并准备增加 `device_role=glass`。
4. 检查 Python phone 是否能长驻运行并接收 control event。

验收：

```bash
uv run python -m pytest examples/for-blind-app/tests/test_app_name_launch.py -q
uv run python -m pytest audio-server/tests/test_memory_service.py -q
```

完成标准：

1. 明确当前相关测试是否能跑。
2. 记录任何与本计划无关的既有失败，例如缺失 `audio_chat_python_glass` 包。

## Phase 0.5：生命周期和释放边界补齐

目标：解决真实联调中“设备已经退出但 Task 仍在等待”和“没有可用绑定设备难排查”的问题。

改动：

1. SDK command runtime 在下发命令前登记 command 与目标设备。
2. 控制 WebSocket 断开或心跳超时时，把该设备上未完成的 command 标记为 `command.failed`。
3. Task 在 phone completed 后主动 stop glass sender；失败、取消时 stop 已启动端侧。
4. Python phone 退出时停止所有 peer receiver，释放本地 WebSocket 端口。
5. Python phone receiver 在 sender WebSocket 断开时结束本次 receiver；无帧断开按 failed 上报。
6. browser-glass 在 peer WebSocket error/close、控制连接断开和页面关闭时停止 sender。
7. 设备选择失败时在错误 details 中带上当前在线设备摘要，便于定位 user_id、device_role 或绑定问题。

验收：

```bash
uv run python -m pytest examples/for-blind-app/tests/test_peer_video_tasks.py -q
uv run python -m pytest examples/dev-support/tests/python_phone/test_peer_video_receiver.py -q
uv run python -m pytest examples/dev-support/tests/test_browser_device_example.py -q
```

完成标准：

1. phone/glass 任一端离线都能让 Task 快速进入 failed，而不是等完整业务 timeout。
2. phone 端退出后不遗留 peer receiver 端口。
3. browser 端 sender 的 timer 和 WebSocket 在 stop/error/unload 时都被释放。

## Phase 1：端侧状态回报 helper

目标：让端侧代码不用手写重复的 `command.progress` JSON，同时保持跨语言协议简单。

### 1.1 Python phone 增加 `RemoteTaskReporter`

建议新增文件：

```text
examples/dev-support/devices/python-phone/audio_chat_python_phone_mock/remote_task.py
```

核心对象：

```python
@dataclass(frozen=True)
class RemoteCommand:
    command_id: str
    command: str
    user_id: str
    session_id: str
    params: dict


class RemoteTaskReporter:
    async def accepted(self, *, message: str = "", data: dict | None = None) -> None: ...
    async def progress(self, status: str, *, message: str = "", data: dict | None = None, metrics: dict | None = None) -> None: ...
    async def completed(self, *, result: dict, message: str = "") -> None: ...
    async def failed(self, *, message: str, error_code: str = "remote_task_failed", data: dict | None = None) -> None: ...
```

实现要求：

1. 从 `command.requested` payload 解析 `command_id` 和 `command`。
2. 每次上报都自动带 `command_id`、`command`、`peer_session_id`、`task_type`、`role`、`status/message/data/metrics`。
3. 本地用 `logging` 打印结构化日志，日志字段包含 `command_id`、`peer_session_id`、`status`。
4. 通过现有控制连接发送 `command.accepted/progress/completed/failed`。
5. 对 `status` 为空、payload 不可 JSON 序列化等情况抛出明确异常或转成 `failed`。

### 1.2 server 侧 CommandEvent 兼容增强

检查 `CommandResultBroker.record()` 是否把端侧 payload 中的 `status`、`message`、`data`、`metrics` 原样保留到 `CommandEvent.data`。如果已有能力足够，不改 SDK；如果缺少日志字段，再做最小增强。

验收测试：

```text
examples/dev-support/tests/python_phone/test_remote_task_reporter.py
```

测试目标：

1. `accepted()` 发送 `command.accepted`。
2. `progress()` 发送 `command.progress`，payload 带 status/data/metrics。
3. `completed()` 发送 `command.completed`，payload 带 result。
4. `failed()` 发送 `command.failed`。
5. 日志包含 `peer_session_id` 和 `status`。

完成标准：

1. Python phone 后续 handler 只调用 reporter，不直接拼 `command.progress`。
2. 非 Python 端侧仍可按文档直接发送等价控制事件。

## Phase 2：Python phone peer receiver

目标：Python phone 能作为视频接收端，收到 browser-glass JPEG 帧后显示回显、逐帧 YOLO mock，并在 30 秒后上报 mock 结果。

### 2.1 新增 peer video receiver

建议新增：

```text
examples/dev-support/devices/python-phone/audio_chat_python_phone_mock/peer_video.py
examples/dev-support/devices/python-phone/audio_chat_python_phone_mock/vision_mock.py
```

`peer_video.py` 职责：

1. 处理 `peer.video.receiver.start`。
2. 打开本地 WebSocket server，例如 `ws://<phone-ip>:19081/peer-video/<peer_session_id>`。
3. 通过 reporter 上报：
   - `accepted`
   - `progress(status="peer.receiver.starting")`
   - `progress(status="peer.receiver.ready", data={"receiver": ...})`
4. 收到首帧后上报 `peer.video.first_frame`。
5. 每帧更新预览窗口或最新帧缓存。
6. 每帧调用 `fork_yolo_mock(frame)`。
7. 关闭按钮或 timeout 后生成 mock result，并 `completed(result=...)`。
8. 收到 stop 后关闭 receiver 和预览状态。

`vision_mock.py` 职责：

1. 提供 `fork_yolo_mock(frame, purpose, object_name)`。
2. 每帧打印 `yolo.mock.frame_processed`。
3. 找物默认返回 label=`object_name`、confidence=0.76。
4. 红绿灯默认返回 state=`green`、can_cross=true。

### 2.2 关闭和超时

配置新增：

```yaml
peer_video:
  enabled: true
  listen_host: "0.0.0.0"
  listen_port: 19081
  timeout_seconds: 30
  close_button_enabled: true
  yolo_mock:
    enabled: true
    per_frame: true
```

关闭规则：

| 触发 | phone 上报 |
| --- | --- |
| 用户点击关闭 | `progress(status="peer.video.closed_by_user")` 后 `completed(result=mock_result)` |
| 30 秒超时 | `progress(status="peer.video.timeout")` 后 `completed(result=mock_result)` |
| server stop | 停止 receiver，返回 `completed(result={"stopped": true})` |
| receiver 异常 | `failed(message=...)` |

验收测试：

```text
examples/dev-support/tests/python_phone/test_peer_video_receiver.py
```

测试目标：

1. `peer.video.receiver.start` 返回 `peer.receiver.ready`。
2. WebSocket 收到一帧后保存最新帧。
3. 每帧触发一次 YOLO mock。
4. timeout 后返回找物 mock result。
5. stop 后释放 receiver。

完成标准：

1. 不启动 server 也可以用单元测试验证 receiver。
2. phone 端控制台能看到逐帧日志。

## Phase 3：browser-glass peer sender

目标：browser-glass 收到 `peer.video.sender.start` 后连接 phone receiver 并发送 JPEG 帧。

### 3.1 命令处理

改动文件：

```text
examples/dev-support/devices/browser-glass/index.html
```

实现要求：

1. 在 control event 分发中识别 `command.requested`。
2. 当 `payload.command == "peer.video.sender.start"`：
   - 记录 `command_id`
   - 发送 `command.accepted`
   - 读取 `params.receiver.url`
   - 建立 WebSocket 到 phone receiver
   - 发送 `command.progress(status="peer.sender.connecting")`
   - 连接成功后发送 `command.progress(status="peer.sender.connected")`
3. 按 fps 发送 JPEG 帧：
   - 有摄像头时从 video/canvas 抽帧。
   - 没有摄像头但用户选择了图片时，循环发送这张图片。
4. 收到 stop 命令时停止定时器、关闭 WebSocket、释放摄像头。
5. 发送端本地打印：
   - `peer.video.sender.start`
   - `peer.sender.connected`
   - `peer.video.frame.sent`
   - `peer.video.sender.stop`

### 3.2 配置和能力

更新：

```text
examples/dev-support/devices/browser-glass/device.audio-chat.yaml
```

新增 properties：

```yaml
properties:
  device_role: glass
  endpoint.role.glass: true
  peer.video.sender: true
```

验收测试：

如果浏览器端已有 Playwright 测试基础，新增：

```text
examples/dev-support/tests/browser_glass/test_peer_video_sender.py
```

否则先用静态测试：

1. 检查 HTML 中存在 `peer.video.sender.start` handler。
2. 检查 stop handler。
3. 检查 command progress 发送函数。

完成标准：

1. browser-glass 可以在没有真实摄像头时用上传图片循环发帧。
2. phone 能收到帧并显示/保存最新帧。

## Phase 4：server Task peer video 编排

目标：`find_object_task` 和 `traffic_light_task` 不再请求 `sensor.rgb.one()`，而是编排 phone 和 glass 两端远程命令。

### 4.1 抽取 Task mixin/helper

建议在：

```text
examples/for-blind-app/audio-server/capabilities/tasks.py
```

内部新增 helper，先不抽到 SDK：

```python
class PeerVideoTaskMixin:
    async def start_peer_video(...)
    async def wait_status(...)
    async def consume_peer_events(...)
    async def stop_peer_video(...)
```

保留在示例 app 的原因：

1. peer video 仍是 for-blind-app 业务形态。
2. SDK 通用化前需要先用实际任务验证 API。
3. 避免过早把不稳定抽象放进核心包。

### 4.2 找物 Task 改造

目标流程：

```text
on_start
  -> phone peer.video.receiver.start
  -> wait peer.receiver.ready
  -> glass peer.video.sender.start
  -> consume phone/glass events
  -> phone completed(result.type=find_object)
  -> TaskSignal find_object.found
  -> output.say(result.message)
  -> complete
```

输入 schema 调整：

1. 保留 `object_name`。
2. 新增或保留 `timeout_seconds` 表示端侧视频任务超时。
3. `mock_found`、`mock_confidence` 不再面向模型暴露；如果暂时兼容，标为 deprecated，并在 prompt/schema 描述中说明模型不要填写。

### 4.3 红绿灯 Task 改造

目标流程：

```text
on_start
  -> phone peer.video.receiver.start
  -> wait peer.receiver.ready
  -> glass peer.video.sender.start
  -> consume phone/glass events
  -> phone completed(result.type=traffic_light)
  -> TaskSignal traffic_light.green
  -> output.say(result.message, priority="high")
  -> complete
```

输入 schema 调整：

1. 保留 `timeout_seconds`。
2. `mock_state` 不再面向模型暴露；phone mock 决定默认返回 green。

### 4.4 取消逻辑

`on_cancel()`：

1. 如果 glass handle 存在，先 stop glass。
2. 如果 phone handle 存在，再 stop phone。
3. 忽略已断开的单端，但记录 warning。
4. 播报“已停止找物”或“已停止红绿灯识别”。

验收测试：

```text
examples/for-blind-app/tests/test_peer_video_tasks.py
```

测试目标：

1. 找物任务先启动 phone，收到 ready 后再启动 glass。
2. 找物任务收到 phone completed 后完成，并播报 result.message。
3. 红绿灯任务收到 green 后高优先级播报。
4. 任一端 failed 时 stop 另一端并 fail task。
5. cancel 时 stop phone 和 glass。

完成标准：

1. Task 不再直接调用 `context.devices.sensors.rgb.one()`。
2. Task 的单元测试不依赖真实浏览器或真实 phone。

## Phase 5：设备配置和路由约定

目标：让 Task selector 能稳定找到 phone 和 glass，同时不暴露 `device_id` 给业务代码。

改动：

1. `browser-glass/device.audio-chat.yaml` 增加：

```yaml
properties:
  device_role: glass
  endpoint.role.glass: true
  peer.video.sender: true
```

2. `phone.preview.yaml` 增加：

```yaml
user_id: user-browser-glass-001
properties:
  device_role: phone
  endpoint.role.phone: true
  endpoint.compute.vision: true
  peer.video.receiver: true
```

3. 确认 `context.devices.commands.start(selector={"device_role": "phone"})` 可以命中 phone。
4. 确认 `selector={"device_role": "glass"}` 可以命中 browser-glass。

验收测试：

```text
examples/for-blind-app/tests/test_peer_video_device_config.py
```

测试目标：

1. 两个设备配置 user_id 默认一致。
2. browser-glass 有 `device_role=glass`。
3. Python phone preview 有 `device_role=phone`。
4. 两端都声明对应 peer video property。

完成标准：

1. 不通过 `device_id` 做 Task 编排。
2. README 中的联调命令默认能把两端放入同一用户设备组。

## Phase 6：端到端联调和运行产物

目标：真实启动 server、Python phone、browser-glass，完成一次找物和一次红绿灯任务链路验证。

启动顺序：

终端 1：

```bash
uv run audio-chat.server.run --config examples/for-blind-app/audio-server/server.yaml
```

终端 2：

```bash
uv run python -m audio_chat_python_phone_mock --config examples/dev-support/devices/python-phone/phone.preview.yaml
```

终端 3：

```bash
uv run audio-chat.web.open --print-url
```

操作：

1. 在 browser-glass 页面确认 `user_id=user-browser-glass-001`。
2. 选择摄像头或上传一张图片作为帧来源。
3. 发起“帮我找水杯”。
4. 发起“看看红绿灯能不能过”。

必须观察到：

server runs：

1. `command.start.requested peer.video.receiver.start`
2. `command.start.requested peer.video.sender.start`
3. `command.progress status=peer.receiver.ready`
4. `command.progress status=peer.sender.connected`
5. `command.completed result.source=mock`
6. `task.completed`

phone 日志：

1. `peer.video.receiver.start`
2. `peer.video.first_frame`
3. `yolo.mock.frame_processed`
4. `peer.video.timeout` 或 `peer.video.closed_by_user`
5. `vision.mock.result`

browser-glass 日志：

1. `peer.video.sender.start`
2. `peer.sender.connected`
3. `peer.video.frame.sent`
4. `peer.video.sender.stop`

完成标准：

1. phone 端看到视频回显或最新帧文件更新。
2. 每帧 YOLO mock 日志出现。
3. 30 秒内自动返回 mock result，或点击关闭后返回 mock result。
4. 用户听到找物或红绿灯结果播报。

## Phase 7：文档和开发者体验

目标：让开源开发者能理解“自己要定义什么，SDK 支持什么”。

文档更新：

1. `examples/dev-support/devices/python-phone/README.md`
   - 增加 peer receiver 启动说明。
   - 说明 `RemoteTaskReporter` 是 Python 参考端 helper。
   - 说明非 Python 端侧只要发送等价 `command.*` 事件。
2. `examples/dev-support/devices/browser-glass/README.md`
   - 增加 peer sender 和图片循环帧说明。
3. `audio-server/docs/reference/context-api.md`
   - 增加“Task 编排端侧远程命令”的 peer video 示例。
4. `examples/for-blind-app/docs/devices/peer-video-link-task-design.md`
   - 根据实现结果更新实际命令字段、测试命令和限制。

完成标准：

1. 文档不把 `RemoteTaskReporter` 描述成跨语言必需对象。
2. 文档明确状态名由开发者定义，SDK 负责信封、日志、校验、运行产物和 Task 消费入口。

## 4. 建议提交顺序

1. `补充端侧远程任务回报 helper`
2. `实现 Python phone 视频接收和 YOLO mock`
3. `实现 browser-glass peer 视频发送`
4. `改造找物和红绿灯 Task`
5. `补齐 peer video 配置和验收测试`
6. `更新跨端视频联调文档`

每个提交都应能独立说明：

1. 改了哪一层。
2. 对应测试命令。
3. 未完成的真实设备验证边界。

## 5. 风险和处理

| 风险 | 影响 | 处理 |
| --- | --- | --- |
| browser 不能直连 phone WebSocket | 视频无法真正点对点 | 首版允许同局域网调试；必要时加 server relay，但保持 peer command 模型不变。 |
| phone GUI 和 asyncio 冲突 | 视频窗口卡顿或无法退出 | 先复用现有 OpenCV/FrameStore；PySide6 控制台后续单独演进。 |
| 每帧 fork 过多导致堆积 | phone CPU 飙高 | YOLO mock worker 增加并发上限和丢帧策略，只保证日志可观察。 |
| Task 等待状态卡住 | 任务长期 running | 每个等待点加 timeout，失败时 stop 已启动端。 |
| 端侧状态名混乱 | 开源开发者难排查 | SDK helper 打印结构化日志，文档给命名建议，但不强行枚举所有业务状态。 |
| 旧测试仍依赖单帧抓拍 | CI 失败 | 明确拆分旧抓拍 Tool 测试和新 peer video Task 测试，必要时调整旧测试描述。 |

## 6. 最小可交付定义

第一版最小可交付必须满足：

1. `find_object_task` 和 `traffic_light_task` 通过 `commands.start()` 编排 phone/glass。
2. Python phone 能收到 browser-glass 发来的帧。
3. Python phone 每帧打印 YOLO mock 日志。
4. Python phone timeout 或按钮关闭后上报 mock result。
5. server Task 收到 result 后完成并播报。
6. 自动测试覆盖 Task 编排顺序、phone reporter、phone receiver 和配置 selector。
7. README 给出三端启动顺序和观察点。

## 7. 实施记录

### Phase 0：准备和现状清理

- 状态：已完成
- 实现：确认 `find_object_task` / `traffic_light_task` 原实现仍走 `sensor.rgb.one()` 单帧抓拍；`mock_found`、`mock_confidence`、`mock_state` 已在 schema 描述中标记为废弃兼容字段。
- 验证：`uv run python -m pytest examples/for-blind-app/tests/test_app_name_launch.py audio-server/tests/test_memory_service.py -q`，结果 16 passed。
- 风险：既有旧 phone task 测试仍依赖 `find_object_phone_task` / `traffic_light_phone_task`，当前主线已迁移到 peer video Task，需要后续单独清理旧测试口径。

### Phase 1：端侧状态回报 helper

- 状态：已完成
- 实现：新增 `audio_chat_python_phone_mock.remote_task.RemoteCommand` 和 `RemoteTaskReporter`，统一生成 `command.accepted/progress/completed/failed`，自动带 `command_id`、`command`、`peer_session_id`、`task_type`、`role`。
- 验证：`uv run python -m pytest examples/dev-support/tests/python_phone/test_remote_task_reporter.py -q`，通过。

### Phase 2：Python phone peer receiver

- 状态：已完成，GUI 关闭按钮待人工体验验收
- 实现：新增 `peer_video.py` 和 `vision_mock.py`；receiver 打开 `/peer-video/<peer_session_id>` WebSocket，收到二进制 JPEG 帧后触发 `fork_yolo_mock()` 并上报 `peer.video.first_frame`、`peer.video.frame_processed`，timeout 后返回 mock result。
- 验证：`uv run python -m pytest examples/dev-support/tests/python_phone/test_peer_video_receiver.py -q`，通过。
- 风险：当前自动测试验证的是 WebSocket 收帧和 mock result，未做 OpenCV 窗口关闭按钮的人工验收。

### Phase 3：browser-glass peer sender

- 状态：已完成，浏览器真实摄像头待人工体验验收
- 实现：`index.html` 增加 `peer.video.sender.start` / stop 命令处理，连接 phone receiver WebSocket，按 fps 发送摄像头或图片样例 JPEG 帧，并声明 `device_role=glass`、`peer.video.sender=true`。
- 验证：`uv run python -m pytest examples/dev-support/tests/test_browser_device_example.py -q` 覆盖静态契约。

### Phase 4：server Task peer video 编排

- 状态：已完成
- 实现：`find_object_task` 和 `traffic_light_task` 改为通过 `TaskContext.devices.commands.start()` 先启动 phone receiver，再启动 glass sender；完成结果来自 phone `command.completed.result`，取消时按 glass -> phone 顺序 stop。
- 验证：`uv run python -m pytest examples/for-blind-app/tests/test_peer_video_tasks.py -q`，通过。
- 实现备注：控制信令 payload 不使用字段名 `video`，改用 `media_config`，避免 SDK 的媒体字节保护误判。

### Phase 5：设备配置和路由约定

- 状态：已完成
- 实现：`browser-glass/device.audio-chat.yaml` 和 `python-phone/phone.preview.yaml` 对齐 `user_id=user-browser-glass-001`，分别声明 `device_role=glass/phone` 和 peer video properties。
- 验证：`uv run python -m pytest examples/for-blind-app/tests/test_peer_video_device_config.py -q`，通过。

### Phase 6：端到端联调和运行产物

- 状态：待人工验收
- 当前验证：完成了 Task 编排、phone receiver、browser sender 静态契约和配置 selector 的自动化验证。
- 未验证：未在本轮真实启动 server + Python phone + browser-glass 完成摄像头/图片端到端联调，也未形成真实 runs 产物。

### Phase 7：文档和开发者体验

- 状态：已完成
- 实现：更新 Python phone、browser-glass、Context API 和本设计文档，说明 `RemoteTaskReporter` 只是 Python helper，非 Python 端侧可直接发送等价 `command.*` 事件。
- 验证：文档命令随 Phase 1-5 自动测试同步执行。
