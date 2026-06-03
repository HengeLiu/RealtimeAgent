# ESP32-S3 眼镜固件调试记录

## 问题概述

ESP32-S3 眼镜设备无法与 audio-chat 服务器正常通信，设备注册成功后心跳超时，无音频数据到达服务器。

## 排查过程

### 1. 固件编译问题

**问题**: 代码编译失败，存在语法错误和配置问题

**解决**:
- 修复 `audio_chat_stream_encode()` 缺少 `sample_rate`、`channels`、`duration_ms` 字段
- 修复 `audio_chat_stream_encode_imu()` 同样缺少必要字段
- 完整烧录需要包含 bootloader、partition table 和 app firmware

### 2. 服务器地址配置

**问题**: 固件配置的服务器地址 `47.100.161.139:8081` 与实际运行地址 `192.168.31.8:8765` 不一致

**解决**:
```c
static const char *SERVER_HOST = "192.168.31.8";
static const uint16_t SERVER_PORT = 8765;
```

### 3. WiFi 名称密码

**问题**: 固件配置的 WiFi SSID/PASS 与实际网络不匹配

**解决**:
```c
static const char *WIFI_SSID = "oioioioioi";
static const char *WIFI_PASS = "890909!!";
```

### 4. Session ID 校验失败

**问题**: 服务器报错 `event session_id must equal producer_id device_id`

**原因**: 固件中 session_id 格式为 `session-dev-esp32-glass-001-xxxxxx`，与 device_id 不相等

**解决**: 修改 `ws_stream.c` 中 session_id 生成逻辑，使其等于 device_id：
```c
snprintf(s_session_id, sizeof(s_session_id), "%s", device_id);
```

### 5. Stream Chunk 缺少必要字段

**问题**: 服务器报错 `KeyError: 'sample_rate'` 和 `KeyError: 'duration_ms'`

**原因**: `audio_chat_stream_encode()` 生成的 header JSON 缺少 `sample_rate`、`channels`、`duration_ms` 字段

**解决**: 在 `audio_chat_stream.c` 中添加这些字段：
```c
"sample_rate":16000,"
"channels":1,"
"duration_ms":20,"
```

### 6. IMU Stream 未注册

**问题**: 服务器报错 `unknown stream_id: imu`

**原因**: 设备发送 IMU 数据前未发送 `stream.input.opened` 事件注册 stream

**解决**: 暂时禁用 IMU 上报（需要后续实现完整的 stream 注册流程）：
```c
// imu_start_reporting(NULL, 0);  // TODO: needs stream.input.opened event first
```

### 7. 音频 Stream 未注册 (核心问题)

**问题**: 设备注册成功后，服务器显示心跳超时，无音频数据到达

**分析**:
1. 设备发送 `control.device.registered` 后立即启动音频任务
2. 但音频 stream 未在服务器注册
3. 参考 `python-playback-glass` 实现，发送音频数据前必须先发送 `stream.input.opened` 事件

**尝试方案**:
在收到 `control.device.registered` 后发送 `stream.input.opened` 事件

**代码**:
```c
if (strcmp(event_name, "control.device.registered") == 0) {
    s_registered = true;
    // Open audio stream before starting
    char audio_open_payload[256];
    int n = snprintf(audio_open_payload, sizeof(audio_open_payload),
        "{\"stream_type\":\"sensor.mic\",\"stream_id\":\"audio\",\"format\":{\"codec\":\"pcm16le\",\"sample_rate\":16000,\"channels\":1,\"chunk_ms\":20}}");
    ws_control_send_event("stream.input.opened", audio_open_payload, n);
    // Start streaming tasks
    audio_start_streaming();
}
```

## 待解决问题

### 问题分析

**已确认固件字段正确** - `audio_chat_stream_encode()` 已包含 `sample_rate`、`channels`、`duration_ms` 字段（见 firmware/main/protocol/audio_chat_stream.c:77-79）

**服务器仍然报 `KeyError: 'duration_ms'`** - 可能原因：
1. **固件未重新烧录** - 需要重新编译并烧录包含修复的固件
2. **WebSocket 发送失败** - `ws_stream_send_audio()` 返回 `ESP_FAIL`（连接未就绪或编码失败）
3. **数据格式问题** - 编码后的二进制格式与服务器期望的不一致

**排查步骤**：
1. 重新编译固件：`cd firmware && idf.py build`
2. 重新烧录固件（所有三个文件）
3. 观察串口日志确认音频任务是否启动
4. 检查服务器日志中是否有 `stream.input.opened` 事件

### 根本原因分析

查看服务器日志发现：
- 设备注册成功 (`control.device.registered`)
- 但音频 chunks 始终报 `KeyError: 'duration_ms'` 或 `KeyError: 'sample_rate'`
- 即使固件代码包含这些字段，服务器仍未收到

**可能原因**：固件可能未重新烧录，或者 audio streaming 任务未实际执行发送逻辑

### 心跳超时问题

服务器配置 `heartbeat_timeout_seconds: 30`，设备注册后 30 秒无心跳则断开。

**可能原因**：
1. 麦克风硬件/I2S 配置问题导致无音频数据
2. 音频数据未通过 WebSocket 发送
3. Stream WebSocket 连接未就绪就尝试发送

### 最新服务器日志分析

```
# 设备注册成功但随后心跳超时
"event": "control.device.registered"
"reason": "heartbeat_timeout"

# Stream chunk 处理失败
"KeyError: 'duration_ms'"
"KeyError: 'sample_rate'"
```

这说明服务器收到了 stream chunk，但 header 中缺少必要字段。而固件代码中已经添加了这些字段，问题可能是**固件未重新烧录**。

## 调试命令

### 烧录固件

**方法一：Windows Python 直接烧录（推荐）**
```bash
# 1. 先复制固件到 Windows 下载目录
cp build/bootloader/bootloader.bin build/partition_table/partition-table.bin build/audio_chat_glass_firmware.bin /mnt/c/Users/kkkkkk/Downloads/

# 2. 在 Windows CMD 中执行烧录
cmd.exe /c "cd /d C:\Users\kkkkkk\Downloads && python -m esptool --chip esp32s3 -p COM3 -b 921600 write_flash 0x0 bootloader.bin 0x8000 partition-table.bin 0x10000 audio_chat_glass_firmware.bin"
```

**方法二：通过 idf.py 烧录**
```bash
cd examples/for-blind-app/devices/native-esp32-glass/firmware
idf.py -p COM3 flash
```

**方法三：手动 esptool 烧录（WSL2 中调用 Windows Python）**
```bash
# 复制固件到共享目录
mkdir -p /mnt/c/Users/kkkkkk/Downloads/esp32_build
cp build/bootloader/bootloader.bin build/partition_table/partition-table.bin build/audio_chat_glass_firmware.bin /mnt/c/Users/kkkkkk/Downloads/esp32_build/

# 调用 Windows Python 执行烧录
cmd.exe /c "cd /d C:\Users\kkkkkk\Downloads\esp32_build && python -m esptool --chip esp32s3 -p COM3 -b 921600 write_flash 0x0 bootloader.bin 0x8000 partition-table.bin 0x10000 audio_chat_glass_firmware.bin"
```

### 查看服务器日志

```bash
# 实时查看服务器日志
tail -100f /home/fkkkk/Projects/Personal/OpenAIglassesDemo/server.log

# 查看最近错误
grep -i "error\|exception\|traceback" /home/fkkkk/Projects/Personal/OpenAIglassesDemo/server.log | tail -50

# 查看心跳相关日志
grep -i "heartbeat\|timeout\|ping\|pong" /home/fkkkk/Projects/Personal/OpenAIglassesDemo/server.log | tail -30

# 查看 stream 相关日志
grep -i "stream\|chunk\|audio" /home/fkkkk/Projects/Personal/OpenAIglassesDemo/server.log | tail -50
```

### Windows 串口查看

**方法一：PowerShell 读取串口**
```powershell
$port = [System.IO.Ports.SerialPort]::new('COM3', 115200)
$port.Open()
while ($true) { if ($port.BytesToRead) { $port.ReadExisting() }; Start-Sleep -Millis 100 }
$port.Close()
```

**方法二：Python 脚本读取串口**
```python
# read_serial.py
import serial
s = serial.Serial('COM3', 115200, timeout=2)
while True:
    data = s.read(1024)
    if data:
        print(data.decode('utf-8', errors='ignore'))
```

## 配置文件

- 固件配置: `examples/for-blind-app/devices/native-esp32-glass/firmware/main/app/main.c`
- Stream 编码: `examples/for-blind-app/devices/native-esp32-glass/firmware/main/protocol/audio_chat_stream.c`
- WebSocket: `examples/for-blind-app/devices/native-esp32-glass/firmware/main/connectivity/ws_stream.c`
- 服务器: `examples/for-blind-app/audio-server/server.yaml`