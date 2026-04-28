# iteration-v15：账号治理和远程配置 Provider

## 本轮目标

补齐账号权限、组织管理、审计事件和远程配置中心第一版，让 SDK 在已有账号级设备索引之外，具备可授权、可审计、可配置的基础设施。

## 主要改动

1. 新增 `AccountGovernanceRuntime`，统一承载组织树、角色绑定、权限策略、审计事件和配置 Provider。
2. 新增 `OrganizationNode`、`RoleBinding`、`PermissionPolicy`、`AuditEvent` 等账号治理模型。
3. 新增 `MemoryAuditSink`、`FileAuditSink`，支持内存和 JSONL 文件审计输出。
4. 新增 `MemoryConfigProvider`、`FileConfigProvider`，支持 global、account、group、device 四级配置读取。
5. `DeviceGroupRuntime` 接入治理运行时：注册和绑定会记录审计，跨账号绑定 deny 也会进入审计事件。
6. `DeviceGroupContext` 新增 `get_config(...)` 和 `require_permission(...)`，业务代码可以通过 SDK 入口读取策略配置和执行权限检查。

## 当前边界

1. 本轮不实现商业后台 UI、外部用户中心、SSO、OAuth 或云端配置服务。
2. 默认权限检查以显式 `authorize(...)` / `require_permission(...)` 为主，后续再逐步接入更多 Tool、Task 自动检查点。
3. 文件配置 Provider 是单机本地文件形态，不是多实例配置推送系统。
4. SQLite 任务持久化仍是下一项优先工作。

## 验证结果

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q
python -m compileall -q openaiglass-sdk/server-python
```
