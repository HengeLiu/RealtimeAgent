# ESP32-S3 固件烧录指南

## 方法：通过 WSL2 调用 Windows esptool

### 前置条件

1. ESP32-S3 设备连接到电脑（COM3）
2. 固件已编译完成
3. Windows 上已安装 Python 和 esptool

### 步骤

#### 1. 复制固件到 Windows 可访问路径

固件文件位于：
```
\\wsl.localhost\ubuntu\home\fkkkk\Projects\Personal\OpenAIglassesDemo\examples\for-blind-app\devices\native-esp32-glass\firmware\build\
```

需要复制以下4个文件到 `C:\Users\kkkkkk\Downloads\`：
- `bootloader/bootloader.bin` - 引导加载程序
- `partition_table/partition-table.bin` - 分区表
- `srmodels/srmodels.bin` - 语音识别模型
- `audio_chat_glass_firmware.bin` - 主应用程序固件

在 WSL2 终端：

```bash
cp /home/fkkkk/Projects/Personal/OpenAIglassesDemo/examples/for-blind-app/devices/native-esp32-glass/firmware/build/bootloader/bootloader.bin /mnt/c/Users/kkkkkk/Downloads/
cp /home/fkkkk/Projects/Personal/OpenAIglassesDemo/examples/for-blind-app/devices/native-esp32-glass/firmware/build/partition_table/partition-table.bin /mnt/c/Users/kkkkkk/Downloads/
cp /home/fkkkk/Projects/Personal/OpenAIglassesDemo/examples/for-blind-app/devices/native-esp32-glass/firmware/build/srmodels/srmodels.bin /mnt/c/Users/kkkkkk/Downloads/
cp /home/fkkkk/Projects/Personal/OpenAIglassesDemo/examples/for-blind-app/devices/native-esp32-glass/firmware/build/audio_chat_glass_firmware.bin /mnt/c/Users/kkkkkk/Downloads/
```

#### 2. 在 Windows PowerShell 中烧录固件

**完整烧录命令（烧录所有4个文件）：**

```powershell
cd C:\Users\kkkkkk\Downloads
python -m esptool --chip esp32s3 -p COM3 -b 460800 write-flash --flash-mode dio --flash-size 8MB --flash-freq 80m 0x0 bootloader.bin 0x8000 partition-table.bin 0x10000 srmodels.bin 0x410000 audio_chat_glass_firmware.bin
```

参数说明：
- `--chip esp32s3` - 芯片型号
- `-p COM3` - 串口端口（根据实际设备管理器中的端口修改）
- `-b 460800` - 烧录波特率
- `write-flash` - 写入命令
- `--flash-mode dio` - Flash模式
- `--flash-size 8MB` - Flash大小（必须与编译配置一致）
- `--flash-freq 80m` - Flash频率
- `0x0 bootloader.bin` - 引导加载程序地址
- `0x8000 partition-table.bin` - 分区表地址
- `0x10000 srmodels.bin` - 语音识别模型地址
- `0x410000 audio_chat_glass_firmware.bin` - 主应用程序固件地址

**简化烧录命令（仅烧录主应用程序）：**

如果只是更新主应用程序，可以使用简化命令：

```powershell
cd C:\Users\kkkkkk\Downloads
python -m esptool --chip esp32s3 -p COM3 -b 921600 write-flash 0x410000 audio_chat_glass_firmware.bin
```

#### 使用 ESP-IDF idf.py

在 Windows 上安装 ESP-IDF 后：

```powershell
cd C:\Users\kkkkkk\Projects\Personal\OpenAIglassesDemo\examples\for-blind-app\devices\native-esp32-glass\firmware
idf.py -p COM3 flash
```

### 常见问题

**Q: 串口 PermissionError**
A: 串口被其他程序占用，关闭 Arduino IDE、VS Code 等程序后重试。

**Q: COM3 找不到**
A: 在 Windows 设备管理器中确认 ESP32 对应的 COM 端口号。

**Q: WSL2 无法访问 USB**
A: WSL2 默认不支持 USB passthrough，需要通过 usbipd 或直接使用 Windows 工具。