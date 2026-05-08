# sdk-v110 ESP32 控制连接重建兜底

## 背景

2026-05-05 真机联调中，`sdk-v109` 已避免应用层反复 stop/start 控制 WebSocket，但服务端重启后仍出现新的恢复卡死：

```text
控制连接未就绪，等待 WebSocket 自动重连
```

同时服务端只能看到眼镜音频旁路上线：

```text
收到未完成控制面注册设备的音频连接 device_id=glass-001
```

这说明 `/ws_audio` 已恢复，但 `/ws/control` 没有完成重连和 `device.register`。

## 根因

`sdk-v109` 的修复假设 ESP-IDF WebSocket client 的自动重连一定能恢复控制连接。真机日志证明该假设不完整：服务端重启后，控制 client 会进入长时间未连接状态，应用层只等待自动重连时无法重新注册设备，导致 SDK 设备组里没有眼镜控制面。

## 本轮改动

1. 控制 WebSocket 断开或错误时记录首次断开时间。
2. `ensure_control_transport_started()` 在 25 秒内继续等待 ESP-IDF 自动重连，避免过早干预内部重连任务。
3. 超过 25 秒仍未连接时，销毁旧 `esp_websocket_client` handle 并重新创建控制连接。
4. 控制连接重新建立后清空断开计时，继续走原有注册和语音会话打开流程。
5. 增加静态测试，覆盖“先等待自动重连、超时后 recreate”的控制连接恢复策略。

## 对业务侧的影响

业务侧无需修改。服务端重启后，眼镜应先尝试自动重连；如果自动重连卡住，端侧会重建控制 WebSocket，从而重新完成 `device.register`。音频上行连接仍按原路径独立恢复。

## 验证

已执行：

```bash
cd openaiglass-sdk/server-python
uv run --with pytest python -m pytest ../tests/unit/test_glass_esp32_runtime.py
```

结果：

- 6 passed

## 真机观察点

服务端重启后重点观察三类日志：

1. 眼镜端先出现 `控制连接未就绪，等待 WebSocket 自动重连`。
2. 如果 25 秒内未恢复，应出现 `控制连接自动重连超时，重新创建 WebSocket client`。
3. 服务端随后应收到眼镜 `device.register`，不应只剩 `收到未完成控制面注册设备的音频连接`。
