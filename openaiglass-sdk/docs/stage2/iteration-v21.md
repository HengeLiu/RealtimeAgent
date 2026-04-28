# iteration-v21：SDK v22 日志观测增强

## 本轮目标

根据业务功能团队在 2026-04-28 的联调反馈，补齐回放眼镜和服务端语音链路的关键时间点日志，减少排查时对业务侧临时打印的依赖。

本轮对应对外 SDK 版本：`sdk-v22`。

## 主要改动

1. `glass-playback` 启动后会打印命令行状态，避免命令运行期间没有任何可见进展。
2. 回放眼镜命令行只打印收到的控制消息，不打印自身发送的控制消息。
3. 服务端 `VoiceRuntime` 在收到首个模型文本增量时打印 `大模型返回首个 token`，并记录 `first_token_latency_ms`。
4. 回放眼镜保存播放音频时按流式读取 `/stream.wav`，收到第一段下行音频后立即打印 `elapsed_ms` 和首段字节数。
5. 单元测试覆盖全双工握手日志边界和播放音频首段到达日志。

## 当前边界

1. `first_token_latency_ms` 从语音链路开始调用 AgentFacade 前计时，主要用于联调排查，不作为业务 SLA 口径。
2. 回放眼镜首段音频日志基于 `/stream.wav` HTTP 响应首个非空字节块；真实 ESP32 端侧还需要在固件或端侧 SDK 中补同类日志。
3. 设备侧“只打印收到消息”约束当前先落在 `glass-playback` 命令行输出；服务端调试日志仍会按服务端运行时策略记录必要的收发细节。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_playback_config.py -q

PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py -q

PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py -q
```
