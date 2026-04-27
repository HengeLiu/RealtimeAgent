# host/phone

本目录放盲人 AI 眼镜手机端业务入口、宿主装配说明和产品侧配置。业务开发者只从本目录下的 Xcode 工程启动手机端，不直接打开 SDK 目录。

手机端业务入口：

```text
host/phone/ios/GlassesVideoReceiver.xcodeproj
```

这个 Xcode 工程属于业务项目，内部引用 [../../../openaiglass-sdk/phone-ios](../../../openaiglass-sdk/phone-ios) 的通用 iOS 手机 SDK 运行时代码。SDK 运行时负责设备注册、视频接收、手机侧任务承载和与服务端运行时通信；业务项目负责配置、启动入口和后续业务插件集成。

具体业务能力插件不放在手机宿主里。当前业务 Xcode 工程已经把以下插件编译进 App，并由 [ios/BusinessGlassesVideoReceiverApp.swift](ios/BusinessGlassesVideoReceiverApp.swift) 在启动时注册：

1. 找物体：源码位于 [../../capabilities/find_object/phone/ios](../../capabilities/find_object/phone/ios)，任务类型为 `find_object_phone_task`，结果事件为 `phone.vision.find_object.result`。
2. 红绿灯：源码位于 [../../capabilities/traffic_light/phone/ios](../../capabilities/traffic_light/phone/ios)，任务类型为 `traffic_light_phone_task`，结果事件为 `phone.vision.traffic_light.result`。

注意：当前 iOS 插件已经是真实手机帧处理链路，但检测算法仍是业务侧启发式占位实现，用于验证手机任务、视频帧、结果上报和服务端通知闭环；还不是正式 YOLO 或红绿灯模型。

手机端本地配置源放在本业务工程：

```bash
cp host/phone/config/AppConfig.plist.example host/phone/config/AppConfig.plist
bash scripts/sync_sdk_live_config.sh
```

同步脚本只写业务目录下的 `host/phone/config/AppConfig.plist`。业务侧 Xcode 工程会把这个配置文件作为 App 资源打包，不再写入 SDK 目录。

业务开发者启动手机端时使用本目录所在业务工程提供的入口，不直接进入 SDK 目录：

```bash
bash scripts/run_phone.sh open
```

首次执行时会自动从模板创建业务本地配置，并打印需要修改的配置文件和字段。基础连接配置以 `config/local_server.env` 为源，脚本会自动同步到 `host/phone/config/AppConfig.plist` 和 `host/glass/config/local_build.env`；眼镜 Wi-Fi 仍在 `host/glass/config/local_build.env` 中维护。不要用临时环境变量覆盖。修改完成后再次执行同一命令即可。

如只需要验证 iOS 工程可构建：

```bash
bash scripts/run_phone.sh build-sim
```
