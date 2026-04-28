# iteration-v18：语音会话模式启动配置

## 本轮目标

把全双工或半双工语音会话选择从代码行为改为服务端启动配置。默认使用 `full_duplex_realtime`，旧眼镜固件、半双工回放或只验证 `/ws_audio` 的场景可以显式配置为 `half_duplex`。

本轮对应对外 SDK 版本：`sdk-v19`。

## 主要改动

1. `ServerSettings` 新增 `voice_session_mode`，可从环境变量 `VOICE_SESSION_MODE` 读取。
2. 配置值只允许 `full_duplex_realtime` 和 `half_duplex`，非法值会触发结构化配置错误。
3. 眼镜注册后，控制面按配置下发 `voice.realtime.session.open` 或旧的 `voice.session.open`。
4. `openaiglass.sdk.server` 启动默认环境新增 `VOICE_SESSION_MODE=full_duplex_realtime`。
5. 运行态快照新增顶层字段 `configured_voice_session_mode`，便于真机联调时确认当前服务端模式。
6. 更新业务侧配置样例和 SDK 安装与能力开发指南，说明默认全双工和半双工回退方式。

## 当前边界

1. 该配置只决定注册后服务端默认打开哪条语音链路，不替代端侧 AEC/VAD 能力协商。
2. `half_duplex` 仍走原 `/ws_audio` 链路，适合旧固件和当前半双工回放工具。
3. `full_duplex_realtime` 仍需要端侧或手机中继连接 `/ws_realtime_audio` 并上报 `voice.realtime.*` 事件。

## 验证结果

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_settings.py openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py -q
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/integration/test_control_register_flow.py -q
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/integration/test_voice_dialog_flow.py -q
python -m compileall -q openaiglass-sdk/server-python
```

## 真机验收建议

1. 默认启动服务端，眼镜注册后确认收到 `voice.realtime.session.open`。
2. 在 `config/local_server.env` 中设置 `VOICE_SESSION_MODE=half_duplex` 后重启服务端，确认眼镜注册后收到 `voice.session.open`。
3. 打开 `/api/runtime/devices` 或运行态快照，确认 `configured_voice_session_mode` 与实际配置一致。
