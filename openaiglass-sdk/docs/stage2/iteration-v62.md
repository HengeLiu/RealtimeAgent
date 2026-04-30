# iteration-v62：播放中候选段显式标记与回声丢弃

对应对外 SDK 版本：`sdk-v62`。

## 背景

`sdk-v61` 把播放中打断从端侧本地 VAD 改为 Omni `semantic_vad` 确认后，真机仍出现“把眼镜自己的播放声收进去”的现象。日志表现为：

1. 播放流首段 PCM 已写入扬声器。
2. 约 1 秒后端侧打印 `播放中 VAD 触发候选语音段，等待 Omni semantic_vad 确认`。
3. 旧播放自然结束后，服务端仍基于候选音频生成下一条回复，导致再次启动播放。

根因是服务端只依赖本地 `current_playback` 推断候选段是否发生在播放中；当控制侧播放状态已经收尾或被清理时，候选段会被误当成普通新语音段。端侧才真正知道本地 VAD 命中时是否仍处在扬声器播放期间，因此需要把这个事实显式带到控制消息里。

## 本轮改动

1. ESP32 在播放中候选段的 `sensor.audio.segment.started` payload 中新增 `started_during_playback=true`。
2. ESP32 同时上报 `playback_stream_id`，让服务端知道候选段对应哪条下行播放流。
3. 服务端优先使用端侧 payload 标记识别播放中候选段，不再只依赖本地播放状态。
4. 播放中候选段只有在 Omni `semantic_vad` 确认后，并且服务端确实中断了旧播放，才允许继续生成新回复。
5. 如果候选段没有真正中断旧播放，即使后续进入 `segment.finished`，也按播放回声候选丢弃，不走 `segment_turn` 重连兜底。
6. 播放中候选段不会在 `segment.started` 阶段触发前置自动抓拍；只有 Omni 确认真实插话并准备中断播放后，才以 `omni_semantic_barge_in_confirmed` 原因抓拍。

## 验证

1. 单元测试覆盖：端侧显式标记的播放中候选段，即使服务端没有当前播放流，也会被识别为播放候选。
2. 单元测试覆盖：播放候选段不会触发 `realtime_semantic_turn_started` 前置抓拍。
3. 单元测试覆盖：没有真正中断旧播放的候选段会被丢弃，不会发送新的 `assistant.reply` 或下行播放。
4. ESP32 静态测试覆盖：固件源码包含 `started_during_playback` 与 `playback_stream_id` 字段。

## 真机观察点

升级服务端并重新烧录固件后，如果只是眼镜自己的播放声：

1. 端侧仍可能打印 `播放中 VAD 触发候选语音段，等待 Omni semantic_vad 确认`，这是候选段，不代表已经打断。
2. 服务端不应发送新的 `sensor.camera.capture reason=realtime_semantic_turn_started`。
3. 服务端应打印 `Omni semantic_vad 未确认有效播放中插话，按回声候选丢弃`。
4. 不应再次出现由这个候选段触发的新 `actuator.audio.play`。

如果是真实用户插话：

1. 服务端应先打印 `Omni semantic_vad 确认播放中用户插话，准备打断播放`。
2. 随后发送 `actuator.audio.interrupt`。
3. 新一轮候选段可以继续生成回复。

## 风险和后续

1. 这轮修复解决的是状态判定问题，不等于 AEC 声学质量已经完成。端侧仍会把回声作为候选音频上传，只是服务端会更严格地丢弃。
2. 如果 Omni 在强回声下仍把播放声识别为真实语音，并且确认发生在旧播放尚未结束前，仍可能触发打断；后续需要结合播放参考音量、麦克风安装位置和端侧候选 VAD 阈值继续校准。
3. 如果真实插话不够灵敏，可以先看服务端是否收到 `input_audio_buffer.speech_started`，再决定是调 Omni semantic VAD 阈值还是端侧候选 VAD 阈值。
