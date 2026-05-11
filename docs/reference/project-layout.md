# 项目结构

`audio-chat` 仓库围绕 server-side Python SDK、应用样例、端侧参考实现、测试和文档组织。

```text
audio-server/audio_chat/
examples/
docs/
testdata/
legacy/
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

## examples

应用样例目录。新应用可以参考：

```text
examples/for-blind-app/
  audio-server/
    server.yaml
    capabilities/
    tools.py
    tasks.py
```

业务能力应该放在应用目录下，而不是写进 SDK 核心包。

## examples/devices

参考端侧实现：

```text
examples/dev-support/devices/browser-glass/
examples/dev-support/devices/python-glass/
examples/dev-support/devices/python-phone/
examples/for-blind-app/devices/native-ios-phone/
examples/for-blind-app/devices/native-esp32-glass/
```

这些目录用于帮助开发者理解协议和做本地联调。正式设备可以在独立仓库或自己的工程里实现，只要遵守设备注册、事件和 stream 协议。

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

## scripts

验收和辅助脚本目录。发布或开源前可以用 acceptance 脚本做开发者体验检查。

## legacy

旧实现和迁移参考。新开发默认不从 `legacy` 开始。
