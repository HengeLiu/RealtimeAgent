# Omni Realtime 事件试验记录

## 背景

浏览器眼镜端连续多次说“看一下我前面有什么”时，服务端日志曾出现用户转写缺失、重复语音被去重、图片解读不稳定等现象。为了避免继续凭二次封装日志猜测，本次用独立探针直接连接 DashScope Omni Realtime，观察原始服务端事件的时机和关联 ID。

官方文档参考：

- Realtime 总览：https://www.alibabacloud.com/help/en/model-studio/realtime
- Server Events：https://www.alibabacloud.com/help/en/model-studio/server-events
- Client Events：https://www.alibabacloud.com/help/en/model-studio/client-events

## 探针脚本

脚本位置：

```bash
tools/omni_realtime_event_probe.py
```

脚本只做诊断，不接入主服务链路。它会：

- 直接创建 `OmniRealtimeConversation`。
- 发送 16kHz 单声道 PCM16 音频。
- 可选发送一张图片。
- 可选注册一个测试 function tool，并在工具调用完成时回填 `function_call_output`。
- 把原始服务端事件和 `event_id`、`response_id`、`item_id`、`call_id` 写入 JSONL。

## 测试输入

音频：

```bash
testdata/audio-sample/看一下我前面有什么.wav
```

图片：

```bash
examples/simple-agent-server/runs/user-browser-glass-001/dev-browser-glass-001/photos/asset_5c68b990ae0e.jpg
```

图片来自本地运行产物目录，仅作为本次真实链路输入，不应提交。

## VAD 模式试验

### 正确使用方式

`semantic_vad` / `server_vad` 模式下，客户端不能主动提交输入边界或创建响应。客户端只发送音频和图片输入，等待服务端 VAD 自动判断回合结束。

本次修正后的命令：

```bash
uv run python tools/omni_realtime_event_probe.py \
  --audio 'testdata/audio-sample/看一下我前面有什么.wav' \
  --image 'examples/simple-agent-server/runs/user-browser-glass-001/dev-browser-glass-001/photos/asset_5c68b990ae0e.jpg' \
  --with-tool \
  --turn-detection semantic_vad \
  --vad-tail-seconds 1.5 \
  --wait-seconds 12 \
  --out runs/omni-realtime-probe/semantic-vad-no-commit-events.jsonl
```

### 观察到的事件顺序

关键事件顺序如下：

```text
client input_audio_buffer.append
client input_image_buffer.append
server input_audio_buffer.speech_started
server input_audio_buffer.speech_stopped
server conversation.item.created
server input_audio_buffer.committed
server conversation.item.input_audio_transcription.completed
server response.created
server response.output_item.added item_type=function_call
server response.function_call_arguments.done
client conversation.item.create type=function_call_output
client response.create
server response.done
server conversation.item.created type=function_call_output
server response.created
server response.output_item.added item_type=message
server response.audio_transcript.delta / response.audio.delta
server response.audio_transcript.done
server response.audio.done
server response.done
```

### 结论

VAD 模式下，Omni 服务器会自己产生 `speech_stopped`、`input_audio_buffer.committed` 和第一段 `response.created`。之前在 VAD 模式主动调用 `commit()` 的探针用法是错误测试方法，不能作为结论依据。

## Manual 模式试验

### 正确使用方式

`manual` 模式下关闭服务端 turn detection：

```text
enable_turn_detection = false
```

客户端需要在输入发送完后主动执行：

```text
input_audio_buffer.commit
response.create
```

本次检查命令：

```bash
uv run python tools/omni_realtime_event_probe.py \
  --audio 'testdata/audio-sample/看一下我前面有什么.wav' \
  --image 'examples/simple-agent-server/runs/user-browser-glass-001/dev-browser-glass-001/photos/asset_5c68b990ae0e.jpg' \
  --with-tool \
  --turn-detection manual \
  --wait-seconds 12 \
  --out runs/omni-realtime-probe/manual-checked-events.jsonl
```

### 观察到的事件顺序

关键事件如下：

```text
4210ms client input_audio_buffer.commit
4210ms client response.create
4476ms server input_audio_buffer.committed item_id=item_LIZHB4E6jE6c2fZAEzwy7
4476ms server conversation.item.created item_type=message
4657ms server conversation.item.input_audio_transcription.completed text=看一下我前面有什么。
4850ms server response.created response_id=resp_XyVUONonHLGYAi1dvzc9d
4851ms server response.output_item.added item_type=function_call call_id=call_a6097c792b8c4aeeaaabe5c8
5258ms server response.function_call_arguments.done call_id=call_a6097c792b8c4aeeaaabe5c8
5259ms client conversation.item.create type=function_call_output
5260ms client response.create
5266ms server response.done response_id=resp_XyVUONonHLGYAi1dvzc9d status=completed
5578ms server response.created response_id=resp_GVF4n3mVwhFgA2SlVfFPq
5579ms server response.output_item.added item_type=message
7637ms server response.audio_transcript.done text=你前面有一只戴着黄色花朵项圈的小猫，它正坐在地上。旁边是它的食盆和水碗，里面装着猫粮和水。
7638ms server response.done response_id=resp_GVF4n3mVwhFgA2SlVfFPq status=completed
```

### 结论

Manual 模式这次测试方法是正确的：客户端显式提交输入边界并创建响应，服务端先生成工具调用响应；客户端回填 `function_call_output` 后，再显式创建第二个响应，服务端返回最终语音和文本回答。

需要注意的是，工具调用阶段和最终回答阶段是两个不同 `response_id`：

- 工具调用响应：`resp_XyVUONonHLGYAi1dvzc9d`
- 最终回答响应：`resp_GVF4n3mVwhFgA2SlVfFPq`

因此主链路不能只用一个全局布尔值判断“当前 response 是否工具调用”，必须按 `response_id` / `item_id` / `call_id` 建状态。

## 后续使用 Omni Realtime 的注意事项

1. VAD 模式和 Manual 模式不能混用。
   - VAD 模式只 append 音频和图片，等待服务端自动 commit 和创建响应。
   - Manual 模式才由客户端主动 commit，再主动 response.create。

2. 日志和状态机必须保存官方关联 ID。
   - 用户输入应按 `item_id` 记录。
   - 每段响应应按 `response_id` 记录。
   - 工具调用应按 `call_id` 记录。
   - 不应该用文本内容作为去重 key。

3. `conversation.item.input_audio_transcription.completed` 是输入音频转写结果，不等于模型最终理解。
   - 它可用于调试用户说了什么。
   - 不能用它推断模型是否已经完整处理视觉输入。

4. 工具调用至少包含两个阶段。
   - 第一段 response 产生 function call。
   - 客户端执行工具并创建 `function_call_output`。
   - 客户端再创建后续 response，模型才会基于工具结果回答。

5. 视觉输入必须和当前 turn 明确绑定。
   - 如果图片来自工具结果，回填后要确保后续 response 和这次 `call_id` / 图片资产有关联日志。
   - 历史图片、历史回答和当前图片冲突时，业务提示词应要求以当前图片为准。

6. 当前主链路需要重点修正的方向。
   - 不再用 `(session_id, transcript)` 去重用户输入。
   - `_summarize_omni_event()` 需要展开 `response_id`、`item_id`、`call_id`。
   - response 音频抑制和工具状态要按 `response_id` 建状态机。
   - VAD 模式下不要在端侧 final chunk 或工具回填后随意调用 commit。
   - 工具 schema 应优先按官方嵌套结构验证和实现，避免依赖非文档兼容行为。

## realtime-agent 当前 Omni 链路试验

### 测试目标

前面的试验验证的是 DashScope Omni Realtime 原生协议。这里进一步验证当前 `realtime-agent` 主链路里的 `QwenOmniRealtimeAdapter`，也就是：

```text
用户音频 -> Omni 触发 capture_photo -> adapter 回填工具结果 -> adapter 重放音频并追加图片 -> Omni 返回最终回答
```

探针脚本：

```bash
tools/realtime_agent_omni_chain_probe.py
```

这个脚本直接使用当前 `realtime_agent.conversation.core.omni_host.QwenOmniRealtimeAdapter`，不绕过 adapter 内部逻辑。

### VAD 输入，不发送 final

命令：

```bash
uv run python tools/realtime_agent_omni_chain_probe.py \
  --audio 'testdata/audio-sample/看一下我前面有什么.wav' \
  --image 'examples/simple-agent-server/runs/user-browser-glass-001/dev-browser-glass-001/photos/asset_5c68b990ae0e.jpg' \
  --wait-seconds 18 \
  --out runs/omni-realtime-probe/realtime-agent-chain-flat-vad-events.jsonl
```

关键现象：

```text
4826ms server conversation.item.input_audio_transcription.completed text=看一下我前面有什么。
4919ms server response.created
4919ms server response.output_item.added tool_name=capture_photo
5338ms server response.function_call_arguments.done tool_name=capture_photo
5338ms adapter tool_done
5341ms adapter replay_audio prepared
5681ms adapter omni.capture_photo.image_appended image_sha256=1954e20085464420203eedec9e69cf5c01de7acd8e60a7424a7d1cee463812c4
5682ms server response.done status=completed
5803ms server response.audio_transcript.done transcript=""
5804ms server response.done status=cancelled
5907ms server error Error append image before append audio.
5907ms server error Error committing input audio buffer: buffer too small, or have no audio.
22389ms summary audio_delta_count=0 audio_done_count=0
```

结论：

- 用户音频提交成功。
- Omni 正确触发了 `capture_photo` 工具调用。
- 当前 adapter 确实执行了工具结果回填、音频重放和图片追加。
- 但是图片追加后的后续回答没有成功产生；没有任何有效 `assistant_audio.delta`。
- 当前 adapter 记录的 `response_create=provider_auto_after_commit` 不可靠。
- `append_video + commit` 的时机不符合这次真实链路表现，服务端返回了图片/音频 buffer 相关错误。

### VAD 输入，发送 final

命令：

```bash
uv run python tools/realtime_agent_omni_chain_probe.py \
  --audio 'testdata/audio-sample/看一下我前面有什么.wav' \
  --image 'examples/simple-agent-server/runs/user-browser-glass-001/dev-browser-glass-001/photos/asset_5c68b990ae0e.jpg' \
  --send-final \
  --wait-seconds 18 \
  --out runs/omni-realtime-probe/realtime-agent-chain-flat-send-final-events.jsonl
```

关键现象：

```text
4454ms adapter omni.input.committed reason=final_chunk
4769ms server conversation.item.input_audio_transcription.completed text=看一下我前面有什么。
5166ms server response.created
5166ms server response.output_item.added tool_name=capture_photo
5648ms server response.function_call_arguments.done tool_name=capture_photo
5784ms adapter omni.capture_photo.image_appended image_sha256=1954e20085464420203eedec9e69cf5c01de7acd8e60a7424a7d1cee463812c4
5785ms server response.done status=completed
6012ms server response.audio_transcript.done transcript=""
6012ms server response.done status=cancelled
6172ms server conversation.item.input_audio_transcription.completed text=看一下我前面有什么。
22481ms summary audio_delta_count=0 audio_done_count=0
```

结论：

- 发送 final 后，当前 adapter 会额外触发一次 `omni.input.committed reason=final_chunk`。
- 工具调用和图片追加仍然发生。
- 图片追加后仍没有产生有效最终回答。
- 重放用户音频后，服务端又产生了一次同样的输入转写，说明当前“工具回填后重放原始音频 + append image + commit”的策略会制造新的输入 item，而不是稳定地把图片作为工具结果的后续回答上下文。

### 当前主链路问题判断

这次测试说明，当前 `realtime-agent` 的 Omni 链路不是“工具没有调用”或“图片没有追加”，而是工具结果后的视觉输入编排方式有问题：

```text
function_call_output -> replay original audio -> append image -> commit -> 依赖 provider 自动 response
```

这个流程在真实 Omni 上没有产生稳定最终回答。更可靠的方向应该回到官方事件模型：

```text
第一段 response 产生 function_call
等待第一段 response.done
conversation.item.create 写入 function_call_output
按官方允许的输入模式追加图片
明确创建后续 response
用 response_id 追踪后续 message/audio
```

其中，是否需要重放用户音频、图片应该使用 `append_video` 还是官方文档中的 image buffer 事件，需要继续按 DashScope SDK 和官方文档确认，不能再依赖 `provider_auto_after_commit` 这种隐式行为。

## VAD 同步提交图片、无工具方案试验

### 测试目标

验证另一个更简单的方案：在 VAD 模式下，每个用户音频 turn 同时提交当前图片，不通过 `capture_photo` 工具。提示词约束模型：

```text
如果用户的问题涉及图片、眼前、前面、看到、画面、这是什么等视觉内容，请基于这张图片简短回答。
如果用户的问题不涉及图片内容，请直接回答用户问题，不要描述图片，也不要提到你收到了图片。
```

探针发送方式：

- 使用 `semantic_vad`。
- 不注册工具。
- 图片使用 `testdata/image-sample/刚子等饭吃.jpeg`。
- 先测试“第一片音频 append 后立即 append 图片”，再测试“音频主体结束、静音尾巴开始前 append 图片”。
- 音频结束后补 1.5 秒静音，等待服务端 VAD 自动提交和创建响应。
- 不主动 `commit()`，不主动 `response.create()`。

### 非视觉问题 + 图片

命令：

```bash
uv run python tools/omni_realtime_event_probe.py \
  --audio 'testdata/audio-sample/你是谁呀.wav' \
  --image 'testdata/image-sample/刚子等饭吃.jpeg' \
  --image-position after_first_audio \
  --turn-detection semantic_vad \
  --vad-tail-seconds 1.5 \
  --wait-seconds 12 \
  --instructions '你是中文语音助手。每个用户语音输入可能同时附带一张当前视野图片。如果用户的问题涉及图片、眼前、前面、看到、画面、这是什么等视觉内容，请基于这张图片简短回答。如果用户的问题不涉及图片内容，请直接回答用户问题，不要描述图片，也不要提到你收到了图片。' \
  --out runs/omni-realtime-probe/vad-image-nonvisual-gangzi-events.jsonl
```

关键结果：

```text
input transcript: 你是谁呀？
assistant transcript: 我是你的中文语音助手，有什么需要帮忙的随时告诉我。
response.done status=completed
```

结论：

- 同 turn 携带图片时，如果用户问题不涉及视觉内容，模型没有描述图片。
- 提示词约束有效。

### 非视觉问题批量稳定性测试

为了确认“非视觉问题 + 同步图片”不是单条样本偶然成立，又使用 `testdata/audio-sample/` 下 23 条非视觉离线音频做批量测试。测试方式保持一致：

- `semantic_vad` 模式。
- 不注册工具。
- 每条音频第一片 append 后立即 append 同一张测试图片。
- 音频结束后补 1.5 秒静音。
- 不主动 `commit()`，不主动 `response.create()`。
- 输出事件写入 `runs/omni-realtime-probe/nonvisual-image-batch/`。

批量命令由临时 Python 驱动脚本顺序调用 `tools/omni_realtime_event_probe.py`，等价于对每条样本执行下面的命令：

```bash
uv run python tools/omni_realtime_event_probe.py \
  --audio 'testdata/audio-sample/<样本名>.wav' \
  --image 'testdata/image-sample/刚子等饭吃.jpeg' \
  --image-position after_first_audio \
  --turn-detection semantic_vad \
  --vad-tail-seconds 1.5 \
  --wait-seconds 10 \
  --instructions '你是中文语音助手。每个用户语音输入可能同时附带一张当前视野图片。只有当用户明确询问图片、眼前、前面、看到的画面、这是什么、周围有什么等视觉内容时，才基于这张图片回答。如果用户的问题不涉及视觉内容，请直接回答用户问题，不要描述图片，也不要提到你收到了图片。' \
  --out 'runs/omni-realtime-probe/nonvisual-image-batch/<样本名>.jsonl'
```

测试样本覆盖：

- 助手身份和自我介绍：`你是谁呀.wav`、`自我介绍一下.wav`
- 普通知识和闲聊：`给我讲个笑话吧.wav`、`帮我查一下大模型是什么.wav`
- 天气、个人信息和记忆：`帮我查一下今天的天气.wav`、`我叫什么呀.wav`、`我的住址在哪里.wav`、`我刚才问了你什么.wav`
- 用户偏好和住址记录：`我叫文刀文字的文刀锋的刀.wav`、`我喜欢爬山吃火锅但我不会游泳.wav`、`我家在上海市徐汇区沄柏中心.wav`
- 提醒和计时器：`一分钟后提醒我.wav`、`一分钟后提醒我看一眼手机.wav`、`设置一个一分钟的计时器到时间后提醒我.wav`、`帮我设置一个三分钟的计时器.wav`
- 设备控制和状态：`把眼睛的音量调大.wav`、`帮我查一下我眼镜的状态.wav`
- 导航意图：`回家.wav`、`步行回家.wav`、`坐公交地铁回家.wav`、`导航到最近的地铁站.wav`、`带我去虹漕路地铁站.wav`、`看一下最近的地铁站怎么去.wav`

统计结果：

```text
总样本数：23
成功完成 response.done 的样本数：23
直接描述当前测试图片内容的样本数：0
出现视觉能力或环境边界表达的样本数：2
```

其中两条边界表达如下：

```text
样本：自我介绍一下.wav
用户转写：自我介绍一下。
助手回答：你好呀，我是你的中文语音助手。平时不管是查资料、定闹钟还是聊聊天，你随时开口我就在。要是你眼前有什么拿不准的东西，直接拍张照问我，我也能帮你认出来。咱们这就开始吧，有什么想做的尽管吩咐。

样本：看一下最近的地铁站怎么去.wav
用户转写：看一下最近的地铁站怎么去。
助手回答：我这边看不到你周围的环境，所以没法直接告诉你最近的地铁站怎么走。你可以打开手机上的地图软件搜一下，或者告诉我你现在的具体位置，我帮你查查路线。
```

结论：

- 这 23 条非视觉问题里，没有发现模型把同步上传的测试图片内容错误带入回答。
- “自我介绍一下”没有描述当前图片，但模型主动介绍了视觉识别能力；如果产品不希望普通自我介绍提到拍照，需要在系统提示词中单独限制自我介绍内容。
- “看一下最近的地铁站怎么去”没有描述当前图片，但“看一下”这个口语表达让模型说出“看不到周围环境”。这说明不能只按“看一下”判断视觉意图，应该在提示词或上层意图判断中区分“看一下路线/查一下信息”和“看一下眼前/画面/这是什么”。
- 当前批量结果支持继续验证“VAD 同步图片、无工具”的方向，但真实浏览器连续对话还需要观察跨 turn 图片是否串扰。

### 视觉问题 + 图片：第一片音频后立即追加图片

这个测试使用同一张猫图，但在第一片音频后立即发送图片：

命令：

```bash
uv run python tools/omni_realtime_event_probe.py \
  --audio 'testdata/audio-sample/看一下我前面有什么.wav' \
  --image 'testdata/image-sample/刚子等饭吃.jpeg' \
  --image-position after_first_audio \
  --turn-detection semantic_vad \
  --vad-tail-seconds 1.5 \
  --wait-seconds 12 \
  --instructions '你是中文语音助手。每个用户语音输入可能同时附带一张当前视野图片。如果用户的问题涉及图片、眼前、前面、看到、画面、这是什么等视觉内容，请基于这张图片简短回答。如果用户的问题不涉及图片内容，请直接回答用户问题，不要描述图片，也不要提到你收到了图片。' \
  --out runs/omni-realtime-probe/vad-image-visual-gangzi-events.jsonl
```

关键结果：

```text
input transcript: 看一下我前面有什么。
assistant transcript: 你前面是一台白色的笔记本电脑，屏幕亮着，上面显示着一些文字和图表。电脑旁边放着一个黑色的鼠标和一个白色的马克杯。
response.done status=completed
```

结论：

- 这次回答明显不符合 `testdata/image-sample/刚子等饭吃.jpeg`，图片里实际是一只戴黄色花朵项圈的猫，旁边有食盆和水碗。
- 因此不能认为“第一片音频后立即 append 图片”已经可靠绑定到当前 turn。
- 关键风险是：脚本在 `session.update` 后立刻开始发送音频和图片，而服务端 `session.updated` 可能尚未返回；图片虽然被客户端发送，但视觉内容没有稳定进入本轮回答。

### 视觉问题 + 图片：音频主体结束后追加图片

为了确认是否是图片发送时机问题，同样使用猫图，但把图片放到音频主体结束、静音尾巴开始前发送：

```bash
uv run python tools/omni_realtime_event_probe.py \
  --audio 'testdata/audio-sample/看一下我前面有什么.wav' \
  --image 'testdata/image-sample/刚子等饭吃.jpeg' \
  --image-position before_tail \
  --turn-detection semantic_vad \
  --vad-tail-seconds 1.5 \
  --wait-seconds 12 \
  --instructions '你是中文语音助手。每个用户语音输入可能同时附带一张当前视野图片。如果用户的问题涉及图片、眼前、前面、看到、画面、这是什么等视觉内容，请基于这张图片简短回答。如果用户的问题不涉及图片内容，请直接回答用户问题，不要描述图片，也不要提到你收到了图片。' \
  --out runs/omni-realtime-probe/vad-image-visual-gangzi-before-tail-events.jsonl
```

关键结果：

```text
input transcript: 看一下我前面有什么。
assistant transcript: 你前面有一只戴着黄色花朵项圈的猫咪，它正坐在地上。旁边还有它的食盆和水碗。
response.done status=completed
```

结论：

- 同一张猫图，在音频主体结束后、VAD 自动提交前 append，模型能正确基于图片回答。
- “VAD 同步图片、无工具”方向仍然可行，但图片发送时机不能太早。
- 主链路实现时需要等 `session.updated` 后再接受输入，并尽量在用户语音结束附近抓拍和 append 图片。
- 这个方案仍然不需要工具调用，不需要工具结果回填，不需要重放用户音频，也不需要额外创建第二段 response。

### VAD 期间每秒重复提交图片

为降低单次图片发送时机不准带来的风险，又测试了“从输入开始后每秒重复提交同一张图片”的方案。这个测试仍然使用同一张猫图，重点观察两个问题：

- 非视觉问题是否会因为多次收到图片而错误描述图片。
- 视觉问题是否能稳定使用图片内容。

探针脚本新增临时参数：

```bash
--repeat-image-seconds 1.0
```

同时，探针在发送 `session.update` 后等待 `session.updated`，再开始发送音频和图片，避免 session 配置尚未生效就提前提交图片。

非视觉问题命令：

```bash
uv run python tools/omni_realtime_event_probe.py \
  --audio 'testdata/audio-sample/你是谁呀.wav' \
  --image 'testdata/image-sample/刚子等饭吃.jpeg' \
  --repeat-image-seconds 1.0 \
  --turn-detection semantic_vad \
  --vad-tail-seconds 1.5 \
  --wait-seconds 12 \
  --instructions '你是中文语音助手。每个用户语音输入可能同时附带一张当前视野图片。如果用户的问题涉及图片、眼前、前面、看到、画面、这是什么等视觉内容，请基于这张图片简短回答。如果用户的问题不涉及图片内容，请直接回答用户问题，不要描述图片，也不要提到你收到了图片。' \
  --out runs/omni-realtime-probe/vad-repeat-image-nonvisual-gangzi-wait-session-events.jsonl
```

关键结果：

```text
input_image_buffer.append repeat_0.00s
input_image_buffer.append repeat_1.01s
input_image_buffer.append repeat_2.03s
input_image_buffer.append repeat_3.05s
input transcript: 你是谁呀？
assistant transcript: 我是你的中文语音助手，有什么可以帮你的吗？
response.done status=completed
```

视觉问题命令：

```bash
uv run python tools/omni_realtime_event_probe.py \
  --audio 'testdata/audio-sample/看一下我前面有什么.wav' \
  --image 'testdata/image-sample/刚子等饭吃.jpeg' \
  --repeat-image-seconds 1.0 \
  --turn-detection semantic_vad \
  --vad-tail-seconds 1.5 \
  --wait-seconds 12 \
  --instructions '你是中文语音助手。每个用户语音输入可能同时附带一张当前视野图片。如果用户的问题涉及图片、眼前、前面、看到、画面、这是什么等视觉内容，请基于这张图片简短回答。如果用户的问题不涉及图片内容，请直接回答用户问题，不要描述图片，也不要提到你收到了图片。' \
  --out runs/omni-realtime-probe/vad-repeat-image-visual-gangzi-wait-session-events.jsonl
```

关键结果：

```text
input_image_buffer.append repeat_0.00s
input_image_buffer.append repeat_1.01s
input_image_buffer.append repeat_2.03s
input_image_buffer.append repeat_3.03s
input_image_buffer.append repeat_4.04s
input transcript: 看一下我前面有什么。
assistant transcript: 你前面有一只戴着黄色花朵项圈的猫咪，旁边还有它的食盆和水碗。
response.done status=completed
```

结论：

- 等待 `session.updated` 后，VAD 期间每秒重复提交图片可以覆盖图片发送时机问题。
- 非视觉问题在重复提交 4 张同图的情况下没有被图片干扰。
- 视觉问题在重复提交 5 张同图的情况下正确基于图片回答。
- 这个方案比“只在第一片音频后提交一次图片”更稳，也比工具链路简单。
- 后续真实实现不应长期重复发送同一张图片，而应该从语音开始后定时抓取当前帧；可以先按 1 秒间隔实现，后续根据带宽和延迟再调参。

### VAD 每秒重复提交图片的小批量复测

为了确认上面的结果不是单条样本偶然成立，又做了一组小批量复测：

- 视觉问题重复跑 3 次。
- 非视觉和弱视觉口语问题跑 5 条。
- 每条都等待 `session.updated` 后再开始输入。
- 每条都按 `--chunk-ms 20`、`--sleep-ms 20` 接近实时发送音频，不是一次性灌入。
- 每条都使用 `--repeat-image-seconds 1.0`，按发送过程中的真实 wall-clock 时间约每秒重复提交同一张猫图。

输出目录：

```bash
runs/omni-realtime-probe/repeat-image-suite/
```

统计结果：

```text
总样本数：8
成功完成 response.done 的样本数：8
视觉问题正确识别猫图的样本数：3/3
非视觉或路线类问题错误描述猫图的样本数：0/5
```

逐条结果：

```text
visual_1
图片提交次数：5
用户转写：看一下我前面有什么。
助手回答：你前面有一只戴着黄色向日葵项圈的猫，它旁边还放着食盆和水碗。

visual_2
图片提交次数：5
用户转写：看一下我前面有什么。
助手回答：你前面有一只戴着黄色向日葵脖圈的猫，它旁边还有食盆和水碗。

visual_3
图片提交次数：5
用户转写：看一下我前面有什么。
助手回答：你前面有一只戴着黄色向日葵脖圈的猫，旁边还有它的食盆和水碗。

nonvisual_who
图片提交次数：3
用户转写：你是谁呀？
助手回答：我是你的智能语音助手，有什么需要帮忙的随时叫我。

nonvisual_weather
图片提交次数：5
用户转写：帮我查一下今天的天气。
助手回答：好的，请告诉我您想查询哪个城市的天气？

nonvisual_timer
图片提交次数：6
用户转写：帮我设置一个三分钟的计时器。
助手回答：好的，已经帮你设置了一个三分钟的计时器。

nonvisual_joke
图片提交次数：4
用户转写：给我讲个笑话吧。
助手回答：好啊，给你讲一个。有一天，0在街上遇到了8，0就很不屑地看了它一眼，说，胖就胖呗，还系什么腰带啊。

ambiguous_route
图片提交次数：6
用户转写：看一下最近的地铁站怎么去。
助手回答：好的，请问您现在的具体位置是？或者告诉我您想从哪个地点出发，我帮您规划一下去最近地铁站的路线。
```

补充结论：

- 重复图片方案在这组样本里明显优于“只在第一片音频后提交一次图片”。
- 对“看一下最近的地铁站怎么去”这类带“看一下”的非视觉问题，提示词需要明确把“看天气、看路线、看资料、看状态”排除出视觉问题，否则模型可能把“看一下”误解成视觉请求。
- 真正接入浏览器端时，图片提交节奏应该跟真实语音流时间一致；测试探针当前通过 20ms 音频分片和 20ms sleep 做近似实时发送。

### 方案风险

- 每个 turn 都上传图片会增加带宽和模型侧输入成本。
- 端侧必须保证图片足够接近用户提问时刻，否则“当前视野”语义会漂移。
- 图片 append 时机必须落在当前 turn 内，且要晚于有效 session 配置生效；过早发送有可能导致模型没有使用这张图片。
- 重复提交图片会进一步增加带宽和模型侧输入量；如果图片分辨率较高，必须评估浏览器端、真实眼镜端和移动网络下的开销。
- 后续仍要在 browser-glass 开发支持组件和真实眼镜端验证连续多轮输入，确认图片不会串到下一轮 turn。
