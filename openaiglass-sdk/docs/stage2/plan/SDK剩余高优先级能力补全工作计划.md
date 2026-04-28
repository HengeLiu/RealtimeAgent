# SDK 剩余高优先级能力补全工作计划

更新时间：2026-04-28

## 1. 文档定位

本文档用于承接 sdk-v13 之后仍然影响功能开发效率和真实联调稳定性的 SDK 欠缺项。本轮只处理用户重新排序后的四个问题，未列出的能力暂时不做。

本轮优先级从高到低为：

1. 手机视觉执行框架的资源管理：真 iOS 运行时补统一模型资源池、任务抢占和功耗治理。已在 sdk-v14 完成第一版。
2. 完整播放仲裁和用户语音打断：统一普通 Agent 回复、任务通知、视觉告警和用户语音打断。已在 sdk-v15 完成第一版。
3. 账号权限、组织管理和远程配置中心：补完整授权、审计、组织树和远程配置中心。已在 sdk-v16 完成第一版。
4. 分布式任务持久化：补多进程、多实例、数据库级任务平台；第一版优先使用 SQLite 文件库和内存库。

## 2. 总体原则

1. SDK 负责系统能力，业务能力仍只写在 `openaiglass-for-blind` 或外部业务项目中。
2. 每项能力先补设计文档，再补代码和测试，最后更新开发指南、版本记录和迭代记录。
3. SQLite 只作为轻量本地数据库和单机多进程存储，不把它描述为跨机器分布式数据库。
4. 用户语音打断先做 SDK 事件、播放状态和仲裁语义，不承诺已经完成全双工实时语音、回声消除和复杂 VAD。
5. 远程配置中心先做 Provider 抽象、文件 Provider、内存 Provider 和审计链路，真实云端配置服务后续替换 Provider。

## 3. 阶段一：设计阶段

### 3.1 iOS 手机视觉资源管理设计

设计产物：

1. 更新 `手机视觉资源管理设计.md`，补充 iOS 真机资源管理目标。
2. 定义 Swift 侧 `VisionResourceCoordinator`、`VisionModelPool`、`VisionTaskLease`、`VisionPowerPolicy`。
3. 明确任务抢占规则：前台任务优先、critical 视觉告警可抢占低优先级任务、后台任务降频而不是无限排队。
4. 明确功耗治理规则：低电量、过热、后台模式和长时间连续推理时如何降帧、暂停或拒绝新任务。

不做内容：

1. 不把 YOLO、盲道、红绿灯或找物模型写进 SDK。
2. 不在 SDK 中绑定某个具体模型框架。

### 3.2 统一播放仲裁和用户打断设计

设计产物：

1. 更新 `通知抢播与用户打断策略设计.md`，从通知仲裁扩展为统一播放仲裁。
2. 定义 `PlaybackIntent`、`PlaybackArbiter`、`PlaybackLease`、`UserInterruptEvent`。
3. 统一四类输入：普通 Agent 回复、Task 通知、手机视觉告警、用户语音打断。
4. 明确打断后的处理语义：丢弃、恢复、摘要补偿、转入下一条队列。

不做内容：

1. 不实现完整实时语音模型。
2. 不让业务 Task 直接控制 TTS 或播放器。

### 3.3 账号权限、组织管理和远程配置中心设计

设计产物：

1. 更新 `账号级设备组织设计.md`。
2. 定义 `AccountDirectory`、`OrganizationNode`、`RoleBinding`、`PermissionPolicy`、`AuditSink`、`ConfigProvider`。
3. 明确权限检查点：设备注册、绑定、任务创建、Tool 调用、Task 访问设备能力、远程配置读取。
4. 明确审计事件结构：谁在何时对哪个账号、设备组、设备或任务执行了什么动作，结果是什么。

不做内容：

1. 不实现商业后台 UI。
2. 不绑定外部用户中心。

### 3.4 SQLite 任务持久化设计

设计产物：

1. 更新 `任务持久化生产化设计.md`。
2. 定义 `SQLiteTaskPersistenceStore`、任务租约、事件游标、事件幂等键、恢复锁。
3. 明确三种存储形态：内存库用于单测，SQLite 文件库用于单机多进程，后续外部数据库用于跨机器多实例。
4. 明确 SQLite WAL、事务边界和迁移版本。

不做内容：

1. 不把 SQLite 描述成跨机器分布式数据库。
2. 不在第一版引入 Redis、PostgreSQL 或复杂分布式锁。

## 4. 阶段二：开发阶段

### 4.1 迭代 A：真 iOS 手机视觉资源管理

主要开发任务：

1. 在 iOS 运行时新增视觉资源协调器，接管 Swift 插件的帧率、最大并发、模型 lease 和任务取消。
2. 把服务端下发的 `vision_policy` 映射到 iOS 侧统一策略。
3. 增加任务抢占和降级：新任务无法拿到资源时返回结构化拒绝或过载事件。
4. 把资源状态上报到服务端任务事件和 iOS 调试日志。

测试要求：

1. Swift 单元测试覆盖帧率限制、最大并发、低优先级拒绝、高优先级抢占。
2. Python contract 测试覆盖 `vision.task.overloaded`、`vision.task.preempted`、`vision.resource.denied` 的事件格式。
3. package-check 继续证明 iOS 源码包清单包含新增运行时代码和测试。

### 4.2 迭代 B：统一播放仲裁和用户打断入口

状态：已完成第一版，落地版本 sdk-v15。

主要开发任务：

1. 在服务端引入统一 `PlaybackArbiter`，把 Agent 回复、任务通知、视觉告警都转成 `PlaybackIntent`。已完成。
2. 把 `NotificationCoordinator` 收敛为仲裁输入或兼容层，不再成为唯一播放策略入口。已完成，通知仍负责去重和通知级队列，播报进入 `PlaybackArbiter`。
3. 新增用户打断控制事件，先支持“停止当前播报并清空或保留队列”的半双工语义。已完成，控制消息为 `user.voice.interrupt`。
4. 在运行态快照输出当前播放 lease、等待队列、最近仲裁决策和最近用户打断。已完成，对应字段为 `active_playback_intent`、`pending_playback_intents`、`recent_playback_decisions`。

测试要求：

1. 单元测试覆盖低优先级排队、高优先级抢播、用户打断、打断后队列清理。已完成。
2. 回放或语音样例测试覆盖普通 Agent 回复与任务通知的顺序不互相污染。当前由 VoiceRuntime 队列和播放仲裁单测覆盖，真实设备回放后续可继续补强。
3. DEBUG 日志和运行态快照可以解释每次播放决策。已完成快照字段，日志后续按真机排障再增强。

### 4.3 迭代 C：账号权限、组织管理和配置 Provider

状态：已完成第一版，落地版本 sdk-v16。

主要开发任务：

1. 在 SDK 中新增账号目录和组织树模型，支持默认本地账号、组织节点、设备组和角色绑定。已完成，核心对象为 `AccountGovernanceRuntime`、`OrganizationNode`、`RoleBinding`。
2. 增加最小权限策略：设备注册、跨账号绑定、Task 访问设备能力、Tool 调用前检查权限。已完成统一 `authorize` / `require_permission` 入口，设备注册和绑定已写审计。
3. 增加审计事件输出，先支持内存和文件 sink。已完成 `MemoryAuditSink`、`FileAuditSink`。
4. 增加远程配置 Provider 抽象，先落地内存 Provider 和文件 Provider，预留 HTTP Provider。已完成 `MemoryConfigProvider`、`FileConfigProvider`。

测试要求：

1. 本地默认账号不增加普通开发启动成本。已完成，默认 Provider 和内存审计自动启用。
2. 跨组织访问被拒绝并记录审计事件。已完成账号级和组织级权限检查测试，跨账号绑定 deny 审计已覆盖。
3. 配置 Provider 的配置值变更可以被 SDK 读取，并能在运行态快照中看到配置版本。已完成。

### 4.4 迭代 D：SQLite 任务持久化

主要开发任务：

1. 新增 `SQLiteTaskPersistenceStore`，使用 Python 标准库 `sqlite3`。
2. 设计并创建 `tasks`、`task_events`、`task_leases`、`schema_migrations` 表。
3. 支持事件幂等、任务快照恢复、终态清理和单机多进程 WAL。
4. `TaskRuntimeManager` 支持通过配置选择内存、文件或 SQLite 存储。

测试要求：

1. 单元测试覆盖建表、写任务、写事件、重复事件幂等、重启恢复。
2. 多 manager 使用同一个 SQLite 文件时，租约能阻止同一任务被重复恢复执行。
3. 文件型存储继续兼容，不因 SQLite 引入而失效。

## 5. 阶段三：验收阶段

每个迭代完成后必须完成专项验收，并在对应 `iteration-v*.md` 中记录真实结果。

### 5.1 iOS 手机视觉资源验收

通过标准：

1. iOS 业务插件不需要自行实现帧率限制、模型并发和任务过载。
2. 多任务竞争资源时，SDK 能按优先级和功耗策略给出结构化结果。
3. 服务端能看到资源事件，回放和真机日志能定位过载原因。

### 5.2 播放仲裁与用户打断验收

状态：sdk-v15 第一版已通过自动化验收。

通过标准：

1. 普通 Agent 回复、任务通知和视觉告警都经过同一仲裁入口。已完成。
2. 用户打断事件可以终止当前播报，并按策略处理队列。已完成。
3. 运行态快照可以解释当前播报为什么被播放、排队、抢播或丢弃。已完成。

### 5.3 账号权限与配置中心验收

状态：sdk-v16 第一版已通过自动化验收。

通过标准：

1. 本地单账号开发无需额外配置即可跑通。已完成。
2. 跨账号或跨组织访问会被拒绝并记录审计事件。已完成。
3. 配置 Provider 可以在不改业务代码的情况下调整 SDK 策略参数。已完成。

### 5.4 SQLite 任务持久化验收

通过标准：

1. 服务重启后任务状态、事件日志和幂等记录仍可恢复。
2. 单机多进程同时访问同一 SQLite 文件时，任务租约行为可预测。
3. SQLite 存储失败时有结构化错误，业务 Task 不依赖具体数据库实现。

## 6. 总体验收命令建议

每轮至少执行：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit -q
python -m compileall -q openaiglass-sdk/server-python
PYTHONPATH=openaiglass-sdk/server-python uv run --with setuptools --with wheel openaiglass.sdk.package-check --repo-root .
git diff --check
```

涉及 iOS 运行时时追加：

```bash
xcodebuild test -project openaiglass-sdk/phone-ios/GlassesVideoReceiver.xcodeproj -scheme GlassesVideoReceiver -destination 'platform=iOS Simulator,name=iPhone 16'
```

涉及真机联调时追加：

```bash
bash script/sync_sdk_live_config.sh
bash script/run_sdk_live_check.sh --report logs/sdk-live-check-priority4.json
```

## 7. 完成判定

本轮四项全部完成后，功能开发团队应能做到：

1. 真 iOS 手机视觉插件只声明资源需求，不管理系统资源。
2. 所有播放型输出和用户打断都走 SDK 仲裁。
3. 多账号、多组织、多设备组场景有基础权限、审计和配置支撑。
4. 长任务可以使用 SQLite 存储在单机多进程场景下恢复和去重。
