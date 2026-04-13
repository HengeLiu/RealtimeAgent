# agent-core 与 VoiceSessionController 最小运行时设计

## 1. 目标

本设计只解决 [第一期功能开发计划.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/restriction/第一期功能开发计划.md) 第 1、2 项落地时真正需要的服务端运行时边界问题：

- `server-api` 到底负责什么
- 第一期是否必须先实现完整 `agent-core`
- 语音对话链路由谁编排
- 后续 Skill、MCP、后台任务怎样在不推翻首版实现的前提下接入

结论先写清楚：

- 第一期第 1、2 项不以完整 `agent-core` 为前置
- 服务端先实现一个最小 `VoiceSessionController`
- `VoiceSessionController` 负责单设备短期会话上下文、单轮音频聚合、模型调用、回复播放编排
- 后续真正引入 `agent-core` 时，再把 `VoiceSessionController` 作为其内部语音子组件接管

## 2. 第一期最小运行时划分

### 2.1 `server-api`

定位：

- 服务器侧传输与接入边界

职责：

- 接收控制连接与媒体连接
- 解析和校验 `ControlMessage`
- 维护 `device_id -> control_connection`
- 把控制消息路由给对应运行时主体
- 把媒体流交给对应 `VoiceSessionController`
- 对外暴露 `/ws/control`、`/ws_audio`、`/stream.wav` 这类通道

不负责：

- 不负责维护对话上下文
- 不负责判断何时调用模型
- 不负责业务编排和工具调用
- 不把语音会话逻辑散在 WebSocket handler 内

### 2.2 `VoiceSessionController`

定位：

- 第一期语音链路的最小会话编排器

职责：

- 在设备注册成功后自动打开语音会话
- 为每台眼镜维护一条活跃语音会话
- 维护该设备当前会话的短期 Message 上下文
- 接收 `sensor.audio.segment.started`
- 聚合当前 `segment_id` 的 `audio_chunk`
- 在 `sensor.audio.segment.finished` 后先调用独立 ASR
- 把当前轮转写文本写入 `MessageContext` / `DerivedArtifact`
- 再构造对话模型输入
- 调用 `qwen3-omni-flash`
- 在收到首段可播放音频后触发 `actuator.audio.play`
- 持续把回复音频写入播放下行通道
- 在 `actuator.audio.finished` 后恢复待命

不负责：

- 不负责通用 Skill 调度
- 不负责 MCP 管理
- 不负责后台任务管理
- 不负责长期记忆、跨会话上下文和复杂知识库管理

### 2.3 第一期中的 `agent-core`

第 1、2 项阶段，`agent-core` 只作为未来扩展位存在。

建议落地方式：

- 代码目录可以先预留 `agent-core`
- 但运行时只实现一个面向语音的 `VoiceSessionController`
- `VoiceSessionController` 可以先放在 `server` 内部语音模块里
- 后续引入完整 `agent-core` 时，再把它收编成 `agent-core.voice`

这样可以避免为了第 2 项先把 Skill、MCP、记忆、任务编排一并做出来。

## 3. 最小运行时对象

第一期第 1、2 项只需要四类运行时对象：

1. `DeviceConnection`
   - 代表一台已注册设备的控制连接
2. `VoiceSession`
   - 代表一条逻辑语音会话
3. `VoiceSegmentBuffer`
   - 代表当前一轮用户输入的音频缓冲
4. `PlaybackStreamContext`
   - 代表当前回复播放流上下文
5. `MessageContext`
   - 代表当前会话短期消息上下文
6. `MediaAssetRef`
   - 代表一段音频、图片或视频的存储引用

建议最小索引：

- `device_id -> DeviceConnection`
- `device_id -> VoiceSessionController`
- `session_id -> VoiceSession`
- `stream_id -> PlaybackStreamContext`
- `session_id -> MessageContext`
- `asset_id -> MediaAssetRef`

第一期第 1、2 项默认不需要 `task_id -> TaskRuntime`。

## 3.1 Message 上下文模型

多模态模型的会话上下文不能只存纯文本。

建议把上下文拆成三层：

1. `MessageContext`
   - 维护当前会话中的消息顺序
   - 维护每条消息的角色、摘要、模态引用
2. `MediaAssetRef`
   - 维护原始媒体资产引用
   - 维护媒体的存储位置、格式、时长、大小等元信息
3. `DerivedArtifact`
   - 维护从原始模态提取出来的可复用结果
   - 例如语音转写文本、图片摘要、视频摘要、检测结果

核心原则：

- 消息上下文里不直接塞原始音频、图片、视频字节
- 上下文只保留轻量文本和资产引用
- 原始媒体单独存储
- 提交给模型时，再按需把引用解析成模型需要的输入结构

## 3.2 Message 结构建议

建议一条会话消息最少包含：

```json
{
  "message_id": "msg_01J...",
  "session_id": "sess_01J...",
  "role": "user",
  "kind": "multimodal_input",
  "created_at": 1744262400000,
  "text": "帮我看一下前面有什么",
  "asset_refs": [
    "asset_audio_01J...",
    "asset_image_01J..."
  ],
  "derived_refs": [
    "artifact_transcript_01J...",
    "artifact_caption_01J..."
  ],
  "meta": {}
}
```

建议字段：

- `role`
  - `system`、`user`、`assistant`、`tool`
- `kind`
  - `text`
  - `audio_input`
  - `image_input`
  - `video_input`
  - `multimodal_input`
  - `assistant_reply`
- `text`
  - 当前消息的文本部分或摘要
- `asset_refs`
  - 指向原始模态资产
- `derived_refs`
  - 指向转写、摘要、检测结果等派生结果

## 3.3 MediaAssetRef 结构建议

建议一条媒体资产引用至少包含：

```json
{
  "asset_id": "asset_audio_01J...",
  "session_id": "sess_01J...",
  "asset_type": "audio",
  "storage_kind": "file",
  "storage_uri": "runs/session/sess_01J/audio/seg_01J.wav",
  "mime_type": "audio/wav",
  "codec": "pcm16le",
  "created_at": 1744262400000,
  "duration_ms": 3420,
  "bytes": 109440
}
```

扩展到图片和视频时，增加：

- `width`
- `height`
- `frame_count`
- `fps`
- `source_stream_id`

## 3.4 多模态资产的存储位置

多模态模型交互里，图片、视频、语音都可能在多个阶段复用，因此必须有独立存储位置。

第一期建议先用最简单的本地文件存储：

- 用户语音
  - `runs/session/<session_id>/audio/input/<segment_id>.wav`
- 模型回复音频
  - `runs/session/<session_id>/audio/output/<reply_id>.wav`
- 拍照图片
  - `runs/session/<session_id>/image/<capture_id>.jpg`
- 视频片段或关键帧
  - `runs/session/<session_id>/video/<stream_id>/...`
- 转写和摘要
  - `runs/session/<session_id>/artifact/...json`

后续可以替换成对象存储，但对上层 `MediaAssetRef` 接口不应变化。

## 3.5 不同模态在上下文中的保留方式

### 3.5.1 语音

建议保留：

- 原始聚合后的输入 `wav`
- 转写文本
- 必要时保留端点检测、时长等元数据

一般不保留：

- 每个 `20ms` 的原始分片

### 3.5.2 图片

建议保留：

- 原始图片文件
- 图片摘要
- 若后续有视觉工具结果，也作为派生结果挂接

### 3.5.3 视频

视频不应直接进入大模型长期上下文。

建议保留方式：

- 原始视频片段或关键帧目录
- 关键帧摘要
- 结构化检测结果
- 必要时只把“当前轮用到的关键帧”引用给模型

## 3.6 提交给多模态模型时的组装原则

提交给模型前，`VoiceSessionController` 或后续 `agent-core` 应做一次上下文组装：

1. 读取当前 `session_id` 的 `MessageContext`
2. 选取最近若干轮有效消息
3. 把文本消息直接放入 `messages`
4. 把 `asset_refs` 解析为模型所需的音频、图片或视频输入
5. 把过大的视频或长音频先转成摘要、关键帧或裁剪片段

关键点：

- 模型输入是“按需展开”的
- 会话上下文是“轻量引用”的
- 存储层是“独立持久化”的

## 3.7 第 1、2 项的最小上下文要求

即使当前只做语音对话，第 1、2 项也不应完全没有 Message 上下文。

最小要求：

- 一条 `system` 提示词
- 最近若干轮 `user` 文本或语音转写摘要
- 最近若干轮 `assistant` 文本摘要
- 当前轮输入音频的 `asset_ref`
- 当前轮回复音频的 `asset_ref`

第一期不要求：

- 跨天持久记忆
- 多设备共享上下文
- 用户知识库
- 自动长期摘要压缩
- 全量原始视频进入长期会话历史

## 4. 注册成功后的自动动作

设备注册成功后，服务端必须自动做下面几件事：

1. 建立 `device_id -> control_connection` 映射
2. 获取或创建该设备的 `VoiceSessionController`
3. 创建新的 `session_id`
4. 下发 `voice.session.open`
5. 等待设备回 `voice.session.opened`

第 1 项阶段不需要让用户手动触发“开始对话”。

## 5. 语音链路中的职责边界

### 5.1 控制消息流

- `glass-api` 发送 `device.register`
- `server-api` 校验注册并回 `device.registered`
- `VoiceSessionController` 触发 `voice.session.open`
- `glass-api` 回 `voice.session.opened`
- `glass-api` 上报 `sensor.audio.segment.started`
- `glass-api` 上报 `sensor.audio.segment.finished`
- `server-api` 转交给 `VoiceSessionController`
- `VoiceSessionController` 下发 `actuator.audio.play`
- `glass-api` 上报 `actuator.audio.started`
- `glass-api` 上报 `actuator.audio.finished`

### 5.2 媒体流

- 麦克风上行继续走 `/ws_audio`
- 回复下行首版继续走 `/stream.wav`
- `server-api` 只负责接入与转发
- `VoiceSessionController` 负责决定何时开始聚合、何时开始播放、何时结束本轮

### 5.3 MessageContext 与媒体资产

- `server-api` 不直接维护消息上下文
- `VoiceSessionController` 维护当前设备会话的短期 `MessageContext`
- 原始音频、图片、视频不直接放入 `MessageContext`
- `MessageContext` 只持有文本、摘要、`asset_ref`
- 原始媒体统一落到独立存储位置
- 提交给模型时，再由控制器按需把 `asset_ref` 展开成模型输入

## 6. 与后续完整 agent-core 的关系

后续如果进入第 4 项“引入 AgentCore 调用工具”，建议这样演进：

1. 保留 `server-api` 的接入职责不变
2. 把 `VoiceSessionController` 迁入 `agent-core`
3. 在 `VoiceSessionController` 内增加：
   - 上下文维护
   - Tool/Skill 调用
   - MCP 调用
   - 文本与语音统一回复
   - 多模态资产管理
4. 让后台任务相关能力通过 `backend-task-core` 接入

也就是说：

- 第一期第 1、2 项先实现的是“可演进的最小语音运行时”
- 不是一次性把最终 `agent-core` 全部做完

## 7. 第一版不做的东西

第一版明确不做：

- 通用 Agent SDK 封装
- Skill 运行时
- MCP 路由层
- 后台任务管理器
- 多设备共享上下文
- 长记忆
- 复杂状态机引擎

但第一版仍然需要：

- 短期 `MessageContext`
- 原始音频资产落盘
- 语音转写或摘要结果挂接到消息上下文

做到这里，已经足够支撑第 1、2 项真实开发。

## 7.1 引入独立 ASR 后的补充约束

第一期实际落地中，语音链路已经采用“独立 ASR + 对话模型”两阶段。

因此 `VoiceSessionController` 在语音链路中的职责应进一步细化为：

1. 聚合当前轮用户音频。
2. 调用独立 ASR，把当前轮用户语音转成文本。
3. 生成 `DerivedArtifact(transcript)`。
4. 把转写文本写入当前轮 `MessageContext.text`。
5. 仅把文本历史送入对话模型多轮上下文。
6. 把原始音频继续保存在 `asset_refs` 中，供审计、复盘和必要时重新转写。

这意味着：

1. `MessageContext` 的主载体仍然是文本。
2. 原始音频不应充当多轮历史主输入。
3. `DerivedArtifact.transcript` 是语音链路进入 `agent-core` 之前最关键的中间产物。

## 7.2 语音消息在上下文中的推荐保留方式

引入独立 ASR 后，推荐一轮用户语音消息至少保留：

```json
{
  "role": "user",
  "kind": "audio_input",
  "text": "给我讲个笑话",
  "asset_refs": [
    "runs/session/sess_xxx/audio/input/seg_xxx.wav"
  ],
  "derived_refs": [
    "runs/session/sess_xxx/artifact/transcript_seg_xxx.json"
  ]
}
```

对应的派生结果可以是：

```json
{
  "artifact_id": "artifact_transcript_01J...",
  "artifact_type": "transcript",
  "source_asset_id": "asset_audio_01J...",
  "text": "给我讲个笑话",
  "language": "zh",
  "created_at": 1744262400000
}
```

## 7.3 多轮上下文的推荐组装方式

引入独立 ASR 后，提交给对话模型的多轮上下文推荐遵循：

1. `system`：系统提示词。
2. 历史 `user`：使用转写后的文本。
3. 历史 `assistant`：使用回复文本。
4. 当前轮 `user`：优先使用当前轮 ASR 文本。
5. 当前轮原始音频不直接作为多轮历史主输入；若后续模型能力或产品策略需要，可作为补充输入单独开启。

这样做的原因是：

1. 文本历史更稳定，便于回答“我刚才问了什么”这类问题。
2. 文本历史更轻量，便于裁剪和长期演进。
3. 保留音频资产和 transcript 派生结果后，后续仍可扩展更复杂的多模态回放策略。
