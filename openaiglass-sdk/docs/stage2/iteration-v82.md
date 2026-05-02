# sdk-v82 配置分层与 YAML 化

## 背景

服务端配置长期堆在 `local_server.env` 中，模型、语音链路、设备令牌、日志、记忆和工具前置播报混在一起。随着 Omni Realtime、旁路 ASR、TTS、工具前置播报、长期记忆等配置增多，继续用扁平 env 文件很难看出配置项之间的组合关系。

## 配置分层

新的业务侧推荐配置是：

```text
openaiglass-for-blind/config/local_server.yaml  # 非敏感运行配置，不提交真实本地文件
openaiglass-for-blind/config/.env               # API Key 等敏感信息，不提交
```

`local_server.yaml.example` 按以下层次组织：

```text
app                 运行环境
server              监听地址、端口、局域网公开地址
logging             日志级别和日志文件
devices             服务端、眼镜、手机设备编号和配对令牌
heartbeat           心跳间隔和超时
models              base_url、Agent、Omni Realtime、ASR、TTS
voice               会话模式、回复模式、连续对话、turn detection、落盘目录
tools               工具前置播报全局开关和音频模式
agent.memory        长期记忆开关、路径和提示词注入数量
```

敏感信息不进入 YAML。当前 `.env.example` 只保留：

```bash
DASHSCOPE_API_KEY=""
```

## 兼容策略

1. `openaiglass.server.start --config` 支持 `.yaml/.yml` 和旧 `.env` 格式。
2. 如果传入 YAML，启动器会把分层配置转换为现有运行时环境变量，`ServerSettings` 暂不感知 YAML 结构，降低改动风险。
3. 同目录 `.env` 会自动加载，适合放 `DASHSCOPE_API_KEY`。
4. 旧的 `local_server.env` 仍可用，便于已有部署平滑迁移。
5. `openaiglass.config.sync` 支持从 YAML 读取 `server.public_host`、`server.port` 和 `devices.tokens`，并能回写 `server.public_host`。

## 关键映射

| YAML 路径 | 运行时环境变量 |
| --- | --- |
| `server.host` / `server.port` | `HOST` / `PORT`，启动器再同步为 `SERVER_HOST` / `SERVER_PORT` |
| `server.public_host` | `SERVER_PUBLIC_HOST` |
| `devices.tokens` | `DEVICE_TOKEN_MAP` |
| `models.agent.model` | `AGENT_MODEL_NAME` |
| `models.voice.model` / `models.voice.voice` | `VOICE_MODEL_NAME` / `VOICE_MODEL_VOICE` |
| `models.omni_realtime.*` | `VOICE_OMNI_REALTIME_*` |
| `models.asr.*` | `VOICE_ASR_*` |
| `models.tts.*` | `TTS_*` |
| `voice.*` | `VOICE_*` 和 `MAX_SEGMENT_AUDIO_BYTES` |
| `tools.progress_audio.enabled` | `TOOL_PROGRESS_AUDIO_ENABLED` |
| `tools.progress_audio.mode` | `TOOL_PROGRESS_AUDIO_MODE` |
| `agent.memory.*` | `AGENT_MEMORY_*` |

## 验证

已新增单元测试覆盖：

1. YAML 分组配置转换为运行时 env。
2. 同目录 `.env` 注入 `DASHSCOPE_API_KEY`。
3. 旧 env 配置读取仍保持兼容。

## 后续修正：工具前置播报音频来源

`tools.progress_audio.mode=realtime` 不再固定走 TTS。实际音频生成方跟随当前主回复链路：

1. `voice.reply_mode=omni_realtime` 时，工具前置播报使用 `models.omni_realtime.model` 和 `models.voice.voice` 创建独立 Omni Realtime 会话生成音频。
2. `voice.reply_mode=agent_tts` 时，工具前置播报使用 `models.tts.model` 和 `models.tts.voice` 创建流式 TTS 会话生成音频。
3. `cached` 缓存只服务于 TTS 主链路；Omni 主链路不会复用 TTS 缓存，避免提示音和最终回复音色、情感不一致。

## 后续修正：工具前置播报全局开关与缓存校验

`tools.progress_audio.enabled` 是工具前置播报的全局开关：

1. `true` 时，SDK 仍按模型首输出类型自动判定是否播报：首输出为工具调用才播，首输出为文本或音频不播。
2. `false` 时，即使 Tool 配置了 `progress_message`，调用工具前也不会插入任何提示音。
3. `tools.progress_audio.mode=cached` 且主链路为 TTS 时，Server 启动会读取当前工具注册表的所有 `progress_message`，按当前文本、TTS 模型、音色、采样率生成缓存指纹。
4. 如果某个 Tool 删除了 `progress_message`，对应旧缓存会在启动阶段被清理。
5. 如果某个 Tool 修改了 `progress_message`，旧文案缓存会被清理，新文案会重新生成离线音频。
6. `mode=realtime` 或 Omni 主链路不依赖离线提示音缓存，启动时不需要更新提示音文件。
