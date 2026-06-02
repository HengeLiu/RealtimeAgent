# ESP32-S3 固件烧录指南

## 方法：通过 WSL2 调用 Windows esptool

### 前置条件

1. ESP32-S3 设备连接到电脑（COM3）
2. 固件已编译完成
3. Windows 上已安装 Python 和 esptool

### 步骤

#### 1. 复制固件到 Windows 可访问路径

在 WSL2 终端：

```bash
cp /home/fkkkk/Projects/Personal/OpenAIglassesDemo/examples/for-blind-app/devices/native-esp32-glass/firmware/build/audio_chat_glass_firmware.bin /mnt/c/Users/kkkkkk/Downloads/
```

#### 2. 从 WSL2 调用 Windows cmd 执行烧录

```bash
cmd.exe /c "cd /d C:\Users\kkkkkk\Downloads && python -m esptool --chip esp32s3 -p COM3 -b 921600 write_flash 0x0 audio_chat_glass_firmware.bin"
```

参数说明：
- `--chip esp32s3` - 芯片型号
- `-p COM3` - 串口端口
- `-b 921600` - 烧录波特率
- `write_flash 0x0` - 写入地址
- `audio_chat_glass_firmware.bin` - 固件文件

### 其他方式

#### 直接在 Windows PowerShell 中烧录

如果 WSL2 无法访问串口，直接在 Windows PowerShell 中执行：

```powershell
cd C:\Users\kkkkkk\Downloads
python -m esptool --chip esp32s3 -p COM3 -b 921600 write_flash 0x0 audio_chat_glass_firmware.bin
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