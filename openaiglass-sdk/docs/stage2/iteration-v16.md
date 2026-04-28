# iteration-v16：SQLite 任务持久化

## 本轮目标

把 SDK 托管任务从 JSON 文件快照升级到 SQLite 轻量数据库形态，支持任务快照恢复、事件幂等和单机多进程任务租约。

## 主要改动

1. 新增 `SQLiteTaskPersistenceStore`，使用 Python 标准库 `sqlite3`，保持与文件存储一致的 `save/load` 契约。
2. SQLite schema 包含 `schema_migrations`、`tasks`、`task_events`、`task_leases` 四张表。
3. 文件型 SQLite 默认启用 WAL，任务快照和事件写入使用 `BEGIN IMMEDIATE` 事务。
4. `task_events` 使用 `(task_id, event_id)` 主键做事件幂等。
5. `TaskRuntimeManager` 新增 `enable_sqlite_persistence(...)`，支持从 SQLite 恢复任务。
6. `SQLiteTaskPersistenceStore.acquire_lease(...)` / `release_lease(...)` 提供单机多进程租约能力。

## 当前边界

1. SQLite 第一版只保证单机 SQLite 文件内的多进程协调，不保证跨机器强一致。
2. 当前 manager 通过快照保存契约接入 SQLite，后续可进一步细化为增量事件写入。
3. 多服务器部署仍需要外部数据库、恢复协调器和事件消费游标。

## 验证结果

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q
python -m compileall -q openaiglass-sdk/server-python
```
