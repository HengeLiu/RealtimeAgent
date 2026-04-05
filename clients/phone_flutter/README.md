# phone_flutter

这是手机端正式 Flutter 实现目录。

当前阶段定位：

- 作为 iPhone 真机运行的手机端通信壳
- 承接 server 注册 / 心跳 / 本地控制服务 / 任务级 WebSocket 入口
- 承接找物任务的最小 Dart 侧处理链路
- 为后续迁移 `nextgen/apps/phone` 的能力提供 Dart 版本落位

## 当前已落代码

- `lib/main.dart`
- `lib/models/`
- `lib/runtime/`
- `lib/services/`
- `lib/screens/`
- `lib/tasks/`

当前已经具备：

- server 注册 / 心跳
- 本地 HTTP 控制服务
- 任务级 WebSocket 入口
- `/stream/frame` 的最小图片帧解码与启发式找物处理
- `/find-object/frame-analysis` 的 Dart 侧引导建议生成
- 可切换的检测后端抽象层
- CoreML / TFLite / ONNX Runtime Mobile 后端占位与配置落位

## 当前未完成

- 本机没有 Flutter CLI，尚未生成完整 iOS Runner 工程
- 本机没有完整 Xcode 开发目录，未进行真机编译
- 还未接入真正的 iOS 本地检测模型

## 当前检测后端状态

- `heuristic`
  - 已实现
  - 用于真机前期联调
- `coreml`
  - 已预留配置和后端占位
  - 尚未接真实模型
- `tflite`
  - 已预留配置和后端占位
  - 尚未接真实模型
- `onnxRuntime`
  - 已预留配置和后端占位
  - 尚未接真实模型

## 后续在具备 Flutter + Xcode 的机器上执行

```bash
cd clients/phone_flutter
flutter create . --platforms=ios
flutter pub get
flutter run -d <your-iphone-device-id>
```

## 与仓库现有代码的关系

- 历史原型：
  - [connection-tests/flutter_app](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/connection-tests/flutter_app)
- 当前 Python 参考实现：
  - [nextgen/apps/phone](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/nextgen/apps/phone)

正式手机端后续统一以本目录为准。
