# 模型文件说明

本目录的模型文件未直接包含在仓库中，需要手动下载并放置到 `app/src/main/assets/` 目录。

## 需要下载的模型

| 文件 | 大小 | 用途 |
|------|------|------|
| `yolov8n.bin` + `yolov8n.param` | ~6MB | YOLO 目标检测 |
| `trafficlight.ncnn.bin` + `trafficlight.ncnn.param` | ~167MB | 红绿灯状态检测 |

## 下载地址

模型文件存放在阿里云 OSS，请联系项目维护者获取下载链接。

## 放置位置

```
examples/android-phone/app/src/main/assets/
├── yolov8n.bin
├── yolov8n.param
├── trafficlight.ncnn.bin
└── trafficlight.ncnn.param
```

## 构建说明

1. 下载上述模型文件
2. 放入 `app/src/main/assets/` 目录
3. 执行 `./gradlew assembleDebug` 构建 APK