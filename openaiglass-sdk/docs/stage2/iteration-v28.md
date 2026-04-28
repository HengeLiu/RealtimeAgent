# iteration-v28：SDK v29 真实眼镜实时语音打开兼容

## 本轮目标

修复真实 ESP32 眼镜在服务端默认 `VOICE_SESSION_MODE=full_duplex_realtime` 下注册成功但 WakeNet 没有效果的问题。

本轮对应对外 SDK 版本：`sdk-v29`。

## 问题原因

当前服务端默认会在眼镜注册后下发 `voice.realtime.session.open`。此前真实 ESP32 眼镜固件只处理旧的 `voice.session.open`，因此不会：

1. 保存服务端下发的 `session_id`。
2. 设置 `s_voice_session_opened=true`。
3. 建立 `/ws_audio` 上行连接。
4. 打开 WakeNet 门控。

结果表现为控制连接已经注册，但用户说唤醒词后没有任何语音段上传。

## 主要改动

1. ESP32 眼镜运行时新增 `voice.realtime.session.open` 控制消息处理。
2. 真实眼镜收到实时打开请求后，回复 `voice.realtime.session.opened`。
3. 回复 payload 声明 `accepted_mode=half_duplex`，并声明 `capabilities.aec=false`、`vad=true`、`barge_in=false`、`output_cancel=false`。
4. 服务端根据 `aec=false` 把实时会话降级为半双工；眼镜端复用现有 WakeNet 和 `/ws_audio` 链路。
5. 新增静态测试，防止真实眼镜运行时再次遗漏实时打开兼容分支。

## 当前边界

1. 本轮不是实现 ESP32 真全双工 AEC/VAD 实时音频，只是让默认全双工服务端配置下的真实眼镜可降级工作。
2. 若需要验收真正全双工插话，应使用支持端侧 AEC 的新固件或手机音频中继。
3. 修改服务端公网地址后仍必须重新同步 `host/glass/config/local_build.env` 并重新构建烧录固件。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_glass_esp32_runtime.py \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py -q
```
