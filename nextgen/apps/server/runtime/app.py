"""服务器端运行时应用实现。"""

from dataclasses import dataclass
from typing import Any, Dict
from uuid import uuid4

from nextgen.apps.server.agent.agent_center import AgentCenter
from nextgen.apps.server.gateway.server_gateway import ServerGateway
from nextgen.apps.server.mcp.mcp_registry import ServerMcpRegistry
from nextgen.apps.server.runtime.device_registry import DeviceRegistry
from nextgen.apps.server.runtime.event_router import EventRouter
from nextgen.apps.server.runtime.peer_link_coordinator import PeerLinkCoordinator
from nextgen.apps.server.skills.create_hybrid_task import CreateHybridTaskSkill
from nextgen.apps.server.skills.skills_registry import ServerSkillRegistry
from nextgen.apps.server.storage.state_log_store import StateLogStore
from nextgen.apps.server.task_center.background_task_center import BackgroundTaskCenter
from nextgen.shared.enums.common import LinkStatus, RuntimeType, TaskStatus
from nextgen.shared.models.base import SourceTargetRef
from nextgen.shared.models.control import DeviceHeartbeat, DeviceRegistration, NodeEndpoint
from nextgen.shared.utils.http import post_json

@dataclass
class ServerRuntimeApp:
    """服务器端运行时应用。"""

    name: str = "server-runtime"

    def start(self) -> None:
        """启动服务器端运行时。

        主要逻辑：
        - 当前阶段完成最小模块装配，便于后续扩展真实服务启动逻辑。
        """

        self.gateway = ServerGateway()
        self.event_router = EventRouter()
        self.agent_center = AgentCenter()
        self.background_task_center = BackgroundTaskCenter()
        self.skill_registry = ServerSkillRegistry()
        self.state_log_store = StateLogStore()
        self.device_registry = DeviceRegistry()
        self.peer_link_coordinator = PeerLinkCoordinator()
        self.create_hybrid_task = CreateHybridTaskSkill(
            task_center=self.background_task_center,
            state_log_store=self.state_log_store,
        )
        self.mcp_registry = ServerMcpRegistry()
        self.agent_center.task_center = self.background_task_center

        self.skill_registry.register("create_hybrid_task", self.create_hybrid_task)
        self.event_router.on_keyword_dispatch = self.handle_keyword_dispatch
        self.gateway.attach_runtime(self)

    def stop(self) -> None:
        """停止服务器端运行时。"""

        self.gateway.disconnect()

    def handle_keyword_dispatch(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """处理基于关键词的任务分发。"""

        payload = event.get("payload", {})
        text = payload.get("text", "")
        parsed = self.agent_center.interpret(text)
        if parsed.get("intent") == "create_hybrid_task":
            return self.create_hybrid_task.run(
                task_name=parsed["task_name"],
                params=parsed["params"],
            )
        return {"status": "ignored", "reason": "no_matching_intent"}

    def register_device(self, registration: DeviceRegistration) -> Dict[str, Any]:
        """注册设备。"""

        stored = self.device_registry.register(registration)
        self.state_log_store.append_device_event(
            device_id=registration.device_id,
            event={"event_name": "device_registered", "runtime": registration.runtime.value, "endpoint": registration.endpoint.to_dict()},
        )
        return stored

    def apply_heartbeat(self, heartbeat: DeviceHeartbeat) -> Dict[str, Any]:
        """应用设备心跳。"""

        stored = self.device_registry.heartbeat(heartbeat)
        self.state_log_store.append_device_event(
            device_id=heartbeat.device_id,
            event={"event_name": "device_heartbeat", "status": heartbeat.status, "endpoint": heartbeat.endpoint.to_dict() if heartbeat.endpoint else None},
        )
        return stored

    def prepare_peer_link(self, session_id: str, glass_device_id: str, phone_device_id: str, stream_type: str) -> Dict[str, Any]:
        """为某个任务准备任务级连接。"""

        link_state = self.peer_link_coordinator.create_link(
            session_id=session_id,
            glass_device_id=glass_device_id,
            phone_device_id=phone_device_id,
            stream_type=stream_type,
        )
        self._sync_session_link_status(session_id, link_state.to_dict(), phase="preparing_peer_link")
        self.state_log_store.append_task_event(
            session_id=session_id,
            event={"event_name": "peer_link_prepare_requested", "phone_device_id": phone_device_id, "glass_device_id": glass_device_id, "stream_type": stream_type},
        )
        return {
            "task_session_id": session_id,
            "phone_command": self.peer_link_coordinator.build_phone_prepare_command(session_id),
            "link_state": link_state.to_dict(),
        }

    def mark_peer_link_ready(self, session_id: str, listen_endpoint: NodeEndpoint) -> Dict[str, Any]:
        """记录手机已准备好任务级连接入口。"""

        link_state = self.peer_link_coordinator.mark_phone_ready(session_id=session_id, listen_endpoint=listen_endpoint)
        self._sync_session_link_status(session_id, link_state.to_dict(), phase="peer_link_listening")
        self.state_log_store.append_task_event(
            session_id=session_id,
            event={"event_name": "peer_link_ready", "listen_endpoint": listen_endpoint.to_dict()},
        )
        return {
            "task_session_id": session_id,
            "glass_command": self.peer_link_coordinator.build_glass_connect_command(session_id),
            "link_state": link_state.to_dict(),
        }

    def report_peer_link_status(self, session_id: str, runtime: str, status: LinkStatus, reason: str | None = None) -> Dict[str, Any]:
        """记录任务级连接状态。"""

        link_state = self.peer_link_coordinator.mark_status(
            session_id=session_id,
            runtime=runtime,
            status=status,
            reason=reason,
        )
        phase = "peer_link_connected" if link_state.status == LinkStatus.CONNECTED else f"peer_link_{link_state.status.value}"
        self._sync_session_link_status(session_id, link_state.to_dict(), phase=phase)
        self.state_log_store.append_task_event(
            session_id=session_id,
            event={"event_name": "peer_link_status_reported", "runtime": runtime, "status": status.value, "reason": reason},
        )
        return link_state.to_dict()

    def stop_peer_link(self, session_id: str) -> Dict[str, Any]:
        """结束任务级连接。"""

        link_state = self.peer_link_coordinator.close_link(session_id)
        self._sync_session_link_status(session_id, link_state.to_dict(), phase="peer_link_closed")
        self.state_log_store.append_task_event(
            session_id=session_id,
            event={"event_name": "peer_link_stop_requested"},
        )
        return {
            "task_session_id": session_id,
            "glass_command": self.peer_link_coordinator.build_stop_command(session_id),
            "phone_command": self.peer_link_coordinator.build_stop_command(session_id),
            "link_state": link_state.to_dict(),
        }

    def build_status_snapshot(self) -> Dict[str, Any]:
        """构造当前控制面状态快照。"""

        return {
            "devices": self.device_registry.list_all(),
            "tasks": [item.to_dict() for item in self.background_task_center.list_sessions()],
            "recent_logs": self.state_log_store.get_recent_records(limit=30),
        }

    def create_control_session(
        self,
        task_name: str,
        input_payload: Dict[str, Any],
        glass_device_id: str,
        phone_device_id: str,
    ) -> Dict[str, Any]:
        """创建一个面向真实控制面的任务实例。"""

        session_id = f"tasksess_{uuid4().hex[:12]}"
        session = self.background_task_center.create_runtime_session(
            session_id=session_id,
            task_name=task_name,
            initiator=SourceTargetRef(runtime=RuntimeType.SERVER, device_id="server-main"),
            input_payload={**input_payload, "glass_device_id": glass_device_id, "phone_device_id": phone_device_id},
            participants={"glass": ["capture"], "phone": ["local_task"], "server": ["task_center"]},
        )
        self.state_log_store.append_task_event(
            session_id=session_id,
            event={"event_name": "control_session_created", "task_name": task_name, "glass_device_id": glass_device_id, "phone_device_id": phone_device_id},
        )
        return session.to_dict()

    def orchestrate_peer_link(self, session_id: str, stream_type: str = "image_stream") -> Dict[str, Any]:
        """执行一次完整的任务级连接协调。"""

        session = self.background_task_center.get_session(session_id)
        if session is None:
            raise KeyError(f"任务实例不存在: {session_id}")
        glass_device_id = session.input["glass_device_id"]
        phone_device_id = session.input["phone_device_id"]
        phone_device = self.device_registry.get(phone_device_id)
        glass_device = self.device_registry.get(glass_device_id)
        if phone_device is None or glass_device is None:
            raise KeyError("眼镜或手机设备尚未注册。")

        prepare_response = self.prepare_peer_link(
            session_id=session_id,
            glass_device_id=glass_device_id,
            phone_device_id=phone_device_id,
            stream_type=stream_type,
        )
        phone_command = prepare_response["phone_command"]
        phone_ready = post_json(
            f"{NodeEndpoint(**phone_device['endpoint']).as_base_url()}/task/prepare-peer-link",
            phone_command,
        )
        ready_response = self.mark_peer_link_ready(
            session_id=session_id,
            listen_endpoint=NodeEndpoint(**phone_ready["listen_endpoint"]),
        )
        glass_command = ready_response["glass_command"]
        glass_connected = post_json(
            f"{NodeEndpoint(**glass_device['endpoint']).as_base_url()}/task/connect-peer",
            glass_command,
        )
        self.report_peer_link_status(session_id=session_id, runtime="phone", status=LinkStatus.CONNECTED)
        final_state = self.report_peer_link_status(session_id=session_id, runtime="glass", status=LinkStatus.CONNECTED)
        return {
            "task_session_id": session_id,
            "prepare_response": prepare_response,
            "phone_ready": phone_ready,
            "ready_response": ready_response,
            "glass_connected": glass_connected,
            "link_state": final_state,
        }

    def stop_peer_link_and_notify(self, session_id: str) -> Dict[str, Any]:
        """结束任务级连接并通知两端。"""

        session = self.background_task_center.get_session(session_id)
        if session is None:
            raise KeyError(f"任务实例不存在: {session_id}")
        glass_device = self.device_registry.get(session.input["glass_device_id"])
        phone_device = self.device_registry.get(session.input["phone_device_id"])
        response = self.stop_peer_link(session_id)
        phone_ack = None
        glass_ack = None
        if phone_device is not None:
            phone_ack = post_json(
                f"{NodeEndpoint(**phone_device['endpoint']).as_base_url()}/task/stop-peer-link",
                response["phone_command"],
            )
        if glass_device is not None:
            glass_ack = post_json(
                f"{NodeEndpoint(**glass_device['endpoint']).as_base_url()}/task/stop-peer-link",
                response["glass_command"],
            )
        return {
            **response,
            "phone_ack": phone_ack,
            "glass_ack": glass_ack,
        }

    def apply_task_state(
        self,
        session_id: str,
        runtime: str,
        status: TaskStatus,
        phase: str,
        summary: Dict[str, Any] | None = None,
        result: Dict[str, Any] | None = None,
        error: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """记录任务状态推进。"""

        session = self.background_task_center.transition_session(
            session_id=session_id,
            status=status,
            phase=phase,
            summary=summary,
            result=result,
            error=error,
        )
        self.state_log_store.append_task_event(
            session_id=session_id,
            event={
                "event_name": "task_state_updated",
                "runtime": runtime,
                "status": status.value,
                "phase": phase,
                "summary": summary or {},
            },
        )
        return session.to_dict()

    def record_guidance_executed(
        self,
        session_id: str,
        runtime: str,
        hint_text: str,
        execution_feedback: Dict[str, Any],
        state_summary: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """记录眼镜侧引导执行结果。"""

        self.state_log_store.append_task_event(
            session_id=session_id,
            event={
                "event_name": "guidance_executed",
                "runtime": runtime,
                "hint_text": hint_text,
                "execution_feedback": execution_feedback,
                "state_summary": state_summary or {},
            },
        )
        session = self.background_task_center.get_session(session_id)
        if session is not None:
            session = self.background_task_center.transition_session(
                session_id=session_id,
                status=session.status,
                phase="guidance_executed",
                summary=state_summary or session.last_state_summary,
            )
            return session.to_dict()
        return {}

    def handle_broken_peer_link(
        self,
        session_id: str,
        runtime: str,
        reason: str,
        auto_recover: bool = False,
    ) -> Dict[str, Any]:
        """处理断链上报。"""

        link_state = self.report_peer_link_status(
            session_id=session_id,
            runtime=runtime,
            status=LinkStatus.BROKEN,
            reason=reason,
        )
        recovered = None
        if auto_recover:
            recovered = self.recover_peer_link(session_id)
        return {
            "link_state": link_state,
            "recovered": recovered,
        }

    def recover_peer_link(self, session_id: str) -> Dict[str, Any]:
        """执行一次任务级连接恢复。"""

        session = self.background_task_center.get_session(session_id)
        if session is None:
            raise KeyError(f"任务实例不存在: {session_id}")
        link_state = self.peer_link_coordinator.get(session_id)
        if link_state is None:
            raise KeyError(f"任务级连接不存在: {session_id}")
        self.state_log_store.append_task_event(
            session_id=session_id,
            event={"event_name": "peer_link_recover_requested"},
        )
        return self.orchestrate_peer_link(
            session_id=session_id,
            stream_type=link_state["stream_type"],
        )

    def render_status_page(self) -> str:
        """渲染简单的 Web 状态页面。"""

        snapshot = self.build_status_snapshot()
        device_rows = "".join(
            f"<tr><td>{item['device_id']}</td><td>{item['runtime']}</td><td>{item.get('status','')}</td><td>{item['endpoint']['host']}:{item['endpoint']['port']}</td><td>{item.get('last_seen_at','')}</td></tr>"
            for item in snapshot["devices"]
        ) or "<tr><td colspan='5'>暂无设备</td></tr>"
        task_rows = "".join(
            f"<tr><td>{item['session_id']}</td><td>{item['task_name']}</td><td>{item['status']}</td><td>{item['phase']}</td><td>{(item.get('link_status') or {}).get('status','')}</td></tr>"
            for item in snapshot["tasks"]
        ) or "<tr><td colspan='5'>暂无任务</td></tr>"
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>nextgen 状态页</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; background: #f8fafc; color: #111827; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 24px; }}
    th, td {{ border: 1px solid #d1d5db; padding: 8px; text-align: left; }}
    th {{ background: #e5e7eb; }}
    pre {{ background: #111827; color: #f9fafb; padding: 12px; border-radius: 8px; overflow: auto; }}
  </style>
</head>
<body>
  <h1>nextgen 控制面状态</h1>
  <h2>设备</h2>
  <table>
    <thead><tr><th>device_id</th><th>runtime</th><th>status</th><th>endpoint</th><th>last_seen_at</th></tr></thead>
    <tbody>{device_rows}</tbody>
  </table>
  <h2>任务</h2>
  <table>
    <thead><tr><th>session_id</th><th>task_name</th><th>status</th><th>phase</th><th>link_status</th></tr></thead>
    <tbody>{task_rows}</tbody>
  </table>
  <h2>最近日志</h2>
  <pre>{snapshot['recent_logs']}</pre>
</body>
</html>"""

    def _sync_session_link_status(self, session_id: str, link_status: Dict[str, Any], phase: str) -> None:
        session = self.background_task_center.get_session(session_id)
        if session is None:
            return
        target_status = session.status
        if link_status["status"] == LinkStatus.BROKEN.value:
            target_status = TaskStatus.PAUSED
        elif link_status["status"] == LinkStatus.FAILED.value:
            target_status = TaskStatus.FAILED
        elif link_status["status"] == LinkStatus.CONNECTED.value and session.status in {TaskStatus.CREATED, TaskStatus.PAUSED}:
            target_status = TaskStatus.RUNNING
        self.background_task_center.transition_session(
            session_id=session_id,
            status=target_status,
            phase=phase,
            summary=session.last_state_summary,
        )
        updated = self.background_task_center.get_session(session_id)
        if updated is not None:
            updated.link_status = link_status
            self.background_task_center.update_session(updated)
