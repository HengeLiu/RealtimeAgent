# iteration-v19：SDK v20 阻塞点收口

## 本轮目标

根据业务功能团队反馈，收口 SDK 阻塞业务能力继续迭代的四个公共能力缺口。

本轮对应对外 SDK 版本：`sdk-v20`。

## 主要改动

1. 真实 `ControlRuntime` 初始化时自动为 `DeviceGroupRuntime` 绑定视频链路启动和停止适配器。
2. `DeviceGroupContext.start_phone_video_link(...)` 返回标准 `phone_video_link_task` 快照字段，业务可继续通过 `query_task(link["task_id"])` 查询阶段、流编号、目标地址和错误信息。
3. `openaiglasses` 公开入口导出 `BaseMcpAdapter` 和 `McpMethodSpec`，业务 MCP Adapter 不再需要直接导入 `agent_core` 包路径。
4. `phone-mock` 配置支持 `task_class` 与 `processor_plugins`，可加载 Python `BasePhoneTask` / `BasePhoneProcessor` 作为 mock 插件。
5. `glass-playback` 支持响应 `voice.realtime.session.open`，保存并复用服务端下发的 `session_id`。
6. SDK 预检新增真实服务端句柄视频链路 adapter 绑定检查。

## 当前边界

1. `/api/debug/phone-video-link/start|stop` 仍保留为调试兼容入口；业务能力不要依赖它。
2. Python phone 插件加载只服务于 `phone-mock` 和本地契约测试；真实 iPhone 插件仍通过 Swift 侧 `PhoneTaskCapabilityRegistry.register(taskType:)` 接入。
3. `glass-playback` 已补齐全双工打开握手，但触发音频仍复用 `/ws_audio` 上传；真正实时媒体帧验收仍需 `/ws_realtime_audio`。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/phone-mock:openaiglass-sdk/glass-playback:openaiglass-for-blind uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_sdk_phase_two.py \
  openaiglass-sdk/tests/unit/test_phone_mock_config.py \
  openaiglass-sdk/tests/unit/test_playback_config.py -q

PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/phone-mock:openaiglass-sdk/glass-playback:openaiglass-for-blind uv run python -m compileall -q \
  openaiglass-sdk/server-python openaiglass-sdk/phone-mock openaiglass-sdk/glass-playback
```
