# SDK 迭代记录：Omni 最终回复播放流延迟注册

对应对外 SDK 版本：`sdk-v78`。

## 背景

工具调用前会播报一段等待提示。问题出现在 Omni 原生音频回复链路：服务端在语音段开始时就提前创建了最终回复播放流，虽然那时还没有任何模型音频，但播放仲裁器已经把这条最终回复流登记为当前播放流。

当模型随后触发工具调用时，工具前置播报只能排队等待；等 Omni 返回最终音频后，最终回复反而先播放，前置播报延后播放。这和“工具调用前提示用户等待”的产品预期相反。

## 根因

播放仲裁器只看播放流登记顺序，不关心该流是否已经有音频数据。

旧逻辑：

1. `sensor.audio.segment.started` 时启动 Omni Realtime 上行字节流。
2. 同时提前创建 `omni_realtime` 最终回复播放上下文。
3. 工具调用发生时创建 `agent_progress` 前置播报。
4. 播放仲裁器认为 `omni_realtime` 已经占用当前播放位，导致 `agent_progress` 被排队。

## 本轮变更

1. Omni Realtime 上行会话仍然在语音段开始时建立，继续保持端到端字节流输入。
2. 最终回复播放上下文不再在语音段开始时创建。
3. 只有当 Omni 返回首段 `response.audio.delta` 时，才懒创建 `omni_realtime` 播放上下文。
4. 如果 Omni 没有返回音频但链路需要收尾，才在最终阶段创建兜底播放上下文。
5. 保留工具前置播报的同步注册逻辑，确保工具调用发生时播放仲裁器能立即看到 `agent_progress` 流。

## 验证

1. 单元测试：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q
```

结果：`72 passed`。

新增或调整的关键用例：

- `test_omni_mode_prestreams_realtime_audio_direct_session`
  - 验证语音段开始时只建立 Omni 输入会话，不提前创建最终回复播放上下文。
- `test_omni_final_audio_does_not_jump_ahead_of_progress_reply`
  - 验证工具前置播报已占位时，Omni 最终音频不会抢先播放。

2. 设备级回放：

```bash
LOG_LEVEL=DEBUG uv run openaiglass.server.start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env \
  --log-file openaiglass-for-blind/logs/server-progress-order-check.log

uv run openaiglass.phone.mock \
  --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json

uv run openaiglass.glass.start \
  --runtime playback \
  --config openaiglass-for-blind/host/glass-playback/config/look_look.json
```

结果：`glass-playback` 返回 `assertions_ok=true`，`actuator_count=2`。

关键日志：

```text
2026-05-01T14:07:01.981635 server.voice Omni Realtime 工具调用请求 tool_name=manage_memory
2026-05-01T14:07:01.982420 server.voice-reply_8ba8f1e97708 工具前置播报命中静态音频缓存
2026-05-01T14:07:01.984038 server.voice-reply_8ba8f1e97708 下行播放请求已发送 audio_source=tts
2026-05-01T14:07:02.660490 server.voice Omni Realtime 返回首段音频
2026-05-01T14:07:02.664485 server.voice-reply_0073e5d2f8b1 下行音频源返回首段音频 audio_source=omni_realtime
2026-05-01T14:07:04.367587 server.voice-reply_0073e5d2f8b1 下行播放请求已发送 audio_source=omni_realtime
```

眼镜端顺序：

```text
2026-05-01T14:07:01.982815 glass-playback actuator.audio.play stream_id=reply_8ba8f1e97708
2026-05-01T14:07:04.365781 glass-playback 本机播放器播放结束 stream_id=reply_8ba8f1e97708
2026-05-01T14:07:04.367688 glass-playback actuator.audio.play stream_id=reply_0073e5d2f8b1
```

这确认前置播报先播放，最终回复在前置播报结束后播放。

验证结束后已停止本地 server 和 phone mock。
