# Changelog

## 0.1.0rc1 - 2026-05-07

`audio-chat` 的第一个老 SDK 可用性对齐发布候选版本。

### 已纳入 release candidate gate

- `audio-chat.sdk.package-check` 会检查 wheel build、wheel 安装导入、editable install、CLI entry points、公开 API、package data、端侧参考源码、包边界和本地私密产物排除。
- `scripts/acceptance_check.py old-sdk-parity-release` 作为发布候选 lane，覆盖 package-check、docs contract、basic app playback 和 for-blind app playback。
- Python wheel 只交付 server-side SDK 包，不包含 examples、runs、端侧真机工程、本地配置或缓存产物。

### 当前不兼容点

- 不复用旧 `openaiglass-sdk` 的 `DeviceGroupContext` 名称；新 SDK 使用 `UserDeviceContext`。
- 不保留 glass / phone 固定设备类型建模；端侧通过 capabilities 和 subscriptions 声明能力。
- 不保留旧 `/ws_audio`、`/ws_realtime_audio` 和 `sensor.camera.*` 协议名称；新 SDK 统一使用 event + stream。
- iOS、ESP32、web-glass 和 Python phone mock 当前作为参考端源码随仓库交付，不进入 Python wheel。

