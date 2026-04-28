"""账号治理、权限审计和远程配置运行时。"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


def _now_ms() -> int:
    """返回当前毫秒时间戳。"""

    return int(time.time() * 1000)


def _new_id(prefix: str) -> str:
    """生成短编号。"""

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass(slots=True)
class OrganizationNode:
    """组织树节点。

    主要功能：
    1. 表示账号所属的组织单元。
    2. 支持后续把权限和远程配置按组织范围下发。
    """

    node_id: str
    name: str
    parent_id: str | None = None
    account_ids: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """导出可序列化结构。"""

        return {
            "node_id": self.node_id,
            "name": self.name,
            "parent_id": self.parent_id,
            "account_ids": sorted(self.account_ids),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class RoleBinding:
    """角色绑定。

    主要功能：
    1. 把用户、设备或服务账号绑定到某个作用域。
    2. 为权限检查提供最小 RBAC 数据。
    """

    binding_id: str
    subject_id: str
    subject_type: str
    role: str
    scope_type: str
    scope_id: str
    created_at_ms: int = field(default_factory=_now_ms)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """导出可序列化结构。"""

        return {
            "binding_id": self.binding_id,
            "subject_id": self.subject_id,
            "subject_type": self.subject_type,
            "role": self.role,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "created_at_ms": self.created_at_ms,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class PermissionDecision:
    """权限检查结果。"""

    allowed: bool
    reason: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    account_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """导出可序列化结构。"""

        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "actor_id": self.actor_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "account_id": self.account_id,
        }


@dataclass(slots=True)
class AuditEvent:
    """审计事件。"""

    event_id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    decision: str
    reason: str
    account_id: str | None = None
    created_at_ms: int = field(default_factory=_now_ms)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """导出可序列化结构。"""

        return {
            "event_id": self.event_id,
            "actor_id": self.actor_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "decision": self.decision,
            "reason": self.reason,
            "account_id": self.account_id,
            "created_at_ms": self.created_at_ms,
            "metadata": dict(self.metadata),
        }


class AuditSink(Protocol):
    """审计输出接口。"""

    def record(self, event: AuditEvent) -> None:
        """记录一条审计事件。"""


class MemoryAuditSink:
    """内存审计输出。

    主要功能：
    1. 保留最近审计事件，便于单测和运行态快照观察。
    2. 避免本地开发必须配置外部审计服务。
    """

    def __init__(self, *, max_events: int = 512) -> None:
        self._max_events = max_events
        self._events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        """记录审计事件。"""

        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events :]

    def list_events(self) -> list[AuditEvent]:
        """列出当前保留的审计事件。"""

        return list(self._events)


class FileAuditSink:
    """JSONL 文件审计输出。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def record(self, event: AuditEvent) -> None:
        """追加写入一条审计事件。"""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")


class ConfigProvider(Protocol):
    """远程配置 Provider 接口。"""

    def get_value(
        self,
        key: str,
        *,
        account_id: str | None = None,
        group_id: str | None = None,
        device_id: str | None = None,
        default: Any = None,
    ) -> Any:
        """读取配置值。"""

    def snapshot(self) -> dict[str, Any]:
        """导出配置 Provider 快照。"""


class MemoryConfigProvider:
    """内存配置 Provider。

    主要功能：
    1. 支持全局、账号、设备组和设备四级配置。
    2. 为单测、本地开发和后续 HTTP Provider 提供一致行为。
    """

    def __init__(self, *, version: str = "memory-v1") -> None:
        self.version = version
        self._values: dict[str, dict[str, Any]] = {"global": {}}

    def set_value(self, key: str, value: Any, *, scope_type: str = "global", scope_id: str = "global") -> None:
        """写入配置值。"""

        scope_key = self._scope_key(scope_type=scope_type, scope_id=scope_id)
        self._values.setdefault(scope_key, {})[key] = value

    def get_value(
        self,
        key: str,
        *,
        account_id: str | None = None,
        group_id: str | None = None,
        device_id: str | None = None,
        default: Any = None,
    ) -> Any:
        """按设备、设备组、账号、全局顺序读取配置。"""

        for scope_key in self._candidate_scopes(
            account_id=account_id,
            group_id=group_id,
            device_id=device_id,
        ):
            scoped = self._values.get(scope_key, {})
            if key in scoped:
                return scoped[key]
        return default

    def snapshot(self) -> dict[str, Any]:
        """导出配置快照。"""

        return {
            "provider": self.__class__.__name__,
            "version": self.version,
            "scopes": {scope: dict(values) for scope, values in sorted(self._values.items())},
        }

    @staticmethod
    def _scope_key(*, scope_type: str, scope_id: str) -> str:
        """生成配置作用域键。"""

        if scope_type == "global":
            return "global"
        return f"{scope_type}:{scope_id}"

    def _candidate_scopes(
        self,
        *,
        account_id: str | None,
        group_id: str | None,
        device_id: str | None,
    ) -> list[str]:
        """返回配置读取优先级。"""

        scopes = []
        if device_id:
            scopes.append(self._scope_key(scope_type="device", scope_id=device_id))
        if group_id:
            scopes.append(self._scope_key(scope_type="group", scope_id=group_id))
        if account_id:
            scopes.append(self._scope_key(scope_type="account", scope_id=account_id))
        scopes.append("global")
        return scopes


class FileConfigProvider(MemoryConfigProvider):
    """JSON 文件配置 Provider。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        super().__init__(version="file-v1")
        self.reload()

    def reload(self) -> None:
        """重新读取配置文件。"""

        payload = json.loads(self._path.read_text(encoding="utf-8"))
        self.version = str(payload.get("version") or self._path.name)
        values = payload.get("values")
        if not isinstance(values, dict):
            values = {}
        self._values = {
            str(scope): dict(raw_values)
            for scope, raw_values in values.items()
            if isinstance(raw_values, dict)
        }
        self._values.setdefault("global", {})

    def snapshot(self) -> dict[str, Any]:
        """导出配置快照。"""

        result = super().snapshot()
        result["path"] = str(self._path)
        return result


class PermissionPolicy:
    """最小 RBAC 权限策略。"""

    ROLE_ACTIONS = {
        "owner": {"*"},
        "admin": {
            "device.register",
            "device.bind",
            "task.create",
            "task.cancel",
            "tool.invoke",
            "config.read",
            "config.write",
            "audit.read",
        },
        "developer": {
            "task.create",
            "task.cancel",
            "tool.invoke",
            "config.read",
        },
        "viewer": {
            "config.read",
            "audit.read",
        },
        "device": {
            "device.register",
            "task.create",
            "config.read",
        },
    }

    def decide(
        self,
        *,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        account_id: str | None,
        bindings: list[RoleBinding],
    ) -> PermissionDecision:
        """执行权限判断。"""

        if not actor_id:
            return PermissionDecision(
                allowed=False,
                reason="missing_actor",
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                account_id=account_id,
            )

        for binding in bindings:
            allowed_actions = self.ROLE_ACTIONS.get(binding.role, set())
            if "*" in allowed_actions or action in allowed_actions:
                return PermissionDecision(
                    allowed=True,
                    reason=f"role:{binding.role}",
                    actor_id=actor_id,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    account_id=account_id,
                )

        return PermissionDecision(
            allowed=False,
            reason="no_matching_role",
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            account_id=account_id,
        )


@dataclass(slots=True)
class AccountGovernanceRuntime:
    """账号治理运行时。

    主要功能：
    1. 维护组织树、角色绑定和最小权限策略。
    2. 提供内存与文件审计输出。
    3. 统一远程配置读取入口。
    """

    permission_policy: PermissionPolicy = field(default_factory=PermissionPolicy)
    config_provider: ConfigProvider = field(default_factory=MemoryConfigProvider)
    audit_sink: AuditSink = field(default_factory=MemoryAuditSink)
    _organization_nodes: dict[str, OrganizationNode] = field(default_factory=dict)
    _account_to_org: dict[str, str] = field(default_factory=dict)
    _role_bindings: dict[str, RoleBinding] = field(default_factory=dict)

    def register_account(self, *, account_id: str, user_id: str | None = None) -> None:
        """注册账号，并为用户补默认 owner 角色。"""

        if user_id:
            self.bind_role(
                subject_id=user_id,
                role="owner",
                scope_type="account",
                scope_id=account_id,
                subject_type="user",
                metadata={"auto": True},
            )

    def create_organization_node(
        self,
        *,
        node_id: str,
        name: str,
        parent_id: str | None = None,
        account_ids: set[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrganizationNode:
        """创建或更新组织节点。"""

        if parent_id and parent_id not in self._organization_nodes:
            raise ValueError(f"父组织节点不存在: {parent_id}")
        node = OrganizationNode(
            node_id=node_id,
            name=name,
            parent_id=parent_id,
            account_ids=set(account_ids or set()),
            metadata=dict(metadata or {}),
        )
        self._organization_nodes[node_id] = node
        for account_id in node.account_ids:
            self._account_to_org[account_id] = node_id
        return node

    def bind_role(
        self,
        *,
        subject_id: str,
        role: str,
        scope_type: str,
        scope_id: str,
        subject_type: str = "user",
        metadata: dict[str, Any] | None = None,
    ) -> RoleBinding:
        """绑定角色。"""

        binding = RoleBinding(
            binding_id=_new_id("role"),
            subject_id=subject_id,
            subject_type=subject_type,
            role=role,
            scope_type=scope_type,
            scope_id=scope_id,
            metadata=dict(metadata or {}),
        )
        self._role_bindings[binding.binding_id] = binding
        return binding

    def authorize(
        self,
        *,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        account_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PermissionDecision:
        """执行权限判断并写入审计。"""

        bindings = self._matching_bindings(actor_id=actor_id, account_id=account_id, resource_id=resource_id)
        decision = self.permission_policy.decide(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            account_id=account_id,
            bindings=bindings,
        )
        self.record_audit(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            allowed=decision.allowed,
            reason=decision.reason,
            account_id=account_id,
            metadata=metadata or {},
        )
        return decision

    def require_permission(self, **kwargs: Any) -> PermissionDecision:
        """要求权限通过，否则抛出异常。"""

        decision = self.authorize(**kwargs)
        if not decision.allowed:
            raise PermissionError(f"权限拒绝: action={decision.action} reason={decision.reason}")
        return decision

    def get_config(
        self,
        key: str,
        *,
        account_id: str | None = None,
        group_id: str | None = None,
        device_id: str | None = None,
        default: Any = None,
    ) -> Any:
        """读取远程配置值。"""

        return self.config_provider.get_value(
            key,
            account_id=account_id,
            group_id=group_id,
            device_id=device_id,
            default=default,
        )

    def record_audit(
        self,
        *,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        allowed: bool,
        reason: str,
        account_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """记录审计事件。"""

        event = AuditEvent(
            event_id=_new_id("audit"),
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            decision="allow" if allowed else "deny",
            reason=reason,
            account_id=account_id,
            metadata=dict(metadata or {}),
        )
        self.audit_sink.record(event)
        return event

    def list_audit_events(self) -> list[AuditEvent]:
        """列出内存审计事件。"""

        if isinstance(self.audit_sink, MemoryAuditSink):
            return self.audit_sink.list_events()
        return []

    def build_snapshot(self) -> dict[str, Any]:
        """构建治理运行态快照。"""

        return {
            "organization_nodes": [
                node.to_dict()
                for node in sorted(self._organization_nodes.values(), key=lambda item: item.node_id)
            ],
            "role_bindings": [
                binding.to_dict()
                for binding in sorted(self._role_bindings.values(), key=lambda item: item.binding_id)
            ],
            "recent_audit_events": [event.to_dict() for event in self.list_audit_events()[-50:]],
            "config": self.config_provider.snapshot(),
        }

    def _matching_bindings(
        self,
        *,
        actor_id: str,
        account_id: str | None,
        resource_id: str,
    ) -> list[RoleBinding]:
        """查找与当前 actor 和作用域匹配的角色绑定。"""

        result: list[RoleBinding] = []
        org_id = self._account_to_org.get(account_id or "")
        for binding in self._role_bindings.values():
            if binding.subject_id != actor_id:
                continue
            if binding.scope_type == "global":
                result.append(binding)
            elif binding.scope_type == "account" and account_id and binding.scope_id == account_id:
                result.append(binding)
            elif binding.scope_type == "organization" and org_id and binding.scope_id == org_id:
                result.append(binding)
            elif binding.scope_type == "resource" and binding.scope_id == resource_id:
                result.append(binding)
        return result
