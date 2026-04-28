# OpenAI Glasses Demo

本项目现在按两个边界清晰的方向组织：

1. [openaiglass-sdk](./openaiglass-sdk)：眼镜、手机、服务器三端开发框架，负责协议、统一模型、设备绑定、通讯、日志、异常处理、大模型运行时、Tool/Task/Skill、全局上下文、硬件能力抽象、回放测试和联调工具。
2. [openaiglass-for-blind](./openaiglass-for-blind)：基于 SDK 的盲人 AI 眼镜真实场景工程，负责找物、红绿灯、导航、计时器等业务能力，以及业务侧三端宿主配置和功能文档。

两个目录通过 [openaiglass-for-blind/SDK安装与能力开发指南.md](./openaiglass-for-blind/SDK安装与能力开发指南.md) 和 [openaiglass-for-blind/docs/stage1/develop/架构阻塞点说明与改进建议.md](./openaiglass-for-blind/docs/stage1/develop/架构阻塞点说明与改进建议.md) 沟通。功能开发团队默认只改 `openaiglass-for-blind`，或者自行创建一个新的同级通结构目录，但不要在业务迭代中补 SDK 层系统能力。

## 快速开始

本节面向功能开发者，不要求先理解三端架构。按下面流程完成本地配置、启动、验证后，就可以基于现有样板开始写业务能力。

### 1. 准备环境

```bash
uv sync --python 3.11
uv pip install -e openaiglass-sdk/server-python
```

如果 `uv run openaiglass...` 提示找不到命令，重新执行一次 editable 安装命令。

### 2. 同步本机联调配置

```bash
uv run openaiglass.config.sync --app-root openaiglass-for-blind
```

这一步会把当前开发机的局域网地址、服务端端口、眼镜设备号、手机设备号和配对令牌同步到业务侧配置文件。换网络、换端口、换设备号后都重新执行一次。

### 3. 配置眼镜 WiFi

真实 ESP32 眼镜需要先写入可连接的 WiFi 名称和密码。编辑业务侧眼镜配置文件：

```bash
open openaiglass-for-blind/host/glass/config/local_build.env
```

至少确认下面两项是真实 WiFi：

```bash
GLASS_WIFI_PRIMARY_SSID="你的WiFi名称"
GLASS_WIFI_PRIMARY_PASSWORD="你的WiFi密码"
```

如果配置文件不存在，先从模板复制：

```bash
cp openaiglass-for-blind/host/glass/config/local_build.env.example \
  openaiglass-for-blind/host/glass/config/local_build.env
```

**注意眼镜和手机必须处于同一个局域网下，如果你的服务器是本地开发环境，同样要确保在同一局域网下。**
`openaiglass.config.sync` 会同步服务端地址、设备号和配对令牌，但不会替你猜测真实 WiFi 密码。换 WiFi 后要重新修改这两项，再重新烧录眼镜端。

### 4. 启动服务端

```bash
uv run openaiglass.server.run \
  --app-module host.server.main \
  --app-root openaiglass-for-blind
```

看到服务端启动后，另开一个终端继续下一步。

### 5. 启动手机端

有真实 iPhone 时，用 Xcode 运行业务侧手机 App：

```bash
uv run openaiglass.phone.open --app-root openaiglass-for-blind
```

上面命令会打开业务侧工程：

```text
openaiglass-for-blind/host/phone/ios/GlassesVideoReceiver.xcodeproj
```

在 Xcode 中选择你的 iPhone 作为运行目标，按 `Run`。如果是第一次真机运行，先在 Xcode 里完成 Team、Bundle Identifier 和签名配置。不要打开 `openaiglass-sdk/phone-ios` 目录下的 SDK 工程作为业务入口。

没有真实 iPhone 时，先用 `phone-mock` 完成服务端到手机任务的设备级闭环：

```bash
uv run openaiglass.phone.mock \
  --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json
```

### 6. 启动眼镜端

有真实 ESP32 眼镜时，连接眼镜 USB 串口后执行：

```bash
uv run openaiglass.glass.start \
  --app-root openaiglass-for-blind \
  --sdk-root openaiglass-sdk \
  --port '/dev/tty.usbmodem*'
```

这条命令会按业务侧 `host/glass/config/local_build.env` 写入的 WiFi、服务端地址、设备号和配对令牌构建、烧录并进入串口监看。只想编译不烧录时加 `--build-only`；只想看串口时加 `--monitor-only`。

没有真实眼镜时，可以用 `glass-playback` 启动虚拟眼镜。先在 `openaiglass-for-blind/host/glass-playback/config/` 放一个回放配置 JSON，然后执行：

```bash
uv run openaiglass.glass.start \
  --runtime playback \
  --config <glass-playback.json> \
  --sdk-root openaiglass-sdk
```

手机端和眼镜端都启动后，服务端应该能看到对应设备在线。

### 7. 验证 SDK 和业务宿主

```bash
uv run openaiglass.sdk.preflight \
  --report openaiglass-for-blind/logs/sdk-preflight-current.json
```

只想确认服务端是否活着，可以访问：

```bash
curl http://127.0.0.1:8765/api/health
```

确认设备是否在线：

```bash
curl http://127.0.0.1:8765/api/runtime/devices
```

### 8. SDK 功能快速验证

下面几条只用于确认设备开发环境和 SDK 主链路可用，不代表真正业务功能已经完成。

唤醒词暂时使用 ESP32 自带的“嗨，乐鑫”。当前每句话前都要说一次唤醒词。

| 验证目标                 | 说法                               | 备注                                                                                            |
| ------------------------ | ---------------------------------- | ----------------------------------------------------------------------------------------------- |
| 简单对话测试             | “嗨，乐鑫，自我介绍一下”         | 目前相应较慢且不能打断播放过程，持续优化中                                                      |
| 工具调用测试             | “嗨，乐鑫，看一下我眼前有什么”   | 会主动调用拍照工具从眼镜采集一张照片，目的是为了测试工具调用，不是真实功能的流程                |
| 眼镜手机直连视频链路测试 | “嗨，乐鑫，帮我找一下手机在哪里” | 需要真实iPhone或者XCode中的模拟iPhone，并且保持App在前台运行，点击手机App上的完成按钮结束视频流 |

如果服务端、手机端、眼镜端日志都能看到对应请求、工具调用或任务事件，说明 SDK 开发环境基本可用。以上测试未实现真正的业务功能，只用于设备开发环境可用性验证。

### 9. 开始功能开发

新增能力优先复制一个现有样板：

| 目标           | 建议参考                                                                                                                      |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| 一次性工具能力 | [openaiglass-for-blind/capabilities/find_object/server/tool.py](./openaiglass-for-blind/capabilities/find_object/server/tool.py) |
| 后台任务能力   | [openaiglass-for-blind/capabilities/timer/server/task.py](./openaiglass-for-blind/capabilities/timer/server/task.py)             |
| 手机视觉任务   | [openaiglass-for-blind/capabilities/traffic_light](./openaiglass-for-blind/capabilities/traffic_light)                           |
| 地图或外部服务 | [openaiglass-for-blind/capabilities/navigation](./openaiglass-for-blind/capabilities/navigation)                                 |

功能代码放在 `openaiglass-for-blind/capabilities/<能力名>`。如果开发中发现 SDK 公开接口不够用，不要直接改 SDK 内部实现，先写入 [架构阻塞点说明与改进建议.md](./openaiglass-for-blind/docs/stage1/develop/架构阻塞点说明与改进建议.md)。

## 目录概览

| 目录                                                                    | 说明                                                                              |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| [openaiglass-sdk/server-python](./openaiglass-sdk/server-python)           | Python SDK 包源码，发布包名为 `openaiglasses-sdk`，导入名为 `openaiglasses`。 |
| [openaiglass-sdk/phone-ios](./openaiglass-sdk/phone-ios)                   | iOS 通用手机运行时，负责注册、心跳、视频接收、手机任务承载和结果上报。            |
| [openaiglass-sdk/glass-esp32](./openaiglass-sdk/glass-esp32)               | ESP32 通用眼镜运行时，负责 WiFi、控制连接、音频、摄像头和端侧命令。               |
| [openaiglass-sdk/phone-mock](./openaiglass-sdk/phone-mock)                 | 按真实 phone 协议运行的 Python 虚拟手机设备。                                     |
| [openaiglass-sdk/glass-playback](./openaiglass-sdk/glass-playback)         | 按真实 glass 协议运行的眼镜回放设备。                                             |
| [openaiglass-for-blind/capabilities](./openaiglass-for-blind/capabilities) | 当前业务能力：`find_object`、`traffic_light`、`navigation`、`timer`。     |
| [openaiglass-for-blind/host](./openaiglass-for-blind/host)                 | 盲人业务工程的服务端、手机端、眼镜端薄宿主和本地配置。                            |
| [openaiglass-for-blind/docs](./openaiglass-for-blind/docs)                 | 业务计划、开发记录、当前状态、真机联调和阻塞点文档。                              |

## SDK 简要现状

当前 SDK 已经能支撑功能团队继续开发：服务端可通过 `OpenAIGlassesSDK` 注册 `BaseTool`、`BaseTask`、`BasePhoneTask`、`BasePhoneProcessor`、`BaseSensorProvider` 和 MCP Adapter；`DeviceGroupContext` 提供设备查询、抓拍、手机视频链路、手机任务、通知、MCP 和 SDK 托管任务等公开入口；CLI 提供配置同步、服务端启动、手机工程打开/构建、`phone-mock`、眼镜构建/回放、预检和包检查。

仍需 SDK 团队后续补齐的系统能力包括实时语音打断、普通回复端到端真流式、公网/NAT peer-link 治理、生产级任务持久化恢复、手机端模型资源仲裁、真实外部服务治理，以及 iOS/ESP32 发布级包化。详细判断见 [SDK对功能开发支持情况的说明.md](./SDK对功能开发支持情况的说明.md)。

## 功能开发现状

`openaiglass-for-blind` 已经基于 SDK 完成四类样板能力：

| 能力              | 当前状态                                                                                                        |
| ----------------- | --------------------------------------------------------------------------------------------------------------- |
| `find_object`   | 服务端 Tool/Task、手机处理器、手机任务和 iOS 插件样例已具备，可启动眼镜到手机视频链路并按手机检测事件完成任务。 |
| `traffic_light` | 已有红绿灯识别 Tool/Task、手机侧处理器和任务样例。                                                              |
| `navigation`    | 已有导航准备 Tool、导航 Task、业务侧 mock AMap MCP Adapter、POI/路线准备和事件推进样板。                        |
| `timer`         | 已有计时器 Tool/Task，用 SDK 托管任务验证创建、查询、取消和完成通知。                                           |

功能开发的详细入口见 [openaiglass-for-blind/SDK安装与能力开发指南.md](./openaiglass-for-blind/SDK安装与能力开发指南.md)，当前真实实现面见 [openaiglass-for-blind/docs/当前实现状态.md](./openaiglass-for-blind/docs/当前实现状态.md)。

## 常用命令

```bash
uv sync --python 3.11
uv pip install -e openaiglass-sdk/server-python

uv run openaiglass.config.sync --app-root openaiglass-for-blind
uv run openaiglass.server.run --app-module host.server.main --app-root openaiglass-for-blind
uv run openaiglass.phone.mock --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json
uv run openaiglass.glass.start --runtime playback --config <glass-playback.json>
uv run openaiglass.sdk.preflight --report openaiglass-for-blind/logs/sdk-preflight-current.json
uv run python -m pytest openaiglass-sdk/tests -q
```

真实三端联调建议按“同步配置 -> 启动服务端 -> 启动手机或 `phone-mock` -> 启动 ESP32 眼镜或 `glass-playback` -> 触发业务能力 -> 看任务事件和端侧日志”的顺序执行。

## 给功能开发者的下一步建议

1. 新能力优先复用现有四个样板目录，不要直接操作 SDK 内部运行时。
2. 业务代码需要设备、通知、抓拍、视频链路、手机任务、MCP 或后台任务时，只通过 SDK 公开上下文调用。
3. 测试优先使用 `phone-mock`、`glass-playback` 和 `openaiglass.sdk.preflight` 做设备级自动化闭环，再进入真机联调。
4. 如果 SDK 公开能力不能表达业务需求，把问题写入 [架构阻塞点说明与改进建议.md](./openaiglass-for-blind/docs/stage1/develop/架构阻塞点说明与改进建议.md)，由 SDK 团队继续优化。
