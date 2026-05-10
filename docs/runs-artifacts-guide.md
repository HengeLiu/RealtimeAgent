# runs 目录产物说明

文档状态：当前调试说明。本文描述当前 `runs/<app_name>` 产物结构，是排查模型请求、设备通讯、stream、资产、Tool、Task 和输出链路的主要入口。

`runs` 是 audio-chat 的开发调试和回放证据目录。它不是业务数据目录，也不是开发者日常需要全部阅读的目录。

默认根目录由 `paths.runtime_root` 统一决定；未配置时从 `server.yaml` 的 `app-name` 派生，例如
`for-blind-app` 的默认值是：

```text
runs/for-blind-app
```

`observability.runs_root`、`asset.root`、`memory.path`、`user.message_store.root`
和 `dev_checks.report_path` 都会从这个根目录自动派生。只有确实需要单独覆盖某个
子目录时，才在对应配置段里显式写路径。

从当前版本开始，终端日志中的 `detail_path` 会打印绝对路径。相对路径需要按 server 启动时的工作目录拼接。

## 先看什么

一次真实联调通常只需要看 4 个文件：

| 文件 | 用途 |
| --- | --- |
| `<user_id>/<device_id>/model-request.json` | 本轮发给模型的请求快照，包括 prompt、messages、tools。 |
| `<user_id>/<device_id>/agent-events.jsonl` | Agent Core 和 provider 的关键事件，排查模型是否响应、是否调用工具。 |
| `<user_id>/<device_id>/tool-events.jsonl` | 工具调用参数、结果、耗时和错误。 |
| `<user_id>/<device_id>/events.jsonl` | 控制事件时间线，排查设备注册、唤醒、音频会话开关。 |

如果只想确认模型到底听到了什么、拿到了哪些工具，优先看 `model-request.json`。

如果模型没反应，按顺序看：

1. `events.jsonl`
2. `stream-events.jsonl`
3. `agent-events.jsonl`
4. `tool-events.jsonl`
5. 根目录 `system-events.jsonl`

## 根目录结构

```text
runs/<app_name>/
  control-events.jsonl
  control-routes.jsonl
  system-events.jsonl
  debug/
    playback.json
  <user_id>/
    <device_id>/
      ...
  tasks/
    ...
```

## 根目录文件

| 路径 | 类型 | 是否日常必看 | 说明 |
| --- | --- | --- | --- |
| `control-events.jsonl` | 全局控制事件 | 否 | 所有无 session 或跨 session 控制事件汇总。按 session 排障时优先看 session 下的 `events.jsonl`。 |
| `control-routes.jsonl` | 全局事件路由诊断 | 否 | 记录事件订阅匹配、投递数量。只有排查“为什么设备没收到事件”时看。 |
| `system-events.jsonl` | 全局系统错误/降级 | 是 | 所有系统级异常、provider 降级、stream 处理错误都会进入这里。 |
| `debug/playback.json` | 当前播放状态快照 | 按需 | 查询当前 Output Service / Playback Arbiter 状态。 |

## 用户设备目录

当前版本按用户和设备组织运行产物：

```text
runs/<app_name>/<user_id>/<device_id>/
```

其中 `<device_id>` 同时也是当前过渡说明的会话标识，会出现在终端日志里，例如：

```text
user_id=user-browser-device-001 device_id=dev-browser-xxxx session_id=dev-browser-xxxx
```

### 日常调试文件

| 文件 | 用途 |
| --- | --- |
| `model-request.json` | 模型请求快照。Realtime Omni 不是传统 Chat Completions，但这里保存等价视图，包括 instructions、messages、tools。 |
| `agent-events.jsonl` | Agent Core 事件。包括 session open/close、input commit、provider event、delta 首包/完成摘要、tool result ready。 |
| `events.jsonl` | 控制面事件。包括设备注册、唤醒、音频 session open/close、stream output 请求等。 |
| `tool-events.jsonl` | 新版工具调用日志。包括工具名称、输入参数、返回结果、错误、耗时。 |
| `stream-events.jsonl` | stream 生命周期和 chunk 摘要。终端不逐条打印 chunk，完整明细在这里。 |
| `assets.jsonl` | 资产写入和资产请求记录。拍照、连续 RGB、深度图等传感器资产从这里追。 |

### 输出和播放相关

| 文件 | 用途 |
| --- | --- |
| `output-decisions.jsonl` | Output Service 的输出决策记录。 |
| `playback-decisions.jsonl` | Playback Arbiter 的播放仲裁记录。当前与 `output-decisions.jsonl` 内容高度重叠。 |
| `output-stream_out_<id>.wav` | server 下发给端侧的可播放音频。人工听检优先打开这个。 |
| `output-stream_out_<id>.pcm` | 同一段输出音频的原始 PCM。一般不直接看，供协议和音频排障使用。 |

### 输入原始数据

| 文件 | 用途 |
| --- | --- |
| `input-stream_in_<id>.pcm` | 麦克风上行原始 PCM。排查录音、采样率、VAD、ASR 或 Omni 输入时使用。 |
| `input-stream_rgb_<id>.pcm` | 当前命名不理想，实际可能是 RGB/JPEG 等 sensor payload 的原始字节。应优先通过 `assets.jsonl` 和 `photos/asset_*.jpg` 查看图片。 |

### 回放验收文件

| 文件 | 用途 |
| --- | --- |
| `result.json` | 本轮回放或测试结果摘要。 |
| `playback-result.json` | python-glass / playback endpoint 的回放验收结果。 |
| `actuators.jsonl` | 端侧执行器消费记录，例如 speaker chunk 收到、播放完成。 |

### 调试文件

| 文件 | 状态 |
| --- | --- |
| `model-events.jsonl` | 历史命名，目前基本等同于 `agent-events.jsonl`。 |
| `tool-events.jsonl` | Tool 调用事件。 |

## messages 文件

```text
runs/<app_name>/<user_id>/<device_id>/messages.jsonl
```

保存用户级对话历史。它不是单轮排障的第一入口，而是用于确认长期上下文是否被正确写入。

## assets 目录

```text
runs/<app_name>/<user_id>/<device_id>/photos/asset_*.jpg
```

保存相机等传感器产生的资产文件。拍照工具相关问题，应同时看：

1. `<user_id>/<device_id>/assets.jsonl`
2. `<user_id>/<device_id>/photos/asset_*.jpg`
3. `<user_id>/<device_id>/model-request.json`
4. `<user_id>/<device_id>/tool-events.jsonl`

## tasks 目录

如果任务存储配置为 jsonl，长任务状态会写入：

```text
runs/<app_name>/tasks/
```

日常语音对话排障通常不用看它。排查后台任务恢复、定时任务、长任务状态时再看。

## 后续可整理点

当前 runs 产物已经按 `runs/<app_name>/<user_id>/<device_id>` 分层，仍有一些文件组织可以继续优化：

1. `agent-events.jsonl` 和 `model-events.jsonl` 内容重复。
2. `tool-events.jsonl` 记录 Tool 调用。
3. `output-decisions.jsonl` 和 `playback-decisions.jsonl` 语义重叠。
4. `.pcm` 原始 payload 文件和日常调试文件混在同一层，导致 session 目录很乱。
5. `input-stream_rgb_<id>.pcm` 后缀不准确，RGB/JPEG 资产不应该表现成 `.pcm`。

建议后续把用户设备目录调整为：

```text
<user_id>/<device_id>/
  summary.json
  model-request.json
  events.jsonl
  agent-events.jsonl
  stream-events.jsonl
  tool-events.jsonl
  task-signals.jsonl
  assets.jsonl
  playback-decisions.jsonl
  media/
    input/
      stream_in_<id>.pcm
      stream_rgb_<id>.jpg
    output/
      stream_out_<id>.wav
      stream_out_<id>.pcm
  compatibility/
    model-events.jsonl
    tool-events.jsonl
    output-decisions.jsonl
```

这不是协议变化，只是观测产物目录整理。

## 快速定位命令

查看某个 session 下面有哪些文件：

```bash
find runs/<app_name>/<user_id>/<device_id> -maxdepth 1 -type f | sort
```

查看本轮模型请求：

```bash
cat runs/<app_name>/<user_id>/<device_id>/model-request.json
```

查看本轮工具调用：

```bash
tail -n 50 runs/<app_name>/<user_id>/<device_id>/tool-events.jsonl
```

查看系统错误：

```bash
tail -n 100 runs/<app_name>/system-events.jsonl
```
