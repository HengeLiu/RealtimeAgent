# realtime-agent-device Python SDK

`realtime-agent-device` 是 Python 端侧通讯 SDK。它只负责设备端和 `realtime-agent`
server 通讯，不包含业务 Tool / Task、不包含硬件驱动、不依赖 server 内部运行时。

适用场景：

- Python 模拟设备。
- Linux 网关或边缘设备。
- 自动化协议回放和系统测试。
- 开发阶段快速验证设备注册、命令、stream 上传和播放回执。

## 遵循的协议

本 SDK 遵循 `realtime-agent.v1` 端侧协议：

| 通道 | 路径 | 用途 |
| --- | --- | --- |
| Control WebSocket | `/ws/control` | 注册、心跳、命令、stream 生命周期事件。 |
| Audio Input WebSocket | `/ws/stream/audio/input?device_id=<device_id>` | 端侧上传 `sensor.mic` PCM chunk。 |
| Audio Output WebSocket | `/ws/stream/audio/output?device_id=<device_id>` | 端侧接收 server 下发的 `actuator.speaker` chunk。 |
| Visual Input WebSocket | `/ws/stream/visual/input?device_id=<device_id>` | server 请求后，端侧上传一帧 `sensor.rgb` 图片。 |

控制事件使用统一信封：

```json
{
  "version": "realtime-agent.v1",
  "event_id": "evt_xxx",
  "event_name": "command.completed",
  "timestamp_ms": 1760000000000,
  "user_id": "user-001",
  "producer_id": "dev-python-001",
  "session_id": "dev-python-001",
  "stream_id": "stream-001",
  "stream_type": "sensor.rgb",
  "payload": {}
}
```

stream 二进制帧格式：

```text
4 bytes big-endian header length
JSON header bytes
payload bytes
```

SDK 会自动处理 header 长度、`payload_size` 校验和 `seq` 字段，不需要开发者手写二进制帧。

## 数据模型

### `DeviceBuilder`

用于生成设备注册 payload，避免手写 `supports` JSON。

```python
device = (
    DeviceBuilder.define("dev-python-001")
    .user("user-001")
    .name("Python device")
    .role("glass")
    .sensor_rgb(modes=["single", "continuous"], format="jpeg", frequency_hz=1)
    .actuator_vibrator(["vibrate"])
)
```

生成的注册 payload 包含：

- `device_id`
- `name` / `device_name`
- `client_type`
- `sdk_version`
- `runtime`
- `properties`
- `supports.sensors`
- `supports.actuators`

### `RealtimeAgentEvent`

控制事件信封。通常不需要直接构造 JSON 字典，可通过 `client.event()` 或
`client.send_event_name()` 发送。

### `StreamChunk` / `StreamChunkCodec`

用于媒体 WebSocket 二进制消息编解码：

- `StreamChunkCodec.encode(chunk)`
- `StreamChunkCodec.decode(raw)`
- `StreamChunkCodec.encode_header(header, payload)`
- `StreamChunkCodec.decode_header(raw)`

### `RealtimeAgentDeviceClient`

负责：

- 连接 `/ws/control`。
- 发送注册事件。
- 启动心跳。
- 发送和接收控制事件。
- 按 `stream_type` 连接 `/ws/stream/audio/input`、`/ws/stream/audio/output` 或 `/ws/stream/visual/input`。
- 发送和接收 stream chunk。
- 分发 `command.requested` 和 `stream.control.open.requested`。

## 导入到自己的项目

### 仓库内开发

当前 monorepo 已把 SDK 加入根目录 `pyproject.toml` 的 package 查找路径。开发时可以直接：

```bash
uv sync --python 3.11
uv pip install -e .
```

然后在代码中导入：

```python
from realtime_agent_device import RealtimeAgentDeviceClient, DeviceBuilder
```

### 复制到独立项目

如果只想把 Python Device SDK 拿到自己的项目中：

1. 复制 `devices/python` 到你的项目或独立仓库。
2. 在你的项目中安装本地包：

   ```bash
   uv pip install -e ./devices/python
   ```

3. 业务代码导入：

   ```python
   from realtime_agent_device import RealtimeAgentDeviceClient
   ```

后续如果发布到 PyPI，建议包名保持为 `realtime-agent-device`。

## 最小注册示例

```python
import asyncio

from realtime_agent_device import RealtimeAgentDeviceClient, DeviceBuilder


async def main() -> None:
    device = (
        DeviceBuilder.define("dev-python-001")
        .user("user-001")
        .name("Python device")
        .role("glass")
        .sensor_rgb(modes=["single"], format="jpeg")
        .actuator_vibrator(["vibrate"])
    )

    client = RealtimeAgentDeviceClient(
        server_url="http://127.0.0.1:8765",
        device=device,
    )
    await client.connect()
    await client.register()
    print(client.diagnostics_snapshot())
    await client.close()


asyncio.run(main())
```

## 处理命令

```python
async def handle_vibrate(command) -> None:
    await command.accepted()
    await command.completed({"status": "ok"})


client.on_command("haptic.vibrate", handle_vibrate)
```

## 响应 RGB 采集请求

```python
async def handle_rgb(request) -> None:
    jpeg_bytes = Path("frame.jpg").read_bytes()
    await request.opened({"request_id": request.request.payload.get("request_id")})
    await request.write(
        jpeg_bytes,
        codec="jpeg",
        sample_rate=1,
        channels=1,
        duration_ms=0,
        final=True,
    )
    await request.closed()


client.on_stream_open("sensor.rgb", handle_rgb)
```

## 调试和诊断

SDK 提供：

```python
client.diagnostics_snapshot()
```

常见排查顺序：

1. server 是否启动：`curl http://127.0.0.1:8765/api/health`
2. 设备是否注册：`curl http://127.0.0.1:8765/api/debug/devices`
3. 端侧是否发送心跳。
4. `runs/` 中的 `control-events.jsonl`、`stream-events.jsonl`、`command-events.jsonl`。

## 测试

```bash
uv run python -m pytest devices/python/unit-tests devices/python/protocol-tests -q
```

当前测试覆盖：

- 设备声明 builder。
- 控制事件 JSON 往返。
- stream chunk 黄金样例。
- 真实 aiohttp WebSocket 注册和 RGB 上传。
- 不导入 server 内部运行时对象的静态边界。
