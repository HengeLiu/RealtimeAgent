# 项目结构

`audio-chat` 仓库围绕 server-side Python SDK、应用样例、端侧通讯 SDK、开发支持组件、测试和文档组织。

```text
audio-server/audio_chat/
audio-device/
examples/
docs/
testdata/
```

## audio_chat

SDK 主体代码，Python 导入名是：

```python
import audio_chat
```

主要模块：

```text
audio-server/audio_chat/
  agent_core/       # Text / Realtime Agent Core
  asset/            # 资产服务
  audio_pipeline/   # 音频链路
  cli/              # audio-chat.* 命令
  control/          # 设备注册、控制事件、事件路由
  output/           # 输出服务和播放仲裁
  stream/           # stream 生命周期和字节传输
  spec/             # SDK 随包 JSON schema
  tasks.py          # Task 扩展基础类型
  tools.py          # Tool 扩展基础类型
  context.py        # ToolContext / TaskContext
```

## audio-device

多语言端侧通讯 SDK。它只封装端侧和 server 的通讯协议，不包含业务 Tool / Task、
硬件驱动、模型、ASR 或 TTS。

```text
audio-device/
  python/      # audio_chat_device，Python 端侧通讯 SDK
  typescript/  # @audio-chat/device，浏览器 / Node / Electron 端侧通讯 SDK
  swift/       # AudioChatDeviceKit，iOS / macOS 协议模型和 stream codec
  kotlin/      # Android / JVM 协议模型和 stream codec
  c/           # ESP32 / 嵌入式 Linux 最小协议核心
```

每个语言目录下都有独立 README，说明协议、数据模型、导入方式和测试命令。

## examples

应用样例目录。新应用可以参考：

```text
examples/for-blind-app/
  audio-server/
    server.yaml
    capabilities/
      __init__.py
      tools.py
      tasks.py
  devices/
    native-ios-phone/
    native-esp32-glass/
```

业务能力应该放在应用目录下，而不是写进 SDK 核心包。

## examples/dev-support 和 examples/*/devices

`examples/dev-support/` 放开发/测试支持组件。它们在代码和协议层通常会注册成普通
Device，用来验证注册、事件、stream、播放、视觉任务和系统测试回放；但它们不是
AudioChat SDK 预设的正式设备类型，也不要求开发者真实设备按这些目录结构实现。

开发/测试支持组件：

```text
examples/dev-support/devices/browser-glass/
examples/dev-support/devices/python-glass/
examples/dev-support/devices/python-playback-glass/
examples/dev-support/devices/python-phone/
```

应用目录下的真实端侧参考工程：

```text
examples/for-blind-app/devices/native-ios-phone/
examples/for-blind-app/devices/native-esp32-glass/
```

正式设备可以在独立仓库或自己的工程里实现，只要遵守设备注册、事件和 stream 协议。

## docs

社区文档和内部设计记录：

```text
docs/getting-started/
docs/tutorials/
docs/how-to/
docs/reference/
docs/community/
audio-server/docs/
```

社区开发者优先阅读 `getting-started`、`tutorials`、`how-to` 和 `reference`。SDK 内部设计记录位于 `audio-server/docs/`；示例项目设计记录位于各 `examples/<project>/docs/`。

## tests

自动化测试目录。常用命令：

```bash
uv run python -m pytest
uv run python -m pytest examples/for-blind-app/tests/test_text_route_audio_samples.py -q
```

## testdata

契约、回放和样例数据目录。适合保存可复现输入，而不是保存真实用户数据。
