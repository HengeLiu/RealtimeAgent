# realtime-agent C Device SDK

`realtime-agent C Device SDK` 面向 ESP32、嵌入式 Linux 和其他 C/C++ 端侧工程。
它提供最小协议核心：设备注册 payload 构造和 stream chunk 二进制编解码。

SDK 不绑定 Wi-Fi、WebSocket、JSON 库或 RTOS。真实设备项目可以把这些能力接到
ESP-IDF、libwebsockets、Mongoose、POSIX socket 或自己的网络层。

## 遵循的协议

协议版本：`realtime-agent.v1`

| 通道 | 路径 | 用途 |
| --- | --- | --- |
| Control WebSocket | `/ws/control` | 注册、心跳、命令、stream 生命周期事件。 |
| Stream WebSocket | `/ws/stream?device_id=<device_id>` | 音频、图片、传感器和播放数据。 |

控制事件仍然是 JSON。C SDK 当前只帮助构造注册 payload，不直接实现完整 JSON 事件
序列化。推荐设备端用项目已有 JSON 库组合事件信封：

```json
{
  "version": "realtime-agent.v1",
  "event_id": "evt_xxx",
  "event_name": "control.device.register.requested",
  "timestamp_ms": 1760000000000,
  "user_id": "user-001",
  "producer_id": "dev-esp32-001",
  "payload": {}
}
```

stream chunk 二进制格式：

```text
4 bytes big-endian header length
JSON header bytes
payload bytes
```

`realtime_agent_stream_decode()` 会校验 header 中的 `payload_size`。

## 数据模型

### `realtime_agent_device_t`

设备注册声明：

```c
realtime_agent_device_t device;
realtime_agent_device_init(&device, "user-001", "dev-esp32-001");
realtime_agent_device_set_name(&device, "ESP32 Glass");
realtime_agent_device_set_role(&device, "glass");
realtime_agent_device_add_rgb_sensor(&device);
realtime_agent_device_add_vibrator(&device);
```

生成注册 payload：

```c
char payload[1024];
int n = realtime_agent_device_registration_json(&device, payload, sizeof(payload));
if (n > 0) {
    // payload 可放入 control.device.register.requested 的 payload 字段
}
```

### stream codec

编码：

```c
const char *header = "{\"stream_id\":\"s1\",\"payload_size\":3}";
const uint8_t payload[] = {'a', 'b', 'c'};
uint8_t raw[128];
size_t written = 0;

realtime_agent_stream_encode(
    header,
    payload,
    sizeof(payload),
    raw,
    sizeof(raw),
    &written
);
```

解码：

```c
char header_json[128];
const uint8_t *decoded_payload = NULL;
size_t decoded_payload_size = 0;

int ok = realtime_agent_stream_decode(
    raw,
    written,
    header_json,
    sizeof(header_json),
    &decoded_payload,
    &decoded_payload_size
);
```

## 导入到自己的项目

### CMake 项目

把目录复制到你的项目，例如：

```text
third_party/realtime-agent-device-c/
```

在 `CMakeLists.txt` 中加入：

```cmake
add_subdirectory(third_party/realtime-agent-device-c)

target_link_libraries(your_target PRIVATE realtime_agent_device)
```

代码中导入：

```c
#include "realtime_agent_device/realtime_agent_device.h"
#include "realtime_agent_device/realtime_agent_stream.h"
```

### ESP-IDF 项目

当前 ESP32-S3 参考固件已直接引用本 SDK：

```cmake
idf_component_register(
    SRCS
        "realtime_agent_reference_main.c"
        "../../../../../../audio-device/c/src/realtime_agent_device.c"
        "../../../../../../audio-device/c/src/realtime_agent_stream.c"
    INCLUDE_DIRS "." "../../../../../../audio-device/c/include"
)
```

正式项目建议把 C SDK 放入 ESP-IDF component：

```text
components/realtime_agent_device/
  include/realtime_agent_device/
  realtime_agent_device.c
  realtime_agent_stream.c
  CMakeLists.txt
```

component `CMakeLists.txt` 示例：

```cmake
idf_component_register(
    SRCS "realtime_agent_device.c" "realtime_agent_stream.c"
    INCLUDE_DIRS "include"
)
```

## 最小运行流程

端侧应用需要自己实现：

1. 连接 Wi-Fi 或有线网络。
2. 建立 `/ws/control`。
3. 用 C SDK 生成注册 payload。
4. 构造 `control.device.register.requested` JSON 并发送。
5. 收到 `control.device.registered` 后定期发送心跳。
6. 收到 `stream.control.open.requested` 后打开硬件。
7. 用 `realtime_agent_stream_encode()` 上传二进制帧到 `/ws/stream`。
8. 收到关闭或断线后释放硬件资源。

## 测试

```bash
cmake -S audio-device/c -B audio-device/c/build
cmake --build audio-device/c/build
ctest --test-dir audio-device/c/build --output-on-failure
```

测试覆盖：

- stream chunk 编解码。
- `payload_size` 错误检测。
- 设备注册 payload 字符串生成。

## 当前限制

- 不包含 WebSocket 实现。
- 不包含 JSON parser，只生成少量注册 JSON 和校验 header 中的 `payload_size`。
- 不包含 ESP-IDF component 发布配置。
- 没有做真实 ESP32 硬件验证。
