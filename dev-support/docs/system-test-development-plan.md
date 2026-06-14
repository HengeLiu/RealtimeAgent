# python-playback-glass 系统测试开发计划

更新时间：2026-05-12

文档状态：开发计划。本文替代此前“SDK 内部 system_test 框架”的思路，明确下一阶段要实现的是 `dev-support/devices/python-playback-glass`，即一个通过真实协议和 server 对话的眼镜回放端侧。

## 1. 总体目标

开发一套系统级集成测试能力，但实现形态必须是端侧设备：

1. 支持从 `browser-glass` 手动测试结果生成 Case YAML 草稿。
2. 支持 `python-playback-glass` 从 Case YAML 自动回放 `testdata/audio-sample` 和 `testdata/image-sample`。
3. 回放设备通过 `/ws/control` 和 `/ws/stream` 与 server 对话。
4. 支持检查事件、stream、Tool、资产、输出和系统错误。
5. 支持输出结构化 `report.json`。
6. 支持被 pytest 或本地命令调用，但 pytest 不直接操作 server 内部对象。

核心验收标准：对 server 来说，`python-playback-glass` 只是一个普通设备；server 不需要知道它是测试端侧。

## 2. 架构边界

必须遵守：

1. 新能力放在 `dev-support/devices/python-playback-glass/`。
2. Case、suite、report 和 recorder 都属于 dev-support 端侧工具，不属于 `realtime_agent` SDK。
3. 回放设备只通过真实 HTTP/WebSocket 协议连接 server。
4. 断言可以读取 server runs 产物，但不能通过 server 内部对象拿结果。
5. pytest 只负责启动 server、启动回放设备、检查报告。

明确禁止：

1. 不新增 `agent-server/realtime_agent/system_test/`。
2. 不新增 `realtime-agent.system-test.run` 或 `realtime-agent.system-test.record`。
3. 不在回放设备里实例化 `RealtimeAgentApp` 或 `RealtimeAgentConfig`。
4. 不直接调用 `ToolGateway`、`Tool Run 运行时`、`OutputService`、`AssetService`。
5. 不直接调用 `register_device()`、`publish_control_event()`、`open_input_stream()`、`write_input_chunk()`、`stream_service.close_stream()`。
6. 不把 in-process playback 作为系统测试主路径。

## 3. 推荐目录

```text
dev-support/devices/python-playback-glass/
  README.md
  device.realtime-agent.yaml
  playback.glass.yaml
  realtime_agent_python_playback_glass/
    __init__.py
    __main__.py
    cli.py
    case_schema.py
    recorder.py
    runner.py
    device.py
    protocol_client.py
    assertions.py
    report.py
  cases/
    smoke/
    draft/
  suites/
    smoke.yaml
  reports/

dev-support/unit-tests/python_playback_glass/
  test_case_schema.py
  test_recorder.py
  test_protocol_client.py
  test_smoke_suite.py
```

命令入口第一阶段使用 module 形式，避免污染 SDK CLI：

```bash
uv run python -m realtime_agent_python_playback_glass run --suite dev-support/devices/python-playback-glass/suites/smoke.yaml
uv run python -m realtime_agent_python_playback_glass record --runs-root examples/simple-agent-server/runs ...
```

后续如果需要 pyproject script，也应命名为 dev-support 端侧工具，例如 `realtime-agent.playback-glass.run`，不能使用 `realtime-agent.system-test.*`。

## 4. 阶段拆分

### 阶段 0：定位校准和现有能力盘点

目标：确认现有 `python-glass` 中哪些代码可借鉴，哪些必须废弃。

任务：

1. 审核 `dev-support/devices/python-glass/realtime_agent_python_glass/playback.py`。
2. 标记只能借鉴的 in-process 实现，例如直接使用 `RealtimeAgentApp` 的部分。
3. 标记可以迁移的网络协议实现，例如 `/ws/control`、`/ws/stream`、StreamChunk 编解码、输出回执。
4. 确认 `testdata/audio-sample/` 文件路径和 WAV 格式。
5. 确认 `testdata/image-sample/` 图片作为 `sensor.rgb` fixture 的上传格式。

验收：

1. 文档列出可复用网络协议代码清单。
2. 文档列出禁止复用的 server 内部调用清单。
3. 没有新增 SDK 内部目录。

### 阶段 1：端侧骨架和真实协议注册

目标：先实现一台能注册并保持控制连接的回放眼镜。

任务：

1. 新增 `python-playback-glass` 目录和 README。
2. 新增 `device.realtime-agent.yaml`，声明 `sensor.rgb` 和 `vibrator` 等能力。
3. 实现 `protocol_client.py`：
   - 连接 `/ws/control`
   - 发送 `control.device.register.requested`
   - 接收 `control.device.registered`
   - 定期发送 heartbeat
4. 实现基础 CLI：
   - `run --server-url ... --case ...`
   - `record --runs-root ...`
5. 补端侧静态测试，确保没有导入 `realtime_agent.app.RealtimeAgentApp`。

验收：

```bash
uv run python -m realtime_agent_python_playback_glass run \
  --server-url http://127.0.0.1:8765 \
  --case dev-support/devices/python-playback-glass/cases/smoke/register_only.yaml
```

预期：

1. server `/api/debug/devices` 能看到 `python-playback-glass`。
2. runs 中出现设备注册事件。
3. 代码搜索确认没有 `RealtimeAgentApp`、`ToolGateway`、`Tool Run 运行时` 等内部依赖。

### 阶段 2：Case Schema 和最小回放 Case

目标：定义 Case 格式，并跑通一条音频回放 Case。

任务：

1. 新增 `case_schema.py`。
2. Case 中描述 device、inputs、expect。
3. 新增 `who_are_you.yaml`。
4. 回放设备收到 `control.audio_session.open.requested` 后发送 `control.audio_session.opened`。
5. 通过 `/ws/stream` 上传 `sensor.mic` WAV chunk。
6. 接收 `actuator.speaker` chunk，并回执 `stream.output.started/finished/closed`。

验收：

```bash
uv run python -m realtime_agent_python_playback_glass run \
  --server-url http://127.0.0.1:8765 \
  --case dev-support/devices/python-playback-glass/cases/smoke/who_are_you.yaml \
  --report runs/python-playback-glass/who_are_you/report.json
```

最小断言：

1. Case 加载成功。
2. server runs 中出现 `sensor.mic` stream。
3. 回放设备收到 speaker 输出 chunk。
4. 设备按协议回执输出关闭事件。
5. `report.json` 写出。

### 阶段 3：视觉 fixture 和能力请求响应

目标：支持 server 请求 `sensor.rgb` 时，回放设备上传图片 fixture。

任务：

1. 监听 `stream.control.open.requested`。
2. 当 `stream_type=sensor.rgb` 时，从 Case fixture 读取图片。
3. 通过 `/ws/stream` 上传图片 bytes。
4. 发送 `stream.input.opened` 和 `stream.input.closed`。
5. 新增 `look_front.yaml`。

验收：

1. `look_front` Case 触发 `capture_photo`。
2. server runs 中出现 `sensor.rgb` stream。
3. `assets.jsonl` 中出现 `sensor.rgb` 资产。
4. speaker 输出存在。

### 阶段 4：断言和报告

目标：让 Case 能表达系统层预期行为。

任务：

1. 实现 `events.includes`。
2. 实现 `streams.includes`。
3. 实现 `tools.called`。
4. 实现 `tasks.signals.includes`。
5. 实现 `assets.<stream_type>.min_count`。
6. 实现 `output.min_audio_chunks`。
7. 实现 `errors.disallow_system_error`。
8. 实现 `model_request.tools.includes`。
9. 输出 `report.json`。

验收：

1. 故意写错工具名时，报告指出 `tools.called` 缺失。
2. 故意写错 stream 类型时，报告指出 `streams.includes` 缺失。
3. `report.json` 包含 Case id、失败断言、runs_dir 和摘要。

### 阶段 5：Recorder 和 browser-glass 录制入口

目标：降低手写 Case 成本。

任务：

1. `python-playback-glass record` 从 runs 产物生成 Case 草稿。
2. 支持 `--runs-root --user-id --device-id --session-id`。
3. 支持 `--audio` 和 `--image stream_type=path`。
4. 读取 `events.jsonl`、`stream-events.jsonl`、`tool-events.jsonl`、`tool-runs.jsonl`、`assets.jsonl`、`model-request.json`。
5. 过滤动态字段。
6. `browser-glass` 页面增加录制状态区，并生成 `python-playback-glass record` 命令。

示例：

```bash
uv run python -m realtime_agent_python_playback_glass record \
  --runs-root examples/simple-agent-server/runs \
  --user-id user-browser-glass-001 \
  --device-id dev-browser-glass-001 \
  --audio testdata/audio-sample/看一下我前面有什么.wav \
  --image sensor.rgb=testdata/image-sample/刚子看电脑.jpeg \
  --out dev-support/devices/python-playback-glass/cases/draft/look_front.yaml
```

验收：

1. 从一次 browser-glass 手动测试产物生成 YAML。
2. 生成 YAML 不包含 `event_id`、`timestamp_ms`、`stream_id`、`asset_id`。
3. 生成 YAML 能被 `python-playback-glass run` 加载。
4. 生成 YAML 回放后至少通过基础断言。

### 阶段 6：Suite 和 pytest 集成

目标：支持“每次改代码后跑一组系统 Case”。

任务：

1. 新增 suite YAML。
2. `python-playback-glass run` 支持 `--suite`。
3. 支持 `--fail-fast` 和 `--keep-runs`。
4. pytest 测试启动真实 server，再以子进程或异步任务启动 `python-playback-glass`。
5. pytest 不直接调用 runner 函数操作 server 内部对象，只检查 CLI 退出码和 report。

示例：

```yaml
id: smoke
name: 开发冒烟系统测试
cases:
  - dev-support/devices/python-playback-glass/cases/smoke/who_are_you.yaml
  - dev-support/devices/python-playback-glass/cases/smoke/look_front.yaml
```

验收：

```bash
uv run python -m realtime_agent_python_playback_glass run \
  --suite dev-support/devices/python-playback-glass/suites/smoke.yaml \
  --server-url http://127.0.0.1:8765 \
  --report runs/python-playback-glass/smoke/report.json

uv run python -m pytest dev-support/unit-tests/python_playback_glass -q
```

## 5. 首批建议 Case

优先选择 mock provider 下稳定的 Case：

| Case | 音频 | 是否需要图片 | 主要断言 |
| --- | --- | --- | --- |
| `who_are_you` | `你是谁呀.wav` | 否 | speaker 输出、无系统错误 |
| `query_device_state` | `帮我查一下我眼镜的状态.wav` | 否 | 调用设备状态工具 |
| `look_front` | `看一下我前面有什么.wav` | 是 | 调用 `capture_photo`、产生 `sensor.rgb` asset |
| `timer_1min` | `一分钟后提醒我.wav` | 否 | 启动 timer task 或输出相关 task 信号 |
| `memory_name` | `我叫文刀文字的文刀锋的刀.wav` | 否 | 写入 memory 或产生可观察消息 |

如果某些 Case 依赖尚未稳定的业务工具，先放到 `acceptance`，不要放进每次都跑的 `smoke`。

## 6. 实现注意点

1. 所有新增 Python 类、函数和测试 docstring 使用中文，说明目标、方法、预期结果。
2. Case YAML 只引用媒体文件路径，不内嵌媒体字节。
3. 录制器默认生成宽松断言，人工确认后再加严格断言。
4. 回放设备必须只通过控制事件和 stream chunk 与 server 对话。
5. 真实 provider lane 只做链路断言，不逐字断言模型回复。
6. 失败报告要指向 runs 目录，方便继续排查。
7. `reports/`、临时 runs 和录制草稿如果会产生大量文件，应确认 `.gitignore` 覆盖。
8. 不把真实用户音频、图片或视频自动复制进可提交目录。
9. 新 CLI 需要同步 dev-support README 和 browser-glass 录制入口说明。
10. 每个阶段都更新实施记录，但实施记录必须写真实命令结果，不能写设计预期。

## 7. 完成标准

第一阶段完成标准：

1. `python-playback-glass` 可以通过真实 WebSocket 注册到 server。
2. 可以用命令跑至少 2 个系统 Case。
3. 至少 1 个 Case 使用音频样例。
4. 至少 1 个 Case 使用图片样例。
5. 每个 Case 都生成独立 result 和统一 report。
6. 故意破坏 Case 断言时能得到明确失败原因。
7. 从一次 browser-glass 手动 runs 产物能生成 Case 草稿。

第二阶段完成标准：

1. browser-glass 页面可以辅助录制 Case。
2. smoke suite 可以通过 pytest 一条命令运行。
3. 多设备 Case 能验证 RGB 从眼镜端转发到 phone preview。
4. Tool Run Case 能验证 `tool-runs.jsonl`。
5. 文档、CLI、示例 Case 和测试结果一致。

## 8. 实施记录

### 阶段 0：定位校准和现有能力盘点

- 状态：已完成。
- 目标：确认本次实现必须是 `dev-support` 下的端侧设备，不落入 SDK 内部测试框架。
- 实现：复查 `browser-glass` 的控制事件、StreamChunk 编解码、`sensor.mic`/`sensor.rgb` 上传和 `actuator.speaker` 回执；确认 `python-glass` 仍作为人工 playback 参考端保留，自动化系统回放单独落在 `python-playback-glass`。
- 文件：`dev-support/devices/browser-glass/index.html`、`testdata/audio-sample/`、`testdata/image-sample/`。
- 验证：`rg` 检查现有协议事件和样例文件；确认没有新增 `agent-server/realtime_agent/system_test`。
- 风险：`python-glass` 和 `python-playback-glass` 名称相近，文档和命令必须明确区分人工 playback 与自动化回放。

### 阶段 1：端侧骨架和真实协议注册

- 状态：已完成。
- 目标：新增 `python-playback-glass` 端侧骨架，支持真实 `/ws/control` 注册和心跳。
- 实现：新增端侧包、`device.realtime-agent.yaml`、`playback.glass.yaml`、README、CLI 入口和 `PlaybackProtocolClient`；协议客户端只使用 `aiohttp` WebSocket，不导入 `RealtimeAgentApp`、`RealtimeAgentConfig`、`ToolGateway` 等 server 内部对象。
- 文件：`dev-support/devices/python-playback-glass/realtime_agent_python_playback_glass/{__main__.py,cli.py,case_schema.py,protocol_client.py}`、`pyproject.toml`。
- 验证：`uv run python -m realtime_agent_python_playback_glass --help` 通过；`uv run realtime-agent.playback-glass.run --help` 通过；`uv run realtime-agent.device.validate dev-support/devices/python-playback-glass/device.realtime-agent.yaml --json` 通过。
- 待验收：需要启动真实 server 后运行 `register_only.yaml`，在 `/api/debug/devices` 和 runs 中观察注册事件。

### 阶段 2：Case Schema 和最小回放 Case

- 状态：已完成。
- 目标：定义 Case YAML，并支持音频 fixture 通过 `/ws/stream` 作为 `sensor.mic` 上传。
- 实现：新增 `PlaybackCase`、`PlaybackSuite`、WAV PCM 读取、`who_are_you.yaml`；回放端收到 `control.audio_session.open.requested` 后发送 `control.audio_session.opened`，再按 20ms chunk 上传 WAV PCM 和 final chunk。
- 文件：`case_schema.py`、`runner.py`、`cases/smoke/who_are_you.yaml`。
- 验证：`uv run python -m pytest dev-support/unit-tests/python_playback_glass -q` 通过。
- 待验收：需要真实 server 和 mock provider 环境验证 speaker 输出 chunk。

### 阶段 3：视觉 fixture 和能力请求响应

- 状态：已完成。
- 目标：支持 server 请求 `sensor.rgb` 时上传图片 fixture。
- 实现：监听 `stream.control.open.requested(sensor.rgb)`，按 Case 中 `inputs.sensors.sensor.rgb.fixtures` 读取图片，通过 stream 上传 JPEG chunk，并发送 `stream.input.opened/closed`。
- 文件：`protocol_client.py`、`runner.py`、`cases/smoke/look_front.yaml`。
- 验证：静态测试覆盖 Case 加载和协议 chunk 编解码；设备能力文件声明 `sensor.rgb` 并通过校验。
- 待验收：需要真实 server 触发 `capture_photo` 后确认 `assets.jsonl` 出现 `sensor.rgb` 资产。

### 阶段 4：断言和报告

- 状态：已完成。
- 目标：支持系统层预期行为断言和结构化 `report.json`。
- 实现：新增 runs 产物读取和断言，覆盖 `events.includes`、`streams.includes`、`tools.called`、`tasks.signals.includes`、`assets.<stream_type>.min_count`、`output.min_audio_chunks`、`errors.disallow_system_error`、`model_request.tools.includes`；新增单 Case result 和统一 report 写出。
- 文件：`assertions.py`、`report.py`。
- 验证：`test_recorder_generates_stable_case_without_dynamic_ids` 和 `test_static_boundaries` 通过；故意破坏断言的真实 server 验证尚未执行。
- 待验收：运行真实 suite 后检查失败报告是否足够定位问题。

### 阶段 5：Recorder 和 browser-glass 录制入口

- 状态：已完成。
- 目标：降低手写 Case 成本。
- 实现：新增 `record` 子命令，从 `events.jsonl`、`stream-events.jsonl`、`tool-events.jsonl`、`tool-runs.jsonl`、`assets.jsonl`、`model-request.json` 生成 Case 草稿；过滤动态字段；在 `browser-glass` 页面新增录制命令生成区。
- 文件：`recorder.py`、`browser-glass/index.html`、`README.md`。
- 验证：`test_recorder_generates_stable_case_without_dynamic_ids` 通过；`node` 检查 browser-glass 内联脚本语法通过。
- 待验收：用一次真实 browser-glass 手测 runs 产物生成草稿，并人工确认断言严格度。

### 阶段 6：Suite 和 pytest 集成

- 状态：已完成。
- 目标：支持每次改代码后跑一组系统 Case，并保留 pytest 外层边界。
- 实现：新增 `suites/smoke.yaml` 和 `run --suite`；新增 `--fail-fast`、`--keep-runs` 参数；新增 `dev-support/unit-tests/python_playback_glass`，测试 schema、协议编解码、recorder 和 server 内部依赖禁用规则。
- 文件：`suites/smoke.yaml`、`dev-support/unit-tests/python_playback_glass/*`。
- 验证：`uv run python -m pytest dev-support/unit-tests/python_playback_glass -q` 通过，结果 `8 passed`；其中 `test_cli_register_only_over_real_websocket_server` 启动真实 aiohttp server，并通过子进程 CLI 执行 `register_only` Case。
- 待验收：pytest 已覆盖真实 WebSocket 注册链路；包含音频、图片、工具和输出的完整 smoke suite 仍需要外部启动 mock provider server 后执行。
