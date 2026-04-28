# iteration-v10：任务持久化生产化

对应 SDK 版本：sdk-v11

## 背景

旧版 SDK 任务运行时已经能导出、保存和恢复 JSON 快照，但保存动作需要宿主主动调用，也缺少原子写入、事件幂等和终态任务清理。功能开发进入长任务和回放阶段后，需要更可靠的单机持久化能力。

## 本轮改动

1. 新增 `FileTaskPersistenceStore`。
2. `TaskRuntimeManager` 支持可选持久化存储。
3. 新增 `enable_persistence(path, restore=True)`。
4. 创建、取消、事件派发、恢复和清理后自动保存。
5. 文件保存使用临时文件加原子替换。
6. `dispatch_event` 支持 `event_id`，并识别 payload 中的 `event_id/idempotency_key`。
7. 新增 `prune_tasks(retain_terminal_ms=...)`。
8. 更新开发指南、支持情况说明和结构设计文档。

## 验收

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q
```

覆盖点：

1. 自动持久化文件包含版本、保存时间和任务列表。
2. 相同 `event_id` 的外部事件只处理一次。
3. 终态任务清理会同步更新持久化文件。

## 后续边界

sdk-v11 是单机生产化，不是数据库级分布式任务平台。多实例抢占、分布式锁、数据库事务和事件游标后续专项处理。
