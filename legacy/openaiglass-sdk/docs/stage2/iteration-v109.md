# sdk-v109 ESP32 控制连接自动重连修复

## 背景

2026-05-05 真机联调中，服务端重启后眼镜音频上行 WebSocket 可以恢复，但控制连接反复失败：

```text
控制连接未就绪，尝试重新建立连接
websocket_client: Client was not started
websocket_client: Error create websocket task
重新启动控制连接失败
```

## 根因

ESP32 控制 WebSocket 已配置：

```c
.reconnect_timeout_ms = 10000
```

也就是说 ESP-IDF `esp_websocket_client` 会在断开后自行等待并重连。眼镜端 heartbeat 任务每 5 秒检查一次控制连接，发现断开后又主动对同一个 client 执行：

```c
esp_websocket_client_stop(s_ws_client);
esp_websocket_client_start(s_ws_client);
```

服务端重启时，IDF 内部自动重连定时器和应用层手动 stop/start 竞争，导致 client 进入未启动但又无法创建新 websocket task 的不一致状态。

## 本轮改动

1. 控制连接改为只使用 ESP-IDF WebSocket client 自动重连。
2. `ensure_control_transport_started()` 在 client 已创建但未连接时，只记录“等待 WebSocket 自动重连”，不再 stop/start 同一个 handle。
3. 保留首次启动控制连接的路径；音频上行连接暂不改动，因为它没有配置控制连接同样的自动重连策略。
4. 增加静态测试，防止控制连接重连路径重新引入 `esp_websocket_client_stop(s_ws_client)`。

## 对业务侧的影响

业务侧无需修改。服务端重启后，眼镜控制连接应由 IDF 自动重连恢复；恢复后会重新注册设备并重新打开语音会话。

## 验证

已执行：

```bash
cd openaiglass-sdk/server-python
uv run --with pytest python -m pytest ../tests/unit/test_glass_esp32_runtime.py
```

结果：

- 6 passed
