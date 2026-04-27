# SDK v5 迭代记录

本文记录 SDK 团队在 `sdk-v4` 视频直连任务语义之后继续补齐的运行时能力。业务侧版本记录更新为 `sdk-v5`。

## 1. 输入反馈

`SDK对功能开发支持情况的说明.md` 中仍有几类非体验类 SDK 缺口：

1. SDK 业务 Task 虽然能创建、查询、取消和接收事件，但缺少可持久化的事件日志和恢复入口。
2. 后台任务缺少统一超时治理，业务团队难以用 SDK 层能力覆盖长任务等待超时。
3. 手机侧视频帧更接近单任务消费模型，缺少多视觉任务共享同一路帧的通用分发能力。

本轮仍不处理电话式实时语音对话、用户打断、全双工语音、公网/NAT 穿透、真实地图策略和端侧 SDK 包化。

## 2. 本轮 SDK 改动

### 2.1 SDK 业务 Task 事件日志

`TaskRuntimeSnapshot` 新增：

1. `created_at_ms`
2. `updated_at_ms`
3. `started_at_ms`
4. `completed_at_ms`
5. `timeout_ms`
6. `deadline_at_ms`
7. `events`

SDK 运行时会记录 `task.created`、`task.started`、外部事件、`task.completed`、`task.failed`、`task.cancelled`、`task.timeout` 和 `task.restored`。

### 2.2 超时治理

创建 SDK 业务 Task 时可在 `input_data` 中传入 `timeout_ms`。当查询、取消或派发事件时发现任务已经超过 `deadline_at_ms`，运行时会把任务推进到 `timeout`，写入结构化错误并追加 `task.timeout` 事件。

### 2.3 快照导出与恢复

`TaskRuntimeManager` 新增：

1. `export_snapshots()`
2. `restore_snapshots(...)`
3. `save_snapshots(path)`
4. `load_snapshots(path)`

这组接口先提供 JSON 兼容快照，宿主可以保存到文件；后续切数据库或对象存储时不需要业务 Task 改接口。

### 2.4 手机侧多任务帧分发

`PhoneRuntime.process_frame(...)` 支持把同一帧按 `stream_id` 和 `task_types` 分发给多个活跃手机任务。`PhoneTaskSnapshot` 新增 `frames_processed`，用于回放测试和真机联调观察。

终态任务不会再收到后续帧，避免已停止或已失败的任务继续消耗模型资源。

## 3. 本轮不进入 SDK 的内容

1. 多模型资源加载、YOLO 真实执行框架和性能保护。
2. 手机视觉任务优先级抢占、帧率降级和功耗治理。
3. 跨进程或跨服务的数据库级任务恢复。
4. 实时语音打断、全双工语音和公网/NAT 穿透。

这些仍属于后续 SDK 系统层迭代。

## 4. 文档同步

已同步更新：

1. `SDK对功能开发支持情况的说明.md`
2. `openaiglass-for-blind/SDK安装与能力开发指南.md`
3. `openaiglass-for-blind/sdk-version`

## 5. 验证范围

新增和调整测试覆盖：

1. SDK 业务 Task 事件日志与查询触发超时。
2. SDK 业务 Task 快照导出、恢复和继续派发事件。
3. SDK 业务 Task 快照保存到 JSON 文件并从文件恢复。
4. 手机侧同一路视频帧分发给多个活跃任务。
5. 手机侧按任务类型过滤分发，并跳过已停止任务。

