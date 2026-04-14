"""agent-core 上下文与运行时对象模型。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal


def now_ms() -> int:
    """返回当前毫秒时间戳。

    返回值：
    1. 当前 Unix 毫秒时间戳。
    """

    return int(time.time() * 1000)


def generate_id(prefix: str) -> str:
    """生成统一前缀标识。

    参数：
    1. `prefix`：标识前缀，例如 `msg`、`turn`、`asset`。

    返回值：
    1. 形如 `prefix_xxx` 的唯一标识。
    """

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass(slots=True)
class MediaAssetRef:
    """媒体资产引用。

    主要功能：
    1. 描述当前会话中的原始媒体资产。
    2. 为语音、图片、视频等后续扩展保留统一结构。

    主要属性：
    1. `asset_id`：媒体资产唯一编号。
    2. `session_id`：所属会话编号。
    3. `asset_type`：媒体类型，例如 `audio` 或 `image`。
    4. `storage_uri`：资产落盘路径或对象存储路径。
    5. `mime_type`：媒体 MIME 类型。
    6. `codec`：编码格式。
    7. `duration_ms/bytes/width/height/fps`：按需媒体元数据。
    8. `source_stream_id`：来源数据流编号。
    """

    asset_id: str
    session_id: str
    asset_type: str
    storage_uri: str
    mime_type: str
    codec: str | None = None
    duration_ms: int | None = None
    bytes: int | None = None
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    source_stream_id: str | None = None
    created_at_ms: int = field(default_factory=now_ms)


@dataclass(slots=True)
class DerivedArtifact:
    """派生结果引用。

    主要功能：
    1. 记录从原始媒体或外部能力计算出来的结构化结果。
    2. 为 ASR 转写、图片摘要、地图查询等结果保留统一入口。

    主要属性：
    1. `artifact_id`：派生结果唯一编号。
    2. `session_id`：所属会话编号。
    3. `artifact_type`：结果类型，例如 `asr_transcript`。
    4. `storage_uri`：结果落盘位置。
    5. `text`：结果文本摘要。
    6. `meta`：结构化补充信息。
    """

    artifact_id: str
    session_id: str
    artifact_type: str
    storage_uri: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)
    created_at_ms: int = field(default_factory=now_ms)


@dataclass(slots=True)
class TaskRef:
    """任务引用。

    主要功能：
    1. 把会话消息与后台任务实例关联起来。
    2. 为 Phase F 任务回流保留标准结构。
    """

    task_id: str
    task_type: str
    state: str
    summary: str
    created_at_ms: int = field(default_factory=now_ms)


@dataclass(slots=True)
class CapabilityTrace:
    """能力调用轨迹。

    主要功能：
    1. 记录单轮 turn 中的 Tool / Skill / MCP / Task 调用。
    2. 提供后续排障、审计和联调观察依据。

    主要属性：
    1. `capability_type`：能力类型，例如 `tool`。
    2. `capability_name`：能力名称。
    3. `status`：调用状态，例如 `running/succeeded/failed`。
    4. `input_summary/output_summary/error_message`：调用摘要。
    """

    trace_id: str
    turn_id: str
    capability_type: str
    capability_name: str
    status: str
    input_summary: str
    output_summary: str = ""
    error_message: str = ""
    started_at_ms: int = field(default_factory=now_ms)
    completed_at_ms: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MessageContext:
    """会话消息上下文。

    主要功能：
    1. 保存会话中的消息顺序。
    2. 挂接资产引用、派生结果和任务引用。

    主要属性：
    1. `role`：消息角色，例如 `user` 或 `assistant`。
    2. `kind`：消息种类，例如 `audio_input` 或 `assistant_reply`。
    3. `text`：消息文本主体。
    4. `asset_refs/derived_refs/task_refs`：关联引用编号列表。
    """

    message_id: str
    session_id: str
    role: str
    kind: str
    text: str
    asset_refs: list[str] = field(default_factory=list)
    derived_refs: list[str] = field(default_factory=list)
    task_refs: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    created_at_ms: int = field(default_factory=now_ms)


@dataclass(slots=True)
class DialogState:
    """最小对话状态。

    主要功能：
    1. 保存当前待追问信息。
    2. 为后续参数补齐和澄清流程保留状态位。
    """

    pending_question: str | None = None
    missing_slots: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentSession:
    """开放式会话对象。

    主要功能：
    1. 承载当前会话的消息、资产、派生结果和调用轨迹。
    2. 作为 `agent-core` 与 `voice-runtime` 的共享会话容器。
    """

    session_id: str
    device_id: str
    messages: list[MessageContext] = field(default_factory=list)
    assets: dict[str, MediaAssetRef] = field(default_factory=dict)
    artifacts: dict[str, DerivedArtifact] = field(default_factory=dict)
    tasks: dict[str, TaskRef] = field(default_factory=dict)
    capability_traces: list[CapabilityTrace] = field(default_factory=list)
    dialog_state: DialogState = field(default_factory=DialogState)
    created_at_ms: int = field(default_factory=now_ms)
    updated_at_ms: int = field(default_factory=now_ms)


@dataclass(slots=True)
class AgentTurn:
    """单轮输入对象。

    主要功能：
    1. 作为 `voice-runtime -> agent-core` 的统一输入对象。
    2. 承载当前轮文本、资产和派生结果引用。

    主要属性：
    1. `turn_id`：本轮输入唯一编号。
    2. `source`：输入来源，例如 `voice_asr`。
    3. `input_text`：当前轮用户文本。
    4. `asset_refs`：当前轮原始媒体引用。
    5. `derived_artifacts`：当前轮派生结果引用。
    """

    turn_id: str
    session_id: str
    device_id: str
    source: str
    input_text: str
    asset_refs: list[MediaAssetRef] = field(default_factory=list)
    derived_artifacts: list[DerivedArtifact] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentTurnResult:
    """单轮输出对象。

    主要功能：
    1. 作为 `agent-core -> voice-runtime` 的统一输出对象。
    2. 表达本轮最终回复、追问或失败结果。

    主要属性：
    1. `action`：当前最终动作，支持 `final_answer/ask_user/fail`。
    2. `reply_text`：需要交给语音播报的最终文本。
    3. `assistant_message_id`：当前轮生成的助手消息编号。
    4. `capability_traces`：本轮能力调用轨迹。
    """

    turn_id: str
    session_id: str
    device_id: str
    action: Literal["final_answer", "ask_user", "fail"]
    reply_text: str
    assistant_message_id: str | None = None
    capability_traces: list[CapabilityTrace] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
