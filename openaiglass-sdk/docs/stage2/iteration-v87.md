# sdk-v87 语音轮次意图裁决

## 背景

2026-05-03 真机联调发现，`sdk-v86` 已经支持停止指令和播放中唤醒词打断，但仍缺少完整对话层面的系统状态机：

1. 用户问“今天天气怎么样”这类非视觉问题时，SDK 仍可能在语音段开始阶段提前自动抓拍。
2. 回复播放结束后，背景音或扬声器回灌会触发短语音段。
3. 如果旁路 ASR 尚未最终返回，服务端会先把短音频提交给 Omni，模型可能回复“我在，文刀”并继续自循环。

## 变更

1. 增加 `VoiceTurnIntentDecision`，在语音段进入 Omni/Agent 前做系统层裁决：
   - `stop_conversation`：停止连续对话。
   - `ignore`：忽略误触发语音段。
   - `visual_query`：允许触发并上传自动照片。
   - `voice_query`：普通语音问题，不携带照片。
2. 取消 `sensor.audio.segment.started` 阶段的提前自动抓拍。
3. 只有命中视觉关键词的语音文本才调用自动抓拍并消费本轮照片。
4. 对短语音段增加旁路 ASR 前置等待：
   - 旁路 ASR 在 3 秒内返回有效文本时，用文本裁决。
   - 1.8 秒以内短语音段如果 ASR 仍未返回，按误触发忽略。
5. 在 Omni 返回转写后增加第二道裁决，防止早期裁决无法覆盖的空转写、语气词或助手回声继续进入 Agent。

## 真机验证

已用 macOS 合成语音从电脑扬声器播放：

```bash
say -v Tingting '嗨，乐鑫，今天天气怎么样'
```

观察结果：

1. 非视觉天气问题提交 Omni 时 `image_count=0`，没有自动照片进入模型。
2. 回复播放结束后，眼镜又误触发了一个 1344ms 的短语音段。
3. 服务端在提交 Omni 前关闭预连接，并下发 `voice.dialog.close`：

```text
语音段已由系统意图裁决忽略 segment_id=seg_22_8e1f80fd ... reason=empty_transcript close_continuous_dialog=True
已发送控制消息: voice.dialog.close
```

该误触发轮次没有继续下发 `assistant.reply` 或新的播放请求。

## 自动化验证

已执行：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py \
  openaiglass-sdk/tests/integration/test_control_register_flow.py \
  -q
```

结果通过。保留一个既有 `PytestCollectionWarning`，原因是集成测试里的 `TestWebSocketClient` 是辅助类且定义了 `__init__`。
