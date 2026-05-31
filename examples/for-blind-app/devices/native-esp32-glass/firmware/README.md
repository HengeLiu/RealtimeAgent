# ESP32-S3 Glass Firmware

盲人眼镜 ESP32-S3 固件，基于 ESP-IDF 5.0 开发。

## 功能

- WakeNet 唤醒词检测
- I2S 麦克风音频采集
- 摄像头 JPEG 抓拍
- IMU 传感器数据
- WebSocket 音视频流
- audio-chat.v1 协议

## 编译

```bash
cd firmware
source /home/fkkkk/esp/esp-idf/export.sh
idf.py set-target esp32s3
idf.py build
```

## 烧录

```bash
idf.py -p /dev/ttyUSB0 flash
idf.py -p /dev/ttyUSB0 monitor
```

## 文件说明

- `main/` - 业务代码
  - `app/` - 应用入口
  - `drivers/` - 硬件驱动
  - `connectivity/` - 网络连接
  - `protocol/` - 协议编解码
  - `utils/` - 工具函数
- `idf_component.yml` - 组件依赖

## 依赖组件

`idf_component.yml` 中声明的组件会在首次编译时自动下载到 `managed_components/` 目录。删除后重新编译会自动恢复。

## 配置

`sdkconfig` 为运行时生成，可删除后用 `idf.py set-target` 重新生成。