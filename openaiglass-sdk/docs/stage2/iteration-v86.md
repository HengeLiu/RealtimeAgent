# sdk-v86 受限连续对话和唤醒词打断修复

## 背景

2026-05-03 真机联调时，`sdk-v85` 已经阻断“空语音 + 自动抓拍 + 模型看图回复”的自循环，但也把真实 ESP32 的免唤醒连续对话完全关闭。业务体验上仍有三个问题：

1. 用户说“结束对话”“安静”等控制指令时，模型仍可能把它当成普通语音继续回复。
2. 连续窗口内背景音误触发时，仍可能产生空语音段和自动抓拍。
3. 播放期间用户无法主动打断当前回复。

## 变更

1. 真实 ESP32 恢复受限连续对话窗口：
   - `voice.realtime.session.open` 请求 `realtime_semantic_vad` 时，端侧回报 `continuous_dialog=true`。
   - 播放结束后冷却时间从 900ms 增加到 1500ms。
   - 连续 VAD 触发门槛从 4 帧增加到 10 帧。
   - 播放期间不允许普通 VAD 启动新语音段，避免扬声器回灌触发自循环。
2. 服务端拦截停止连续对话指令：
   - 旁路 ASR 转写命中“结束对话”“停止对话”“安静”“别说了”等短句时，不再进入 Omni/Agent 回复。
   - 服务端关闭本轮 Omni 预连接，丢弃本轮自动抓拍，并下发 `voice.dialog.close`。
   - 眼镜收到后关闭连续对话窗口，恢复 WakeNet 待命。
3. 播放中支持唤醒词打断：
   - 端侧播放期间继续运行 WakeNet，但不启动普通语音段。
   - 播放中命中 WakeNet 时，本地请求停止播放，并上报 `user.voice.interrupt`。
   - 服务端复用统一播放仲裁器，停止当前播报并清理待播队列。
   - 能力声明新增 `barge_in=true`、`output_cancel=true`、`barge_in_mode=wake_word`。

## 边界

当前 ESP32-S3 端侧仍没有可用 AEC，因此本轮不开放无唤醒词自然插话。播放期间只有“嗨乐鑫”级别的 WakeNet 打断是默认能力；普通说话仍可能被扬声器回灌污染，不能直接作为打断判据。

## 真机验证

已执行真实 ESP32 烧录和自动声场测试：

```bash
uv run openaiglass.glass.start \
  --repo-root . \
  --app-root openaiglass-for-blind \
  --sdk-root openaiglass-sdk \
  --config openaiglass-for-blind/host/glass/config/local_build.env \
  --build-dir /tmp/openaiglass-realtime-fix-build \
  --sdkconfig /tmp/openaiglass-realtime-fix-sdkconfig \
  --port /dev/cu.usbmodem2101 \
  --flash-only

LOG_LEVEL=DEBUG uv run openaiglass.server.start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env \
  --log-file openaiglass-for-blind/logs/realtime-device-test-server-auto-speech.log
```

观察到：

```text
semantic_continuous_requested=1 semantic_continuous_enabled=1
capabilities.barge_in=true
capabilities.output_cancel=true
capabilities.barge_in_mode=wake_word
```

用电脑扬声器播放“嗨乐鑫，结束对话”后，服务端日志出现：

```text
旁路 ASR 转写完成 ... text='结束对话。'
已发送控制消息: voice.dialog.close
已按用户指令关闭连续对话 ...
```

眼镜串口日志出现：

```text
WakeNet detected: segment_id=...
连续对话窗口已关闭: reason=conversation_stop_command
收到 voice.dialog.close，已关闭连续对话窗口并恢复 WakeNet 待命
```

该轮没有继续下发 `assistant.reply`、`actuator.audio.play` 或 Agent 最终回复。

## 自动化验证

已执行：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_glass_esp32_runtime.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py::VoiceRuntimeTestCase::test_empty_continuous_vad_segment_is_suppressed_before_model \
  openaiglass-sdk/tests/unit/test_voice_runtime.py::VoiceRuntimeTestCase::test_stop_command_closes_continuous_dialog_before_model \
  -q

uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/integration/test_control_register_flow.py \
  -q
```

结果均通过。第二组测试仅保留一个既有 PytestCollectionWarning，不影响本轮验证。
