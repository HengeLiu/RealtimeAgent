# Vision 链路图片和视频输入设计

## 背景

Vision 链路当前主流程是：

```text
sensor.mic -> ASR -> VisionRealtimeAgentCore -> Vision Model -> Streaming TTS -> actuator.speaker
```

接下来 Vision 类目中的图片理解、视频理解不应该再设计成“Vision 模型调用另一个视觉模型 Tool 辅助理解”。用户指定的 `qwen3.6-flash` 本身就是多模态大模型，可以在同一次模型请求中接收文本、图片和视频内容。因此新的主方案是：

```text
sensor.mic -> ASR -> VisionRealtimeAgentCore -> MultimodalMessageBuilder
  -> qwen3.6-flash(messages with image/video content blocks)
  -> Streaming TTS
```

也就是说，图像 / 视频理解属于 Vision 主模型请求的一部分。系统只需要在模型明确请求视觉资产后采集或选择资产，并把资产以 content block 的形式拼到后续 message 中；不需要额外调用 `interpret_image`、`interpret_video` 这类“理解工具”。

## 核心结论

1. `qwen3.6-flash` 是 Vision 类目的主模型，也是图片 / 视频理解模型。
2. 图片 / 视频不再通过“视觉理解 Tool”单独调用模型；理解发生在 VisionRealtimeAgentCore 发给主模型的同一次请求里。
3. Tool 只负责获取资产，例如拍照、选择视频、截取当前帧；Tool 不负责理解图片内容。
4. `VisionRealtimeAgentCore` 复用现有 `ContextCompiler / ModelContext` 做基础消息管理，并增加同轮工具结果后的多模态 follow-up message 拼接。
5. 模型返回的文本 delta 继续走现有 Vision realtime TTS 链路。

## Message 管理复用结论

当前代码里已经有一层可复用的 Message / Context 管理能力，不是 Omni 专用：

- `ContextCompiler`：统一编译 system prompt、memory、history、当前输入、工具 schema 和 source map。
- `ModelContext`：统一承载 `messages`、`tools`、`modal_inputs`、`context_sources`。
- Vision 链路已经通过 `ContextCompiler.compile(mode="vision")` 获得当前轮 `messages`，再交给 Vision provider 流式调用。
- Realtime Omni 链路也使用 `ContextCompiler.compile(mode="omni")` 生成等价请求视图和运行产物。

因此 Vision 多模态不应该重新实现一套完整历史消息系统，而应该复用这层共享能力。

不能直接复用的是 Omni provider 内部的 conversation item 管理。Omni 的 `_conversation.create_item()`、`append_audio()`、`append_video()`、`commit()`、`response.create()` 处理的是 Realtime provider 的会话事件和音频 / 视频提交语义；Vision provider 使用的是 Chat Completions 风格的 `messages` 数组和 tool call follow-up 语义，两者生命周期不同。

新的 Vision 视觉链路应新增一层 `ModelMessageManager` 或 `MultimodalMessageBuilder`，职责限定为：

1. 基于 `ContextCompiler` 输出的 `ModelContext.messages` 生成 Vision provider 请求。
2. 在同轮工具循环中接收 `capture_photo` / `capture_video_clip` 的 `ToolResult`。
3. 保留标准 tool call / tool result message，确保 provider 工具协议完整。
4. 额外追加一条由系统构造的多模态 follow-up user message，把刚返回的图片 / 视频资产作为 content block 放入下一次模型请求。
5. 记录 source map 和 `model-request.json` 摘要，证明本轮模型确实收到了哪个资产。

### 历史视觉消息治理

Vision 视觉链路不能把历史里的图片描述、相机超时、旧包装识别结果长期裸露在 active messages 中。否则用户换图后再说“朗读图片内容”时，主模型可能把上一张图片的 assistant 回复当成当前上下文，直接回答旧图。

这里不在 `VisionRealtimeAgentCore` 中增加关键词过滤或强制工具调用逻辑。正确边界是复用现有 summary / compact 能力：

1. `ConversationMemoryService` 继续维护三层历史：active messages、summary fragment、完整 audit messages。
2. for-blind-app 将 `user.message_compact_threshold` 调低，只保留最近少量 active messages，把更早历史交给摘要子 Agent。
3. `message_summarizer` 必须把“视觉与环境线索”写成历史观察，不能写成当前图片、当前画面或当前传感器状态。
4. summary fragment 注入主 Agent 时必须说明：摘要里的视觉线索只代表过去某轮观察；新的看图、读图、眼前/前方观察请求仍应依赖当前视觉输入或采集工具。
5. `messages.jsonl`、history 归档和资产文件仍完整保存，供排障复盘；只是这些旧原文不再长期直接进入主模型当前请求。

这条规则的目的不是替模型做视觉意图判断，而是把容易过期的视觉事实从“当前可直接续写的对话”降级成“历史背景”。是否需要调用 `capture_photo` 仍由主模型根据用户本轮问题、工具列表和系统提示决定。

## 与工具调用的边界

### 保留的工具

`capture_photo` 保留并默认暴露给 Vision 主模型，但语义收窄：

- 只负责采集当前 RGB 图片资产。
- 返回 `AssetRef` 和必要元数据。
- 不调用模型，不生成图片解读文本。

后续可新增 `capture_video_clip` 或 `select_video_asset`：

- 只负责采集 / 选择短视频资产。
- 返回 `video_asset_id`、mime type、duration、size。
- 不调用模型，不总结视频。

### 不作为主路径的工具

以下能力不再作为 Vision 多模态主路径：

- `interpret_current_view`
- `interpret_image`
- `interpret_video`

它们可以保留为兼容或调试工具，但默认不应该暴露给 Vision 主模型；否则模型会先调用工具，再由工具调用另一个模型，造成链路重复、延迟增加、上下文不一致。

### 持续任务仍走 Task

找物、红绿灯、持续观察等需要连续帧、多帧稳定判断和端侧状态回报的能力，继续走 peer video Task / Python phone 本地 CV，不用 qwen3.6-flash 单轮请求替代。

## 什么时候拼图片 / 视频

多模态拼接的关键不是“能不能拼”，而是“什么时候拼”。建议按触发来源分四类。

### 1. 模型调用 `capture_photo`

当前画面理解的主路径由模型自己决定是否调用工具：

```text
ASR final
  -> VisionRealtimeAgentCore 编译纯文本请求，工具列表包含 capture_photo
  -> qwen3.6-flash 判断当前问题需要看图，发起 capture_photo tool_call
  -> ToolGateway 执行 capture_photo，返回 AssetRef
  -> ModelMessageManager 追加 tool result message
  -> ModelMessageManager 追加带 image content block 的 follow-up user message
  -> qwen3.6-flash 基于图片继续回答
  -> 文本 delta 进入 TTS
```

这种方式不需要服务端提前做视觉意图判断。是否需要看图由主模型结合用户问题、系统提示和工具描述决定，服务端只负责工具权限、资产拼接和运行产物记录。

首版应把 `capture_photo` 从 Vision denylist 中移除，同时继续默认隐藏 `interpret_current_view`、`interpret_image`、`interpret_video`。

### 2. 用户刚上传 / 选择了图片或视频

如果端侧或 dev-support 已经上传了图片 / 视频，并且本轮用户追问“这张图”“这个视频”“刚才那个画面”，则不重新抓拍，直接引用最近的视觉资产。

这类资产也应该通过 Message 管理层统一拼接，不绕过 `ContextCompiler` 和 source map。

需要约束：

- 最近资产必须属于同一用户、同一设备或明确关联的会话。
- 图片默认 freshness 可以较短，例如 30 秒；视频资产可以按用户选择事件绑定。
- 如果存在多张候选图，必须优先使用本轮显式选择或最新采集的资产，并在 source map 中记录选择原因。

### 3. Realtime 视觉采样已有当前帧

如果后续 Vision 链路也接入类似 Omni 的 visual sampler，且当前语音 turn 内已经采集过新帧，则可直接使用该帧，不重复请求摄像头。

约束：

- 只使用当前语音 turn 内的新鲜帧。
- 不把历史帧默认为当前画面。
- source map 记录 `visual_source=turn_sampler`。

### 4. 普通闲聊或非视觉问题

不拼图片 / 视频。

例如天气、路线、计时器、普通聊天、设备状态等问题，即使端侧有最近图片，也不应自动塞进模型请求，避免模型无关描述画面、增加 token 和延迟。

## 不再做服务端视觉意图判断

首版不做 `detect_visual_intent -> auto capture` 这条服务器预判路径。

原因：

```text
用户问题 -> 主模型 qwen3.6-flash -> capture_photo tool_call
```

模型已经拥有完整用户语义和工具列表，由它决定是否抓拍更符合工具调用范式，也避免服务器维护一套容易误判的关键词规则。

服务器仍需要做的是策略约束：

- 只允许模型调用被配置暴露的工具。
- 单轮最多抓拍次数可配置，避免循环调用。
- `capture_photo` 成功后必须用新拍资产，不用历史图替代。
- 工具失败时把失败原因作为 tool result 返回，让模型播报可理解错误。

## Message 拼接设计

### 单图当前画面工具 follow-up

模型先调用 `capture_photo`：

```json
{
  "role": "assistant",
  "tool_calls": [
    {
      "id": "call_xxx",
      "type": "function",
      "function": {
        "name": "capture_photo",
        "arguments": "{}"
      }
    }
  ]
}
```

VisionRealtimeAgentCore 保留标准 tool result message：

```json
{
  "role": "tool",
  "tool_call_id": "call_xxx",
  "name": "capture_photo",
  "content": "{\"ok\":true,\"asset_id\":\"asset_xxx\",\"mime_type\":\"image/jpeg\"}"
}
```

然后由 Message 管理层追加一条系统构造的 follow-up user message，把图片真正放入下一次主模型请求：

```json
{
  "role": "user",
  "content": [
    {
      "type": "text",
      "text": "capture_photo 刚刚返回了当前画面。请基于下面这张新照片回答用户上一轮问题；不要再次调用 capture_photo；看不清时直接说明看不清。"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/jpeg;base64,..."
      }
    }
  ]
}
```

### 已有图片追问

```json
{
  "role": "user",
  "content": [
    {
      "type": "text",
      "text": "用户语音转写：这张照片左边是什么？\n下面是用户刚才选择或上传的图片。"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/jpeg;base64,..."
      }
    }
  ]
}
```

### 视频直传

如果 provider 支持 video content block 且视频大小 / 时长满足限制：

```json
{
  "role": "user",
  "content": [
    {
      "type": "text",
      "text": "用户语音转写：这个视频里发生了什么？\n请按时间顺序简短总结关键事件。"
    },
    {
      "type": "video_url",
      "video_url": {
        "url": "file-or-signed-url-or-data-url"
      }
    }
  ]
}
```

### 视频抽帧 fallback

如果当前兼容接口不支持 `video_url`，或者视频过大，则在 message 中拼入关键帧：

```json
{
  "role": "user",
  "content": [
    {
      "type": "text",
      "text": "用户语音转写：这个视频里发生了什么？\n下面是同一段视频抽取的关键帧，请结合时间戳总结。"
    },
    {"type": "text", "text": "frame 1, t=0.0s"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
    {"type": "text", "text": "frame 2, t=1.0s"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
  ]
}
```

## Message 管理模块

建议新增 Vision 多模态 message 管理模块，位置在 SDK core 的 Agent Core 边界内，而不是 for-blind Tool 内：

```text
audio-server/realtime_agent/agent_core/multimodal/
  __init__.py
  assets.py
  builder.py
  messages.py
  video_sampling.py
  policy.py
```

| 模块 | 职责 |
| --- | --- |
| `assets.py` | 从 AssetService / 当前 turn 采样 / 设备请求结果中选择视觉资产。 |
| `builder.py` | 把文本、图片、视频拼成 provider message content blocks。 |
| `messages.py` | 管理 Vision provider 的 tool call、tool result 和多模态 follow-up messages。 |
| `video_sampling.py` | 视频抽帧、压缩和时间戳管理。 |
| `policy.py` | 限制最大图片数、视频大小、freshness、单轮抓拍次数和资产类型。 |

VisionRealtimeAgentCore 只调用一个高层接口：

```python
messages, source_records = message_manager.append_tool_result_followup(
    messages=messages,
    tool_call=tool_call,
    tool_result=result_dict,
    user_id=user_id,
    session_id=session_id,
)
```

返回：

```python
MessageUpdate(
    messages=[...],
    assets=[...],
    source_map=[...],
    diagnostics={...},
)
```

## 与 ContextCompiler 的关系

ContextCompiler 继续负责 system prompt、历史消息、工具 schema、memory 和基础 source map。

多模态拼接主要发生在工具返回后的同轮 follow-up 阶段：

```text
ASR final transcript
  -> ContextCompiler.compile(...)
  -> provider.stream_messages(messages=[system, history..., current text user], tools=[capture_photo...])
  -> provider returns capture_photo tool_call
  -> ToolGateway returns AssetRef
  -> ModelMessageManager appends tool result + multimodal follow-up user message
  -> provider.stream_messages(messages=[..., tool result, follow-up image user], tools=...)
```

source map 必须记录：

```json
{
  "source_id": "visual_asset:asset_xxx",
  "source_kind": "modal_input",
  "source_name": "current_view",
  "included": true,
  "asset_id": "asset_xxx",
  "mime_type": "image/jpeg",
  "freshness_ms": 320,
  "reason": "capture_photo_tool_result_followup"
}
```

这样后续排查时可以确认模型到底有没有收到图片 / 视频。

## 配置设计

建议在 `examples/for-blind-app/audio-server/server.yaml` 中把 Vision 主模型升级为多模态模型：

```yaml
agent:
  text:
    provider: "dashscope-compatible"
    model: "qwen3.6-flash"
    multimodal:
      enabled: true
      attach_tool_result_assets: true
      max_images_per_turn: 4
      image_freshness_seconds: 2
      max_image_base64_bytes: 7500000
      max_capture_photo_calls_per_turn: 1
      video:
        enabled: true
        prefer_native_video: true
        max_inline_bytes: 50000000
        max_duration_seconds: 120
        sample_fps: 1.0
        max_frames: 16
        frame_jpeg_quality: 85
```

模型仍应可配置，不把 `qwen3.6-flash` 写死进代码。若 provider 返回模型不可用，启动预检应提示配置错误，而不是静默切到另一个模型。

## Prompt 约束

Text system prompt 需要补充多模态规则：

```text
当用户问题明确涉及当前画面、图片或视频，而当前消息没有附带视觉输入时，你可以调用 capture_photo 获取当前照片。
capture_photo 只负责获取图片，不负责理解图片。工具返回后，系统会在下一次消息中附带刚获取的图片。
图片附带后，你必须只基于本轮附带的视觉输入回答，不要引用历史图片替代当前画面。
看不清、缺少关键帧或无法判断距离时，直接说明不确定，不要编造。
如果用户要求持续寻找某个物体、持续观察、红绿灯通行判断，必须调用对应 Task，不要只凭单张图或短视频给出持续状态结论。
如果用户问题不涉及视觉内容，即使上下文里存在历史视觉资产，也不要主动描述画面。
```

## 视频策略

| 场景 | 拼接方式 | 说明 |
| --- | --- | --- |
| 当前画面 | 模型调用 `capture_photo` 后拼接 1 张 image block | 面向“眼前 / 前面 / 这是什么”。 |
| 图片追问 | 使用最近或指定 image asset | 不重新抓拍。 |
| 短视频理解 | 原生 video block | provider 支持时优先保留时序。 |
| 视频 fallback | 抽关键帧，多 image blocks | provider 不支持 video 或视频过大。 |
| 持续找物 / 红绿灯 | 不拼进普通 message，调用 Task | 需要连续帧和稳定判断。 |

## 运行产物与日志

新增事件：

| 事件 | 含义 |
| --- | --- |
| `multimodal.asset.selected` | 选中某个图片 / 视频资产拼入 message。 |
| `multimodal.tool_asset.attached` | 工具结果中的图片 / 视频资产已拼入 follow-up message。 |
| `multimodal.video.sampled` | 视频抽帧完成，包含 frame_count、fps、duration。 |
| `model.request` | 必须包含当前 message 的多模态 source map 摘要。 |

终端只打印摘要，不打印 base64、不逐帧刷屏。完整资产仍落在 runs 的 assets / photos / video 目录。

## 错误处理

| 错误 | 行为 |
| --- | --- |
| 模型调用 `capture_photo` 但没有 RGB 设备 | tool result 返回失败原因，模型回复“我现在拿不到摄像头画面”。 |
| 图片资产读取失败 | 不调用模型，返回可播报错误。 |
| 图片过大 | 尝试压缩 / 缩放后再拼接；仍失败则报错。 |
| 视频过大 | 抽帧 fallback。 |
| provider 不支持 `video_url` | 抽帧 fallback。 |
| provider 不支持图片 content block | 启动预检失败；这是配置错误。 |

## 分阶段实施

### Phase 1：模型和配置切换

1. `agent.vision.model` 改为 `qwen3.6-flash`。
2. 增加 `agent.vision.multimodal` 配置。
3. 预检确认当前 provider/model 支持图片 content block。
4. Text prompt 增加多模态规则。
5. Text 工具暴露面移除 `capture_photo` denylist，继续隐藏 `interpret_current_view`、`interpret_image`、`interpret_video`。

### Phase 2：Message 管理和工具结果图片拼接

1. 新增 `ModelMessageManager / MultimodalMessageBuilder`。
2. 复用 `ContextCompiler` 输出的基础 `messages`。
3. `capture_photo` tool result 成功后，追加标准 tool result message。
4. 再追加带 image content block 的 follow-up user message。
5. 测试：用户问“前面有什么”时，模型先调用 `capture_photo`，下一次 model request 中包含 image content block。

### Phase 3：图片追问和资产选择

1. 维护最近视觉资产索引。
2. 用户说“这张图 / 刚才那张”时选中最近图片。
3. source map 记录 asset 选择原因。
4. 测试：图片追问不重新抓拍。

### Phase 4：短视频输入

1. 支持视频 asset 进入当前 user message。
2. provider 支持 video block 时直传。
3. 不支持或超限时抽帧为多 image blocks。
4. 测试：视频问题的 model request 中包含 video block 或 frame blocks。

### Phase 5：真实场景验收

1. browser-glass 当前画面问答。
2. browser-glass 图片样例追问。
3. browser-glass 视频样例理解。
4. 验证 runs 中 model-request source map 可复盘图片 / 视频是否真的进入 qwen3.6-flash。
5. 验证普通闲聊不自动附带视觉资产。

## 不做事项

首版不做：

- 不再设计视觉理解 Tool 调另一个 VLM。
- 不让 ToolResult 承载视觉解释文本作为主路径。
- 不做服务器关键词式视觉意图判断来自动抓拍。
- 不把连续 peer video 全量塞进 Vision 模型请求。
- 不用单轮 VLM 替代找物、红绿灯和持续观察 Task。
- 不在控制事件里传图片 / 视频 bytes。

## 关键决策

1. Vision 类目图片 / 视频理解由 `qwen3.6-flash` 主模型完成。
2. 核心实现点是“模型调用采集工具后，把工具返回资产拼到 follow-up message”，不是新增理解工具。
3. `capture_photo` 这类工具只负责采集资产，不负责理解。
4. 当前画面必须通过本轮 `capture_photo` 采集新图；图片追问才允许复用明确关联的历史图片。
5. 每次是否拼接视觉资产必须进入 source map 和 `model-request.json`，作为可复查证据。
6. 复用 `ContextCompiler / ModelContext` 的共享消息管理；不复用 Omni provider conversation item 层。

## 实施记录

### Phase 1：模型和配置切换

- 状态：已完成。
- 实现：
  - `AgentTextConfig` 增加 `multimodal` 配置，运行时同步到 `RealtimeAgentConfig`。
  - 示例应用 Vision 主模型切到 `qwen3.6-flash`。
  - Text prompt 增加 `capture_photo` 和多模态 follow-up 规则。
  - Text 工具 denylist 移除 `capture_photo`，继续隐藏 `interpret_image`、`interpret_current_view`。
  - 预检增加 `text_multimodal` 静态检查，确认 provider/model 与多模态配置基本匹配。
- 文件：
  - `audio-server/realtime_agent/config.py`
  - `audio-server/realtime_agent/app.py`
  - `audio-server/realtime_agent/preflight.py`
  - `examples/for-blind-app/audio-server/server.yaml`
- 验证：
  - `uv run python -m pytest audio-server/protocol-tests/sdk/config/test_config_sync.py -q` 已通过。
  - `uv run realtime-agent.dev.preflight --config examples/for-blind-app/audio-server/server.yaml --report runs/default-app/text-vlm-preflight.json` 已通过。

### Phase 2：Message 管理和工具结果图片拼接

- 状态：已完成。
- 实现：
  - 新增 `realtime_agent.agent_core.multimodal` 模块，包含 policy、assets、builder、messages 和 video_sampling。
  - `VisionRealtimeAgentCore` 初始化 `ModelMessageManager`，复用 `ContextCompiler` 输出的基础 messages。
  - 工具循环保留标准 assistant tool_call message 和 tool result message。
  - `capture_photo` 返回图片 AssetRef 后，追加一条带 `image_url` content block 的 follow-up user message。
  - `model-request.json` 在每次 provider 调用前刷新；对 data URL 做脱敏，只保留 content block 类型和长度。
  - `context_sources` 增加 `visual_asset:*` source map，`agent-events.jsonl` 记录 `multimodal.tool_asset.attached`。
- 文件：
  - `audio-server/realtime_agent/agent_core/multimodal/`
  - `audio-server/realtime_agent/agent_core/text.py`
  - `audio-server/realtime_agent/agent_core/router.py`
- 验证：
  - `uv run python -m pytest audio-server/protocol-tests/sdk/agent_core/test_vision_agent_tool_loop_async.py -q` 已通过。
  - 新增测试确认 mock 模型第一轮调用 `capture_photo` 后，第二轮收到 `image_url` content block。

### Phase 3：图片追问和资产选择

- 状态：部分完成。
- 实现：
  - `ModelMessageManager` 已支持从任意 ToolResult 的 `assets` 或兼容 `data.asset_id/uri/mime_type` 中提取图片资产。
  - 已有图片追问还缺少端侧“用户显式选择图片”的事件入口和最近资产索引，因此本阶段先完成消息拼接能力，未默认复用历史图。
- 待验收 / 后续：
  - 需要补端侧上传 / 选择图片的控制事件或资产选择入口后，再启用“这张图 / 刚才那张”的自动选择。

### Phase 4：短视频输入

- 状态：部分完成。
- 实现：
  - `ModelMessageManager` 已支持 `video/*` AssetRef，在 `video.enabled=true` 且 `prefer_native_video=true` 时生成 `video_url` content block。
  - 抽帧 fallback 目前只保留稳定诊断入口，尚未接入真实视频采集工具和 OpenCV 抽帧链路。
- 待验收 / 后续：
  - 需要先定义 `capture_video_clip` 或 `select_video_asset` 的端侧协议和 Tool，再做真实视频回归。

### Phase 5：真实场景验收

- 状态：待人工验收。
- 已完成的自动验证：
  - 单测覆盖 Text 工具循环、图片 follow-up message、配置解析和预检。
- 待人工验收：
  - browser-glass 问“前面有什么”时，确认模型先调用 `capture_photo`。
  - 确认 runs 中最新 `model-request.json` 包含脱敏后的 `image_url` content block 和 `visual_asset:*` source map。
  - 确认真实 `qwen3.6-flash` 能基于图片回答，并且 TTS 仍按实时链路播放。
