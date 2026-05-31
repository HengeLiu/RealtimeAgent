# ESP32-S3 固件编译指南

## 编译环境

- ESP-IDF: v5.0
- Python: 3.11+
- 目标芯片: ESP32-S3
- Flash大小: 8MB

## 编译步骤

### 1. 设置ESP-IDF环境

```bash
source ~/esp/esp-idf/export.sh
```

### 2. 进入固件目录

```bash
cd /home/fkkkk/Projects/Personal/OpenAIglassesDemo/examples/for-blind-app/devices/native-esp32-glass/firmware
```

### 3. 设置目标芯片

```bash
idf.py set-target esp32s3
```

### 4. 编译固件

```bash
idf.py build
```

### 5. 查看编译产物

编译成功后，固件文件位于 `build/` 目录：

- `bootloader/bootloader.bin` - 引导加载程序
- `partition_table/partition-table.bin` - 分区表
- `srmodels/srmodels.bin` - 语音识别模型
- `audio_chat_glass_firmware.bin` - 主应用程序固件

## 常见编译问题及解决方法

### 问题1: esp_websocket_client组件与ESP-IDF 5.0不兼容

**错误信息：**
```
error: 'esp_transport_ws_config_t' has no member named 'auth'
error: implicit declaration of function 'esp_transport_ws_get_upgrade_request_status'
```

**原因：**
ESP-IDF 5.0移除了某些API和结构体成员，导致esp_websocket_client组件无法编译。

**解决方法：**

1. 将esp_websocket_client组件复制到components目录：
```bash
mkdir -p components
cp -r managed_components/espressif__esp_websocket_client components/esp_websocket_client
```

2. 修改 `components/esp_websocket_client/esp_websocket_client.c` 文件：

**修改1：删除`.auth`成员（约第569行）**
```c
// 原代码
const esp_transport_ws_config_t config = {
    .ws_path = client->config->path,
    .sub_protocol = client->config->subprotocol,
    .user_agent = client->config->user_agent,
    .headers = client->config->headers,
#if WS_TRANSPORT_HEADER_CALLBACK_SUPPORT
    .header_hook = websocket_header_hook,
    .header_user_context = client,
#endif
    .auth = client->config->auth,  // 删除这一行
    .propagate_control_frames = true
};

// 修改后代码
const esp_transport_ws_config_t config = {
    .ws_path = client->config->path,
    .sub_protocol = client->config->subprotocol,
    .user_agent = client->config->user_agent,
    .headers = client->config->headers,
#if WS_TRANSPORT_HEADER_CALLBACK_SUPPORT
    .header_hook = websocket_header_hook,
    .header_user_context = client,
#endif
    .propagate_control_frames = true
};
```

**修改2：删除`esp_transport_ws_get_upgrade_request_status`函数调用（约第1193行）**
```c
// 原代码
client->error_handle.esp_ws_handshake_status_code = esp_transport_ws_get_upgrade_request_status(client->transport);

// 修改后代码
client->error_handle.esp_ws_handshake_status_code = 0;
```

3. 修改 `main/idf_component.yml` 文件，移除esp_websocket_client依赖：
```yaml
dependencies:
  espressif/esp-sr: '*'
  espressif/esp32-camera: '*'
  # 移除 espressif/esp_websocket_client: '*'
```

4. 删除managed_components目录中的esp_websocket_client：
```bash
rm -rf managed_components/espressif__esp_websocket_client
```

5. 重新编译：
```bash
idf.py reconfigure
idf.py build
```

### 问题2: app partition is too small for binary

**错误信息：**
```
Error: app partition is too small for binary audio_chat_glass_firmware.bin size 0x112df0
- Part 'factory' 0/0 @ 0x10000 size 0x100000 (overflow 0x12df0)
```

**原因：**
固件大小超过了factory分区的大小。默认的single app partition table只分配了1MB空间。

**解决方法：**

1. 修改 `sdkconfig.defaults` 文件，启用自定义partition table：
```
# Partition Table
CONFIG_PARTITION_TABLE_CUSTOM=y
CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions.csv"
```

2. 确保 `partitions.csv` 文件中factory分区大小足够（当前配置为3M）：
```csv
# Name,   Type, SubType, Offset,   Size, Flags
nvs,      data, nvs,     0x9000,   0x6000,
phy_init, data, phy,     0xf000,   0x1000,
model,    data, spiffs,  0x10000,  0x400000,
factory,  app,  factory, 0x410000, 3M,
```

3. 重新编译：
```bash
rm -f sdkconfig
idf.py reconfigure
idf.py build
```

### 问题3: Partitions tables occupies too much flash

**错误信息：**
```
Partitions tables occupies 7.1MB of flash (7405568 bytes) which does not fit in configured flash size 2MB.
```

**原因：**
Partition table定义的分区总大小超过了配置的flash大小（默认为2MB）。

**解决方法：**

1. 修改 `sdkconfig.defaults` 文件，设置flash size为8MB：
```
# Flash Size
CONFIG_ESPTOOLPY_FLASHSIZE_8MB=y
```

2. 重新编译：
```bash
rm -f sdkconfig
idf.py reconfigure
idf.py build
```

### 问题4: 编译环境问题

**症状：**
- 命令超时
- 找不到idf.py命令
- 编译过程卡住

**解决方法：**

1. 删除旧的编译产物：
```bash
rm -rf build managed_components sdkconfig
```

2. 重新设置ESP-IDF环境：
```bash
source ~/esp/esp-idf/export.sh
```

3. 重新编译：
```bash
idf.py set-target esp32s3
idf.py build
```

## 配置文件说明

### sdkconfig.defaults

默认配置文件，包含：
- Partition Table配置（使用自定义partition table）
- Flash Size配置（8MB）
- 主频设置（240MHz）
- WiFi配置
- WebSocket配置
- 内存配置
- 日志配置

### partitions.csv

自定义分区表，包含：
- nvs: 非易失性存储（24KB）
- phy_init: RF初始化数据（4KB）
- model: 语音识别模型（4MB）
- factory: 主应用程序（3MB）

### idf_component.yml

组件依赖声明，包含：
- espressif/esp-sr: 语音识别组件
- espressif/esp32-camera: 摄像头组件

注意：esp_websocket_client组件已移除依赖，使用本地components目录下的修改版本。

## 编译成功标志

编译成功后，终端会显示：
```
Project build complete. To flash, run this command:
...
audio_chat_glass_firmware.bin binary size 0x112df0 bytes. Smallest app partition is 0x300000 bytes. 0x1ed210 bytes (64%) free.
```

固件大小约1.07MB，factory分区大小为3MB，剩余空间约1.93MB（64%）。

## 下一步

编译成功后，请参考 [FLASHING.md](FLASHING.md) 进行固件烧录。