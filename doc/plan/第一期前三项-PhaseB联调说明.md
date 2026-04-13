# 第一期前三项 Phase B 联调说明

## 1. 目标

本说明用于验证 Phase B 的“真实注册链路”是否打通，覆盖两类联调方式：

1. 本地模拟设备联调。
2. ESP32 真机联调。

验证通过的标准是：

1. 设备能连接 `/ws/control`。
2. 设备能发送 `device.register`。
3. 服务端能返回 `device.registered`。
4. 服务端会自动返回 `voice.session.open`。
5. 设备能回 `voice.session.opened`。
6. 设备能持续发送 `device.heartbeat`。

## 2. 服务端启动

在仓库根目录执行：

```bash
export DEVICE_TOKEN_MAP="glass-001=pair-demo-token"
export HEARTBEAT_INTERVAL_MS=5000
export HEARTBEAT_TIMEOUT_MS=15000
PYTHONPATH=server/src python3 -m app.main --host 0.0.0.0 --port 8765
```

更推荐直接使用脚本：

```bash
bash script/deprecated/run_server_phase_b.sh all
```

如果联调阶段服务端部署在远程服务器 `ali5`，可直接使用远程发布脚本：

```bash
bash script/deprecated/run_server_phase_b_remote.sh all
```

说明：

1. `DEVICE_TOKEN_MAP` 必须包含当前设备的 `device_id=pair_token`。
2. Phase B 联调时建议先只配一台设备，避免日志干扰。
3. 脚本支持 `start/logs/stop/all` 四种动作。
4. 远程脚本支持 `sync/start/stop/logs/all`，默认把当前仓库同步到 `ali5:/home/liuh/dev/<当前仓库名>`。

## 3. 本地模拟设备联调

### 3.1 启动命令

服务端启动后，另开一个终端执行：

```bash
python3 script/phase_b_control_client.py \
  --host 127.0.0.1 \
  --port 8765 \
  --device-id glass-001 \
  --pair-token pair-demo-token \
  --duration 12
```

### 3.2 预期日志

客户端侧应看到：

1. `[client] 已发送 device.register`
2. `[client] 收到: name=device.registered`
3. `[client] 收到: name=voice.session.open`
4. `[client] 已自动回发 voice.session.opened`
5. `[client] 已发送 device.heartbeat`

服务端侧应看到：

1. `收到控制消息: device.register`
2. `已发送控制消息: device.registered`
3. `已发送控制消息: voice.session.open`
4. `收到控制消息: voice.session.opened`
5. `收到控制消息: device.heartbeat`

### 3.3 运行态观察

执行：

```bash
curl http://127.0.0.1:8765/api/runtime/devices
```

预期返回：

1. `online_device_count=1`
2. `online_devices` 包含 `glass-001`
3. `connections[0].voice_opened=true`

## 4. ESP32 真机联调

### 4.1 配置方式

眼镜端配置必须走本地私有配置文件，不使用交互式 `menuconfig` 作为日常流程，也不要直接手改 `sdkconfig`。

推荐方式：

```bash
cp glass/config/local_build.env.example glass/config/local_build.env
```

然后编辑本地私有配置文件：

```bash
vim glass/config/local_build.env
```

需要填写：

1. `GLASS_WIFI_PRIMARY_SSID`
2. `GLASS_WIFI_PRIMARY_PASSWORD`
3. `GLASS_WIFI_FALLBACK_SSID`
4. `GLASS_WIFI_FALLBACK_PASSWORD`
5. `GLASS_SERVER_WS_URI`
6. `GLASS_DEVICE_ID`
7. `GLASS_PAIR_TOKEN`
8. `GLASS_HEARTBEAT_INTERVAL_MS`

关键建议：

1. `GLASS_SERVER_WS_URI` 示例：`ws://192.168.1.10:8765/ws/control`
2. `GLASS_DEVICE_ID` 必须与服务端 `DEVICE_TOKEN_MAP` 中的键一致。
3. `GLASS_PAIR_TOKEN` 必须与服务端 `DEVICE_TOKEN_MAP` 中的值一致。
4. 脚本会在每次编译前把这些值同步到本地 `glass/src/sdkconfig.local`，这个文件不会提交到 Git。
5. 主流程只保留 `glass/config/local_build.env -> run_glass_esp32.sh` 这一套配置方式，避免出现两份配置来源。

### 4.2 编译与烧录

```bash
cd glass/src
idf.py set-target esp32s3
idf.py build
idf.py -p <串口端口> flash monitor
```

更推荐直接使用脚本：

```bash
bash script/run_glass_esp32.sh
```

常用变体：

```bash
bash script/run_glass_esp32.sh --build-only
```

```bash
bash script/run_glass_esp32.sh --monitor-only -p /dev/cu.usbmodem2101
```

### 4.3 串口日志观察点

串口应看到：

1. `config: device_id=... server_ws_uri=...`
2. `开始连接 WiFi: ...`
3. `WiFi 已获取 IP，准备建立控制连接`
4. `控制连接已建立`
5. `已发送控制消息: device.register`
6. `注册成功: device_id=... heartbeat_interval_ms=...`
7. `收到 voice.session.open: session_id=...`
8. `已发送控制消息: voice.session.opened`
9. 后续周期性出现 `已发送控制消息: device.heartbeat`

如果当前固件已接入 WakeNet，上述日志之外还应看到：

1. `WakeNet model selected: ...`
2. `WakeNet runtime ready; waiting for voice.session.open`
3. `WakeNet listening enabled for session_id=...`
4. 说出唤醒词后出现 `WakeNet detected: segment_id=...`
5. 紧接着出现 `已发送控制消息: sensor.audio.segment.started`

服务端应同时看到：

1. `收到控制消息: device.register`
2. `收到控制消息: voice.session.opened`
3. `收到控制消息: device.heartbeat`
4. 说出唤醒词后出现 `收到控制消息: sensor.audio.segment.started`
5. 随后出现 `收到语音唤醒状态上报: segment_id=...`

## 5. 联调验收步骤

### 5.1 注册成功验收

1. 启动服务端。
2. 启动本地模拟客户端或 ESP32。
3. 确认服务端返回 `device.registered`。
4. 确认服务端自动下发 `voice.session.open`。
5. 确认设备回发 `voice.session.opened`。
6. 确认运行态接口显示在线设备数为 1。

### 5.2 注册失败验收

1. 把客户端或 ESP32 的 `pair_token` 改错。
2. 再次建立控制连接。
3. 确认服务端返回 `device.register.failed`。
4. 确认设备不会进入已注册状态。

### 5.3 重连覆盖验收

1. 先运行一台 `device_id=glass-001` 的客户端。
2. 再启动第二个同设备编号客户端。
3. 确认旧连接被关闭。
4. 确认运行态接口中仍只有一条 `glass-001` 在线记录。

### 5.4 心跳超时验收

1. 正常完成注册。
2. 停掉客户端或断开 ESP32 网络。
3. 等待超过 `HEARTBEAT_TIMEOUT_MS`。
4. 再查 `/api/runtime/devices`。
5. 确认 `online_device_count` 回到 0。

## 6. 自动化测试命令

```bash
bash script/run_phase_b_tests.sh
```

覆盖范围：

1. 注册成功。
2. 注册失败。
3. 重连覆盖。
4. 心跳超时。
5. `sensor.audio.segment.started` 接收回归。
6. 配置与协议公共层回归。
