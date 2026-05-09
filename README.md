# audio-chat

`audio-chat` 是面向语音交互、多设备协作和实时 stream 的 server-side Python SDK。当前仓库已经升级为以新 SDK 为主的组织方式：

- `audio-chat-sdk/`：Python server SDK 源码，发布包名为 `audio-chat`，导入名为 `audio_chat`。
- `app-examples/`：基于 SDK 的应用样例，新的业务应用从这里开始。
- `device-examples/`：浏览器、Python、iOS、ESP32-S3 等参考端侧实现。端侧实现已经从 SDK 子目录中独立出来，体现 SDK 只承担 server-side 职责。
- `docs/`：架构设计、迁移说明、联调和排障文档。
- `testdata/`、`tests/`、`scripts/`：契约样例、自动化测试和验收脚本。
- `legacy/`：旧 `openaiglass-sdk`、旧 `openaiglass-for-blind`、旧根目录文档和历史运行资产。它们只作为迁移参考，不再作为新开发入口。

server 不负责录音、播放、唤醒词、端侧 AEC 或硬件驱动。设备注册时声明 `user_id`、`device_id` 和 `supports` 能力；业务 Tool / Task 通过 Context 表达设备使用意图。当前可用开发方式以 [设备注册与功能开发说明](docs/device-capability-development-guide.md) 为准；下一阶段目标 API 见 [Context 与设备 API 设计说明](docs/context-device-api-design.md)。

## 快速开始

准备环境：

```bash
uv sync --python 3.11
uv pip install -e .
```

如果 `uv run audio-chat.*` 找不到命令，重新执行 editable 安装。

启动统一示例应用 server：

```bash
uv run audio-chat.server.run --app-name for-blind-app
```

常用调试接口：

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/debug/devices
curl http://127.0.0.1:8765/api/debug/playback
```

## 启动参考设备

最新推荐联调顺序：

```bash
# 1. 校验端侧能力文件，确认设备能力声明有效
uv run audio-chat.device.validate device-examples/browser-glass/device.audio-chat.yaml

# 2. 启动应用 server
uv run audio-chat.server.run --app-name for-blind-app

# 3. 打开浏览器参考端，连接并注册后开始语音或视觉测试
uv run audio-chat.web.open --print-url

# 4. 可选：启动 Python phone mock，验证同一 user_id 下多设备协作
uv run python -m audio_chat_python_phone_mock --config device-examples/python-phone/phone.mock.yaml
```

浏览器设备：

```bash
uv run audio-chat.web.open --print-url
```

Python phone mock：

```bash
uv run python -m audio_chat_python_phone_mock --config device-examples/python-phone/phone.mock.yaml
```

Python 手机视频显示端：

```bash
uv run python -m audio_chat_python_phone_mock --config device-examples/python-phone/phone.preview.yaml
```

该端侧会打开 OpenCV 视频窗口，注册到 server，并订阅同一 `user_id` 下的
`sensor.rgb` 输入流。眼镜端或浏览器端上传 RGB stream 后，画面会回显到这个窗口，
最近一帧会写入 `runs/audio-chat/python-phone/latest-rgb.jpg`。

Python glass playback：

```bash
uv run audio-chat.playback.glass --config app-examples/for-blind-app/host/glass-playback/sdk-playback.yaml
```

发布候选版本当前为 `0.1.0rc1`。发布前使用 `audio-chat.sdk.package-check` 和
`old-sdk-parity-release` 验收，确认统一示例应用、包边界、文档和旧 SDK 等价回放仍然一致。

使用录制音频驱动 playback：

```bash
uv run audio-chat.playback.glass \
  --server-url http://127.0.0.1:8765 \
  --audio-wav legacy/openaiglass-sdk/testdata/audio-sample/wav/看一下我前面有什么.wav
```

Text 模型路线的无头验收可以直接复用旧 SDK 的 AudioSample。mock ASR 会把 WAV 文件名作为转写文本，mock text model 会按文本意图触发真实 ToolGateway，因此这条链路能覆盖 `sensor.mic -> ASR -> TextAgentCore -> Tool -> Streaming TTS -> actuator.speaker`：

```bash
uv run python -m pytest tests/test_text_route_audio_samples.py -q
```

iOS 参考端：

```bash
uv run audio-chat.ios.open
```

ESP32-S3 参考端：

```bash
uv run audio-chat.esp32.config
uv run audio-chat.esp32.build --dry-run --build-only
```

## 开发者工作模型

当前可执行的设备注册、Tool / Task 开发、调试和验收入口统一整理在 [设备注册与功能开发说明](docs/device-capability-development-guide.md)。下一阶段的新版 Context API、selector 规则、AssetRef 边界和设备能力结构整理在 [Context 与设备 API 设计说明](docs/context-device-api-design.md)。

当前开发口径：

- Tool 使用 `ToolContext`，只做短生命周期动作。
- Task 使用 `TaskContext`，可以做持续数据流和异步状态维护。
- 当前功能开发者使用 `context.devices.request_asset()`、`publish_event()`、`watch_assets()`、`submit_text()` 等兼容接口。
- 麦克风和喇叭是系统音频通道，不作为普通设备能力开放。
- 设备当前使用 `supports[].id` 声明能力；新版目标结构是 `supports.sensors[].type` 和 `supports.actuators[].type`。
- `context.devices.sensors.*`、`DeviceContext`、`selector` 是下一阶段目标 API，不要在当前业务代码里直接使用。

默认应用目录结构如下：

```text
app-examples/<your-app>/
  server.yaml
  capabilities/
    your_tool/
      tool.py      # 继承 BaseTool，会被自动发现
    your_task/
      task.py      # 继承 BaseTask，会被自动发现
  skills/
  config/
```

当前已导出的兼容公开类仍可从 `audio_chat` 顶层导入：

```python
from audio_chat import BaseTask, BaseTool, ToolContext, ToolResult
```

`server.yaml` 中配置自动发现包后，开发者只需要把 `BaseTool` / `BaseTask` 子类放进对应 package。SDK 启动时会扫描这些类，把 Tool schema 注册给 Agent Core，把 Task 类型注册给 Task Engine。业务代码不需要在 `app.py` 里手写注册逻辑。

新增一个能力时，推荐按这个顺序设计：

1. 先判断能力是一次性动作还是长流程：一次性动作写 Tool，长流程写 Task。
2. 列出它需要哪些端侧能力：例如 `sensors.rgb`、`sensors.imu`、`actuators.vibrator` 或 `commands.call`。
3. 确认端侧设备能力文件中已经声明对应能力和 selector 可匹配字段。
4. 在 Tool / Task 中通过高级 Context API 表达能力调用，不直接拼底层信令。
5. 用 `runs/audio-chat/...` 中的运行产物验证链路。

业务样例：

- `app-examples/for-blind-app`：最小 Tool / Task / playback 样板。
- `app-examples/for-blind-app`：盲人眼镜业务样例，包含找物、红绿灯、导航、搜索和计时器迁移版本。
- `app-examples/for-blind-app/templates`：新能力开发模板。

关键约束：

- Tool / Task 只能通过 Context 公开 API 访问设备通讯能力。
- Tool 不持有 `tasks`、`memory`、`skills`、`mcp` 服务入口；这些能力通过独立 Tool 暴露给模型。
- 不直接把图片、音频、视频或文件字节放进控制信令，大字节数据必须走 stream。
- 设备开发者只实现注册能力、控制信令处理和 stream 读写，不需要理解 Agent Core 或业务 Tool。

## 设备开发

端侧开发者优先维护设备能力文件。浏览器示例：

```bash
uv run audio-chat.device.validate device-examples/browser-glass/device.audio-chat.yaml --json
```

设备能力文件用于声明 `supports.sensors`、`supports.actuators`、`device_role`、`tags` 和 `external` 扩展信息。底层订阅与控制信令由 SDK 和端侧参考实现处理；新功能开发者不需要直接维护。

端侧实现入口：

- `device-examples/browser-glass`
- `device-examples/python-glass`
- `device-examples/python-phone`
- `device-examples/native-ios-phone`
- `device-examples/native-esp32-glass`

iOS / ESP32 目录目前是参考端和契约入口，不代表真实 iOS 模型或 ESP32 真机效果已经完成。

## 配置同步与检查

生成本地联调配置：

```bash
uv run audio-chat.config.sync --app-root app-examples/for-blind-app
```

预检：

```bash
uv run audio-chat.dev.preflight --config app-examples/for-blind-app/server.yaml
```

发布包检查：

```bash
uv run audio-chat.sdk.package-check --report runs/audio-chat/package-check.json
```

自动验收：

```bash
uv run python scripts/acceptance_check.py developer-usability \
  --report runs/acceptance/developer-usability.json

uv run python scripts/acceptance_check.py capability-template-playback \
  --report runs/acceptance/capability-template-playback.json

uv run python scripts/acceptance_check.py old-sdk-parity-capabilities \
  --report runs/acceptance/old-sdk-parity-capabilities.json

uv run python scripts/acceptance_check.py old-sdk-parity-docs \
  --report runs/acceptance/old-sdk-parity-docs.json

uv run python scripts/acceptance_check.py old-sdk-parity-release \
  --report runs/acceptance/old-sdk-parity-release.json
```

## 测试

```bash
uv run python -m pytest tests -q
```

真实 provider 集成测试需要配置对应 API Key：

```bash
uv run python -m pytest tests/integration/test_dashscope_providers.py -q
```

当前代码仍可能有开发中测试失败；目录升级不隐藏这些问题，后续修复应继续在新的根目录结构下进行。

## 文档

- [SDK 架构设计](docs/audio-chat-sdk-architecture.md)
- [设备注册与功能开发说明](docs/device-capability-development-guide.md)
- [Context 与设备 API 设计说明](docs/context-device-api-design.md)
- [迁移指南](docs/phase3-migration-guide.md)
- [运行产物说明](docs/runs-artifacts-guide.md)
- [浏览器设备设计](docs/browser-glass-design.md)
- [ESP32-S3 参考端说明](docs/esp32-s3-endpoint-bridge.md)
- [历史 SDK 可用性对齐计划](docs/old-sdk-parity-development-plan.md)

历史资料在 `legacy/` 下保留。需要查旧实现时优先看 `legacy/openaiglass-sdk` 和 `legacy/openaiglass-for-blind`，新开发不要再从这些目录复制入口命令或导入路径。
