# phone_flutter

这是手机端正式 Flutter 实现目录。

当前阶段定位：

- 作为 iPhone 真机运行的手机端通信壳
- 承接 server 注册 / 心跳 / 本地控制服务 / 任务级 WebSocket 入口
- 承接找物任务的最小 Dart 侧处理链路
- 作为仓库当前唯一正式手机端实现

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
- 任务状态回传 server
- 真机运行日志写入 iPhone App 沙盒内的日志文件
- 可切换的检测后端抽象层
- CoreML / TFLite / ONNX Runtime Mobile 后端占位与配置落位

## 当前未完成

- 尚未完成真机签名与首次部署
- 尚未完成与本地 server + glass 的一次完整真机联调
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
flutter pub get
flutter run -d 00008130-000579010281001C
```

## 与仓库现有代码的关系

- 历史原型：
  - [connection-tests/flutter_app](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/connection-tests/flutter_app)
- 当前正式实现：
  - 本目录

仓库中的 Python 手机模拟运行时已删除，手机端后续统一以本目录为准。

## 日志说明

iPhone 真机运行时，日志不会直接写回 Mac 仓库路径。

当前实现会把运行日志写入 iPhone App 沙盒的 Documents 目录下：

- `Documents/logs/phone-001-runtime.log`

App 页面中的 `本机状态` 会显示当前实际日志文件路径，`Runtime Logs` 仍会保留最近的内存日志用于现场查看。
