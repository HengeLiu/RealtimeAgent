# host/phone

本目录放盲人 AI 眼镜手机端宿主装配说明和产品侧配置。通用 iOS 手机 SDK 运行时已经移到 [../../../openaiglass-sdk/phone-ios](../../../openaiglass-sdk/phone-ios)，负责设备注册、视频接收、手机侧任务承载和与服务端运行时通信。

具体业务能力插件不放在手机宿主里，当前找物体能力的 iOS 插件源码位于 [../../capabilities/find_object/phone/ios](../../capabilities/find_object/phone/ios)。

手机端本地配置源放在本业务工程：

```bash
cp host/phone/config/AppConfig.plist.example host/phone/config/AppConfig.plist
bash scripts/sync_sdk_live_config.sh
```

同步脚本会把业务配置写入 SDK iOS 运行时工程的 `AppConfig.plist`，该文件只是运行目标，不是业务配置源。

业务开发者启动手机端时使用本目录所在业务工程提供的入口，不直接进入 SDK 目录：

```bash
bash scripts/run_phone.sh open
```

首次执行时会自动从模板创建业务本地配置，并提示修改局域网 IP、设备令牌和眼镜 Wi-Fi。修改完成后再次执行同一命令即可。

如只需要验证 iOS 工程可构建：

```bash
bash scripts/run_phone.sh build-sim
```
