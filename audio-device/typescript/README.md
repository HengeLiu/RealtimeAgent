# @realtime-agent/device

`@realtime-agent/device` 是 JavaScript / TypeScript 端侧通讯 SDK。它面向浏览器、
Node、Electron、WebView 等运行时，负责和 `realtime-agent` server 建立控制通道和
stream 通道。

当前第一版重点覆盖协议模型和 stream chunk 编解码；浏览器参考端已经通过
`examples/dev-support/devices/browser-glass/sdk/realtime-agent-device-browser.js`
复用该协议形态。

## 遵循的协议

协议版本：`realtime-agent.v1`

| 通道 | 路径 | 用途 |
| --- | --- | --- |
| Control WebSocket | `/ws/control` | 注册、心跳、命令、stream 生命周期事件。 |
| Stream WebSocket | `/ws/stream?device_id=<device_id>` | 二进制数据上传和下行播放数据。 |

控制事件字段：

```ts
{
  version: "realtime-agent.v1",
  event_id: "evt_xxx",
  event_name: "stream.input.opened",
  timestamp_ms: 1760000000000,
  user_id: "user-001",
  producer_id: "dev-browser-001",
  session_id: "dev-browser-001",
  stream_id: "stream-001",
  stream_type: "sensor.rgb",
  payload: {}
}
```

stream 二进制帧：

```text
4 bytes big-endian header length
JSON header bytes
payload bytes
```

`StreamChunkCodec` 会写入 `payload_size` 并在解码时校验。

## 数据模型

### `DeviceBuilder`

生成设备注册 payload：

```js
const device = DeviceBuilder.define("dev-browser-001")
  .user("user-001")
  .name("Browser glass")
  .role("glass")
  .runtime({ platform: "browser", language: "javascript" })
  .sensorRgb({ modes: ["single", "continuous"], format: "jpeg", frequencyHz: 1 })
  .actuatorVibrator(["vibrate"]);
```

### `RealtimeAgentEvent`

控制事件信封：

```js
const event = new RealtimeAgentEvent({
  eventName: "command.completed",
  userId: "user-001",
  producerId: "dev-browser-001",
  payload: { command_id: "cmd-001" },
});
ws.send(event.toJson());
```

### `StreamChunk` / `StreamChunkCodec`

用于 stream 二进制帧：

```js
const raw = StreamChunkCodec.encodeHeader(
  {
    version: "realtime-agent.v1",
    user_id: "user-001",
    session_id: "dev-browser-001",
    stream_id: "stream-rgb-001",
    stream_type: "sensor.rgb",
    seq: 0,
    timestamp_ms: Date.now(),
    codec: "jpeg",
    sample_rate: 1,
    channels: 1,
    duration_ms: 0,
    final: true,
    metadata: { request_id: "req-001" },
  },
  jpegBytes,
);
```

### `RealtimeAgentDeviceClient`

提供控制 WebSocket 和 stream WebSocket 的基础封装：

- `connectControl()`
- `sendEvent(event)`
- `ensureStream()`
- `sendStreamChunk(chunk)`

## 导入到自己的项目

### 当前仓库内使用

```js
import {
  RealtimeAgentDeviceClient,
  DeviceBuilder,
  StreamChunkCodec,
} from "./audio-device/typescript/src/index.js";
```

### 浏览器页面使用

如果没有打包器，可以把 `audio-device/typescript/src/*.js` 作为 ESM 模块引入。
浏览器参考端为了避免跨目录静态文件访问，使用了本地 adapter：

```js
import { StreamChunkCodec } from "./sdk/realtime-agent-device-browser.js";
```

真实项目中建议把 SDK 复制到前端源码目录，或通过 npm 包引入。

### Node / npm 项目使用

当前包还没有发布到 npm。接入方式：

```bash
cp -R audio-device/typescript your-project/vendor/realtime-agent-device
```

在 `package.json` 中使用本地依赖：

```json
{
  "dependencies": {
    "@realtime-agent/device": "file:./vendor/realtime-agent-device"
  }
}
```

安装：

```bash
npm install
```

导入：

```js
import { DeviceBuilder, StreamChunkCodec } from "@realtime-agent/device";
```

后续发布到 npm 时建议包名保持为 `@realtime-agent/device`。

## 最小注册示例

```js
import { RealtimeAgentDeviceClient, DeviceBuilder } from "@realtime-agent/device";

const device = DeviceBuilder.define("dev-browser-001")
  .user("user-001")
  .name("Browser glass")
  .role("glass")
  .sensorRgb({ modes: ["single"], format: "jpeg" });

const client = new RealtimeAgentDeviceClient({
  serverUrl: "http://127.0.0.1:8765",
  device,
  WebSocketImpl: WebSocket,
});

const ws = client.connectControl();
ws.addEventListener("open", () => {
  client.sendEvent(client.event("control.device.register.requested", device.registrationPayload()));
});
```

## 测试

```bash
cd audio-device/typescript
npm test
```

测试覆盖：

- 设备注册 payload。
- 控制事件信封。
- WebSocket URL 派生。
- stream chunk 黄金样例。
- stream chunk 对象往返。

## 当前限制

- 第一版没有内置完整重连策略。
- 浏览器权限、摄像头、麦克风和播放驱动由业务项目自己实现。
- npm 发布尚未执行。
