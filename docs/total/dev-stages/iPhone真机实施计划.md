# iPhone 真机实施计划

## 1. 目标

本文档用于约束手机端从历史 Python / 原型 Flutter 形态迁移到 iPhone 真机运行的实施路径。

本计划的目标不是一步到位把所有检测逻辑都迁到 iOS，而是先完成：

- 手机端真实运行在 iPhone
- 手机端能与 `nextgen` server 完成注册、心跳和状态展示
- 手机端能在任务期间启动本地控制服务与任务级 WebSocket 入口
- 眼镜端可以根据服务器协调结果连接到手机端
- 手机端可以在任务级连接中接收图片/视频帧并回传引导建议

---

## 2. 当前现实约束

截至 2026-04-05，仓库当前状态如下：

- Python 手机模拟运行时已从 `nextgen/` 中删除
- 已有 Flutter 原型可参考：
  - [connection-tests/flutter_app](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/connection-tests/flutter_app)
- 已有正式 Flutter 手机端目录：
  - [clients/phone_flutter](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/clients/phone_flutter)
- 当前仍需补齐 Flutter + Xcode 真机运行环境

因此当前实施策略为：

1. 继续以 `clients/phone_flutter/` 为唯一手机端实现目录
2. 在具备 Flutter + Xcode 的机器上完成 `flutter pub get`、iOS 工程生成、签名和真机运行

---

## 3. 阶段拆分

### 阶段 A：真机通信壳

范围：

- 新建正式目录 `clients/phone_flutter/`
- 实现基础 UI
- 实现到 server 的注册 / 心跳
- 实现本地 HTTP 控制服务
- 实现任务级 WebSocket 入口
- 展示日志和状态

验收标准：

- App 能在 iPhone 真机启动
- App 能向 server 注册并周期心跳
- server 能看到 `phone-001` 在线
- App 能接收 `/device-api/task/prepare-peer-link`
- App 能接收 `/device-api/task/stop-peer-link`
- App 能接受来自 glass 的任务级 WebSocket 连接

### 阶段 B：找物链路迁入

范围：

- 把当前 Python 手机端的最小找物处理逻辑迁成 Dart
- 在 `/stream/frame` 接收图片帧
- 产出引导建议并回给 glass

验收标准：

- glass -> iPhone 的图片帧链路可用
- iPhone 可回传引导建议
- server 仍只收任务状态，不接引导正文

### 阶段 C：本地检测能力增强

范围：

- 评估 CoreML / TFLite / ONNX Runtime Mobile
- 将真正需要手机本地执行的模型迁入

验收标准：

- 找物不再依赖 Python 启发式检测
- 真机性能、时延和稳定性达到联调可接受范围

---

## 4. 当前阶段实施内容

当前这一轮已完成阶段 A 和阶段 B 的最小链路，开始推进阶段 C 的后端抽象。

并且已经完成：

- 删除 `nextgen/apps/phone` Python 手机模拟运行时代码
- 删除依赖 Python 手机模拟的启动脚本和 demo
- 测试支持服务已删除，当前改为 `server`、`glass`、iPhone 真机 `phone` 三端独立运行
- 样例数据注入与语音控制由眼镜自身 UI 页面承担

明确不做：

- 不迁移模型推理到 iOS
- 不实现完整语音会话到手机端
- 不处理上架、TestFlight、发布签名
- 不在本阶段接入真实可运行的 CoreML / TFLite / ONNX Runtime Mobile 模型文件

---

## 5. 目录约束

正式手机端目录固定为：

- [clients/phone_flutter](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/clients/phone_flutter)

说明：

- `connection-tests/flutter_app` 保留为历史原型参考，不继续扩写
- 正式实现统一进入 `clients/phone_flutter`

---

## 6. 需要的 iOS 权限

第一版建议至少补这些权限：

- `NSCameraUsageDescription`
- `NSMicrophoneUsageDescription`
- `NSLocalNetworkUsageDescription`
- `NSPhotoLibraryUsageDescription`

如果后续引入 Bonjour 局域网发现，再补：

- `NSBonjourServices`

---

## 7. 本机需要补齐的工具

要让这套工程真正跑到 iPhone，需要在你的 Mac 上完成：

1. 安装 Flutter SDK
2. 安装完整 Xcode
3. 执行 `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`
4. 在 `clients/phone_flutter/` 下执行 `flutter create . --platforms=ios`
5. 执行 `flutter pub get`
6. 用 Xcode 打开 `ios/Runner.xcworkspace`
7. 配置 Team / Bundle Identifier / Signing
8. 连接 iPhone 真机并运行

---

## 8. 当前阶段验收标准

当前阶段代码落地完成后，应达到：

- 仓库内存在正式 Flutter 手机端目录
- 有可读的 README 和运行说明
- 有基础 UI、HTTP 控制服务、任务级 WebSocket 服务代码
- 有与 `nextgen` 协议对应的最小模型和服务层
- 在 Dart 侧具备最小找物图片帧处理链路
- `/stream/frame` 和 `/find-object/frame-analysis` 都能产出引导建议
- 检测后端已经抽象为可替换实现
- 已预留 CoreML / TFLite / ONNX Runtime Mobile 的配置落位
- 文档已说明后续真机运行步骤

---

## 9. 下一步

本计划文档落地后，当前继续执行：

1. 在真机上验证 `register -> heartbeat -> prepare-peer-link -> peer ws -> guidance`
2. 选定一种真机本地模型路线：CoreML / TFLite / ONNX Runtime Mobile
3. 把真实模型文件和前后处理接入当前已抽象好的检测后端
