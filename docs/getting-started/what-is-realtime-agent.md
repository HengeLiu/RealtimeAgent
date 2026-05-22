# realtime-agent 项目介绍

`realtime-agent` 是一套面向多端设备的语音 Agent SDK。它帮助社区开发者快速构建带有语音对话、设备能力调用、跨设备协作和运行排障能力的 AI 应用。

项目内部使用事件协议连接 server 和 device，但普通应用开发者不需要把事件当作主要编程模型。Server 侧开发者通过 Tool、Task 和 Context API 使用设备能力；Device 侧开发者通过 Device SDK 声明设备能力、实现能力处理函数并上传或消费 stream。事件协议是 SDK 通讯层、跨语言兼容和排障观测的内部契约。

## 项目定位

`realtime-agent` 主要解决两类问题。

第一类是基于大模型的端侧语音交互。应用可以选择视觉语言模型链路，也可以选择 Omni / Realtime 音频链路：

```text
Vision 链路：
sensor.mic -> ASR -> VisionRealtimeAgentCore -> Tool -> Streaming TTS -> actuator.speaker

Realtime 链路：
sensor.mic -> OmniRealtimeAgentCore -> assistant_audio.delta -> actuator.speaker
```

第二类是基于标准通讯层的跨设备能力调用。智能眼镜、手机、浏览器、Linux 网关、ESP32 或其他端侧设备可以通过 Device SDK 接入 server，声明自己支持的摄像头、IMU、深度图、震动、播放等能力。业务 Tool / Task 通过 Context API 请求这些能力，而不是直接操作 WebSocket 或硬编码设备 ID。

因此，更准确的定位是：

> `realtime-agent` 是一个用于多端语音 Agent 应用的 Server SDK、Device SDK 和标准通讯协议集合。它把大模型运行时、设备能力调用、stream 传输、Tool / Task 调度、输出播放和运行产物组织成可复用的开发框架。

## 它适合什么

`realtime-agent` 适合以下应用：

1. 语音优先的 AI Agent。
2. 智能眼镜、手机、浏览器或嵌入式设备共同参与的应用。
3. 需要摄像头、IMU、深度图、震动器、speaker 等端侧能力的应用。
4. 需要把一次性动作做成 Tool，把持续流程做成 Task 的应用。
5. 需要本地回放、模拟设备、运行产物和跨设备排障证据的应用。

典型场景包括：

- 智能眼镜助手。
- 视觉辅助和导航。
- 手机与眼镜协作。
- 浏览器设备原型。
- ESP32 或其他边缘设备接入语音 Agent。
- 多传感器 AI 原型验证。

## 它不是什么

`realtime-agent` 不是通用聊天 UI 框架，也不是单纯的语音转文字工具。它不负责：

1. 端侧真实麦克风录音、喇叭播放或摄像头驱动。
2. 唤醒词、AEC、I2S、蓝牙、Wi-Fi 配网或硬件固件细节。
3. 某个固定智能眼镜硬件的完整产品系统。
4. 替代 Pipecat、LiveKit Agents 等实时媒体 Agent 框架。
5. 要求业务开发者学习底层事件协议。

端侧硬件驱动由设备应用自己实现；`realtime-agent` 提供的是 server runtime、device 通讯 SDK、标准协议、能力抽象和调试产物。

## 代码架构

仓库按四个主要层次组织。

```text
realtime-agent
├── agent-server/realtime_agent/       # Server SDK，Python 导入名 realtime_agent
├── devices/                  # 多语言 Device SDK
├── examples/                      # 示例应用、真实端侧参考工程和开发支持组件
├── docs/                          # 社区文档和内部设计记录
└── testdata/                      # 协议、回放和测试样例
```

### Server SDK

Server SDK 位于 `agent-server/realtime_agent/`，负责大模型运行时、设备能力调用和服务端基础设施：

```text
agent-server/realtime_agent/
  agent_core/       # Text / Realtime Agent Core
  audio_pipeline/   # ASR、TTS 和音频输入输出链路
  control/          # 设备注册、连接状态和控制通讯
  stream/           # 音频、图片、视频和传感器数据流
  output/           # 输出服务和播放仲裁
  asset/            # 图片、音频等资产引用和读取
  context.py        # ToolContext / TaskContext
  tools.py          # BaseTool / ToolSpec / ToolResult
  tasks.py          # BaseTask / TaskSpec / TaskRunResult
  spec/             # 随包协议 schema 和规范资产
```

Server SDK 的使用者主要是应用开发者。他们在应用目录里编写 Tool、Task 和配置文件，不需要修改 SDK 核心包。

### Device SDK

Device SDK 位于 `devices/`，负责端侧与 server 的通讯层封装：

```text
devices/
  python/      # realtime_agent_device，Python 端侧通讯 SDK
  typescript/  # @realtime-agent/device，浏览器 / Node / Electron 端侧通讯 SDK
  swift/       # RealtimeAgentDeviceKit，iOS / macOS 协议模型和 stream codec
  kotlin/      # Android / JVM 协议模型和 stream codec
  c/           # ESP32 / 嵌入式 Linux 最小协议核心
```

Device SDK 的目标不是替开发者实现摄像头、麦克风或硬件驱动，而是把端侧注册、能力声明、server 通讯、stream 编解码、命令回执和诊断信息封装成稳定 API。端侧开发者只需要把真实硬件能力接到这些 API 上。

### 标准通讯协议

server 和 device 的底层通讯使用统一协议，包括：

- 设备注册和心跳。
- 能力声明。
- 命令请求和回执。
- 输入 / 输出 stream 生命周期。
- 二进制 stream chunk 编解码。
- 错误码和诊断信息。

这些协议资产位于 `agent-server/realtime_agent/spec/` 和 `docs/internal/`。它们主要面向 SDK 维护者、跨语言 SDK 实现者和调试工具。普通应用开发者应该优先使用 Server SDK 和 Device SDK 暴露的能力 API。

### 示例应用和开发支持组件

`examples/for-blind-app/` 是当前主要示例应用，展示如何组织一个真实语音 Agent 应用：

```text
examples/for-blind-app/agent-server/
  server.yaml
  capabilities/
    tools.py
    tasks.py
```

`examples/dev-support/` 下的 browser-glass、python-phone、python-glass 等组件用于本地开发、联调和回放。它们会以普通 Device 形态接入协议，但不代表 SDK 预设了固定设备类型。

## 关键能力

### 语音 Agent Runtime

Server SDK 提供两条主链路：

1. Vision Realtime Agent Core：使用 ASR、视觉语言模型、Tool loop 和 streaming TTS。
2. Realtime Agent Core：使用 Omni / Realtime 音频模型直接处理音频输入输出。

这两条链路都可以接入 Tool、Task、上下文管理、输出仲裁和运行产物。

### Tool 和 Task

Tool 适合一次性短动作，例如拍照、查路线、搜索资料。Task 适合持续流程或后台动作，例如找物、红绿灯观察、导航执行过程。

业务能力放在应用目录下：

```text
examples/<your-app>/agent-server/capabilities/
  tools.py
  tasks.py
```

SDK 会自动发现并注册这些能力。业务代码不应该写进 `agent-server/realtime_agent/` 核心包。

### Context API

Context API 是 Server SDK 给 Tool / Task 的设备能力入口。开发者通过类型化 facade 表达需求：

```python
asset = await context.devices.sensors.rgb.one(
    params={"reason": "capture", "format": "jpeg"},
    timeout_seconds=5,
)

await context.output.say("已完成分析")
```

这段代码背后可能触发设备选择、控制通讯、stream 打开、图片上传、资产写入和模型上下文拼装，但业务开发者不需要直接处理这些底层步骤。

### Device Capability API

Device SDK 帮助端侧声明能力并处理 server 请求。Python 端侧可以用 builder 生成设备描述：

```python
from realtime_agent_device import RealtimeAgentDeviceClient, DeviceBuilder

device = (
    DeviceBuilder.define("dev-python-001")
    .user("user-001")
    .name("Python device")
    .role("glass")
    .sensor_rgb(modes=["single", "continuous"], format="jpeg")
    .actuator_vibrator(["vibrate"])
)
```

理想的端侧开发模型是：开发者声明设备具备哪些能力，然后把真实硬件逻辑绑定到 SDK 的 handler 或 stream API 上。底层事件收发、回执格式和 stream chunk 编码由 Device SDK 处理。

### Stream 和资产

音频、图片、视频、深度图等大字节数据不放进控制消息。它们通过 stream 通道传输，server 侧写入资产服务或转发到播放输出。Tool / Task 通常拿到的是资产引用，而不是直接处理协议帧。

### 输出和播放仲裁

Server SDK 通过 Output Service 管理用户可听输出和端侧播放。业务代码使用 `context.output.say()` 或 ToolResult message 表达输出意图，SDK 负责把输出转成 TTS、speaker stream、播放决策和运行产物。

### 运行产物和排障

`runs/` 是排查跨设备链路的核心证据目录。一次 session 常用文件包括：

- `messages.jsonl`
- `model-request.json`
- `agent-events.jsonl`
- `tool-events.jsonl`
- `stream-events.jsonl`
- `output-decisions.jsonl`
- `playback-decisions.jsonl`
- `audio/`
- `photos/`

这些产物让开发者可以复查模型请求、工具调用、设备通讯、stream 生命周期和播放仲裁，而不是只看终端日志。

## 典型使用方式

### 方式一：运行示例应用

准备环境：

```bash
uv sync --python 3.11
uv pip install -e .
```

启动示例 server：

```bash
uv run realtime-agent.server.run --app-name for-blind-app
```

打开浏览器眼镜模拟组件：

```bash
uv run realtime-agent.web.open --serve
```

查看 server 状态：

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/debug/devices
curl http://127.0.0.1:8765/api/debug/playback
```

### 方式二：开发一个 Server 侧 Tool

新应用可以参考以下目录结构：

```text
examples/<your-app>/agent-server/
  server.yaml
  capabilities/
    __init__.py
    tools.py
    tasks.py
```

Tool 示例：

```python
from realtime_agent import BaseTool, ToolContext, ToolResult, ToolSpec


class CapturePhotoTool(BaseTool):
    """通过端侧 RGB 传感器抓拍当前画面。"""

    spec = ToolSpec(
        name="capture_photo",
        description="当用户需要了解当前画面时，采集一张 RGB 图片。",
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """请求端侧上传一张图片，并返回资产引用。"""

        asset = await context.devices.sensors.rgb.one(
            params={"reason": "agent_requested", "format": "jpeg"},
            timeout_seconds=5,
        )
        return ToolResult.success(
            data={"asset_id": asset.asset_id, "uri": asset.uri},
            assets=[asset],
            message="已获取当前画面。",
        )
```

### 方式三：开发一个端侧设备

端侧设备开发者应优先使用对应语言的 Device SDK。Python 端侧最小注册示例：

```python
import asyncio

from realtime_agent_device import RealtimeAgentDeviceClient, DeviceBuilder


async def main() -> None:
    """注册一个具备 RGB 和震动能力的 Python 设备。"""

    device = (
        DeviceBuilder.define("dev-python-001")
        .user("user-001")
        .name("Python device")
        .role("glass")
        .sensor_rgb(modes=["single"], format="jpeg")
        .actuator_vibrator(["vibrate"])
    )

    client = RealtimeAgentDeviceClient(
        server_url="http://127.0.0.1:8765",
        device=device,
    )
    await client.connect()
    await client.register()
    print(client.diagnostics_snapshot())
    await client.close()


asyncio.run(main())
```

后续设备应用需要继续绑定真实相机、麦克风、播放、震动或传感器驱动。Device SDK 负责通讯层，硬件逻辑由设备工程实现。

### 方式四：做跨设备联调

推荐顺序：

1. 校验设备能力文件：

   ```bash
   uv run realtime-agent.device.validate examples/dev-support/devices/browser-glass/device.realtime-agent.yaml
   ```

2. 启动 server：

   ```bash
   uv run realtime-agent.server.run --app-name for-blind-app
   ```

3. 打开浏览器眼镜模拟组件：

   ```bash
   uv run realtime-agent.web.open --serve
   ```

4. 可选启动 Python 手机预览组件：

   ```bash
   uv run --extra gui python -m realtime_agent_python_phone_mock --config examples/dev-support/devices/python-phone/phone.preview.yaml
   ```

5. 检查调试接口和 `runs/` 产物。

## 开发者应该关心的边界

1. Server 应用开发者关心 Tool、Task、Context API、模型配置和运行产物。
2. Device 应用开发者关心 Device SDK、能力声明、handler、stream API 和真实硬件驱动。
3. SDK 维护者关心事件协议、schema、AsyncAPI、跨语言兼容和黄金样例。
4. 事件协议是内部通讯机制，不是普通业务开发者的主路径 API。
5. `legacy/` 只作为迁移参考；新功能应优先使用当前 `realtime_agent` 和 `devices` API。

## 下一步阅读

- [快速开始](quickstart.md)
- [第一个 Tool 和 Task](../tutorials/build-first-capability.md)
- [跨设备本地联调](../how-to/cross-device-local-debug.md)
- [项目结构](../reference/project-layout.md)
- [设备能力与 Context API 开发说明](../../agent-server/docs/how-to/device-capability-development.md)
