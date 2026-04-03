"""服务器端任务级连接协调器。"""

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, Optional

from nextgen.shared.enums.common import LinkStatus
from nextgen.shared.models.control import NodeEndpoint, PeerLinkState


class PeerLinkCoordinator:
    """协调眼镜与手机之间的任务级连接。"""

    def __init__(self) -> None:
        self.links: Dict[str, PeerLinkState] = {}

    def create_link(self, session_id: str, glass_device_id: str, phone_device_id: str, stream_type: str) -> PeerLinkState:
        """创建任务级连接记录。"""

        state = PeerLinkState(
            task_session_id=session_id,
            glass_device_id=glass_device_id,
            phone_device_id=phone_device_id,
            stream_type=stream_type,
            status=LinkStatus.PENDING,
        )
        self.links[session_id] = state
        return deepcopy(state)

    def mark_phone_ready(self, session_id: str, listen_endpoint: NodeEndpoint) -> PeerLinkState:
        """标记手机连接入口已准备完成。"""

        state = self._require(session_id)
        state.phone_listen_endpoint = listen_endpoint
        state.phone_status = LinkStatus.LISTENING
        state.status = LinkStatus.LISTENING
        state.updated_at = datetime.now().astimezone().isoformat()
        return deepcopy(state)

    def mark_status(
        self,
        session_id: str,
        runtime: str,
        status: LinkStatus,
        reason: Optional[str] = None,
    ) -> PeerLinkState:
        """记录某一侧上报的连接状态。"""

        state = self._require(session_id)
        if runtime == "glass":
            state.glass_status = status
            if status in {LinkStatus.CONNECTING, LinkStatus.CONNECTED}:
                state.connect_attempt_count += 1
        elif runtime == "phone":
            state.phone_status = status
        else:
            raise ValueError(f"不支持的运行时: {runtime}")

        if status == LinkStatus.CONNECTED and state.glass_status == LinkStatus.CONNECTED and state.phone_status in {
            LinkStatus.CONNECTED,
            LinkStatus.LISTENING,
        }:
            state.status = LinkStatus.CONNECTED
        elif status in {LinkStatus.BROKEN, LinkStatus.FAILED}:
            state.status = status
            state.last_error = reason
        elif status == LinkStatus.CLOSED:
            state.status = LinkStatus.CLOSED
        elif status == LinkStatus.CONNECTING:
            state.status = LinkStatus.CONNECTING
        elif state.status == LinkStatus.PENDING and status == LinkStatus.LISTENING:
            state.status = LinkStatus.LISTENING

        state.updated_at = datetime.now().astimezone().isoformat()
        return deepcopy(state)

    def close_link(self, session_id: str) -> PeerLinkState:
        """关闭任务级连接。"""

        state = self._require(session_id)
        state.status = LinkStatus.CLOSED
        state.glass_status = LinkStatus.CLOSED
        state.phone_status = LinkStatus.CLOSED
        state.updated_at = datetime.now().astimezone().isoformat()
        return deepcopy(state)

    def build_phone_prepare_command(self, session_id: str) -> Dict[str, Any]:
        """构造发送给手机的准备连接命令。"""

        state = self._require(session_id)
        return {
            "task_session_id": state.task_session_id,
            "stream_type": state.stream_type,
            "peer_device_id": state.glass_device_id,
        }

    def build_glass_connect_command(self, session_id: str) -> Dict[str, Any]:
        """构造发送给眼镜的连接命令。"""

        state = self._require(session_id)
        if state.phone_listen_endpoint is None:
            raise ValueError("手机侧监听地址尚未准备完成。")
        return {
            "task_session_id": state.task_session_id,
            "peer_device_id": state.phone_device_id,
            "peer_endpoint": state.phone_listen_endpoint.to_dict(),
            "stream_type": state.stream_type,
        }

    def build_stop_command(self, session_id: str) -> Dict[str, Any]:
        """构造停止连接命令。"""

        state = self._require(session_id)
        return {
            "task_session_id": state.task_session_id,
            "stream_type": state.stream_type,
        }

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取连接状态快照。"""

        state = self.links.get(session_id)
        return deepcopy(state.to_dict()) if state else None

    def _require(self, session_id: str) -> PeerLinkState:
        state = self.links.get(session_id)
        if state is None:
            raise KeyError(f"任务级连接不存在: {session_id}")
        return state
