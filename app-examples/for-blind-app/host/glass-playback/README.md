# host/glass-playback 样板

glass playback 用于不依赖真实 ESP32 的设备级回放。可复制：

- `app-examples/basic-app/host/glass-playback/sdk-playback.yaml`
- `app-examples/basic-app/host/glass-playback/playback.yaml`

业务能力新增输入资产时，优先放在 `testdata` 下，并让 playback 记录 `events.jsonl`、`stream-events.jsonl`、`assets.jsonl`、`result.json`。
