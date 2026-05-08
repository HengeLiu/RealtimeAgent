"""`phone-mock` 配置解析与校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


@dataclass(slots=True)
class PhoneMockEvent:
    """手机 mock 任务事件配置。

    主要功能：
    1. 描述收到服务端手机任务启动命令后要上报的事件。
    2. 支持用 `delay_ms` 模拟手机端处理耗时。

    主要属性：
    1. `event_name`：上报给服务端任务运行时的事件名。
    2. `payload`：事件载荷。
    3. `delay_ms`：发送前等待时间。
    """

    event_name: str
    payload: dict[str, Any] = field(default_factory=dict)
    delay_ms: int = 0


@dataclass(slots=True)
class PhoneMockTaskHandler:
    """手机 mock 任务处理器配置。"""

    task_type: str
    events: list[PhoneMockEvent] = field(default_factory=list)
    task_class: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PhoneMockProcessorPlugin:
    """手机 mock 处理器插件配置。"""

    processor_type: str
    processor_class: str


@dataclass(slots=True)
class CameraSinkConfig:
    """手机 mock 相机流接收服务配置。"""

    enabled: bool = True
    bind_host: str = "0.0.0.0"
    public_host: str = ""
    port: int = 0
    path: str = "/ws/camera"
    save_dir: Path | None = None


@dataclass(slots=True)
class PhoneMockOutputConfig:
    """手机 mock 输出文件配置。"""

    event_log: Path


@dataclass(slots=True)
class PhoneMockConfig:
    """`phone-mock` 设备配置。

    主要功能：
    1. 固定 `device_type=phone`，按真实手机设备协议接入服务端。
    2. 通过任务处理器配置把手机侧 Python 测试代码收敛到独立虚拟设备内。
    """

    config_path: Path
    device_id: str
    pair_token: str
    control_ws_url: str
    camera_sink_ws_uri: str
    camera_sink: CameraSinkConfig
    heartbeat_interval_seconds: float
    task_handlers: dict[str, PhoneMockTaskHandler]
    processor_plugins: dict[str, PhoneMockProcessorPlugin] = field(default_factory=dict)
    outputs: PhoneMockOutputConfig | None = None

    @classmethod
    def load(cls, path: str | Path, *, repo_root: str | Path = ".") -> "PhoneMockConfig":
        """从 JSON 文件加载 `phone-mock` 配置。

        参数：
        1. `path`：配置文件路径。
        2. `repo_root`：仓库根目录，用于解析相对输出路径。

        返回值：
        1. 解析完成的 `PhoneMockConfig`。

        异常情况：
        1. 配置不是 JSON object、缺少设备编号、配对令牌或控制地址时抛出异常。
        """

        config_path = Path(path).resolve()
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("phone-mock 配置必须是 JSON object")

        device_type = str(raw.get("device_type") or "phone").strip()
        if device_type != "phone":
            raise ValueError("phone-mock 仅支持 device_type=phone")

        device_id = str(raw.get("device_id") or "").strip()
        pair_token = str(raw.get("pair_token") or "").strip()
        control_ws_url = str(raw.get("control_ws_url") or "").strip()
        if not device_id:
            raise ValueError("phone-mock 配置缺少 device_id")
        if not pair_token:
            raise ValueError("phone-mock 配置缺少 pair_token")
        if not control_ws_url:
            raise ValueError("phone-mock 配置缺少 control_ws_url")
        _validate_ws_url(control_ws_url, field_name="control_ws_url")

        return cls(
            config_path=config_path,
            device_id=device_id,
            pair_token=pair_token,
            control_ws_url=control_ws_url,
            camera_sink_ws_uri=str(raw.get("camera_sink_ws_uri") or "").strip(),
            camera_sink=_load_camera_sink(raw.get("camera_sink"), config_path=config_path, repo_root=Path(repo_root).resolve(), control_ws_url=control_ws_url, device_id=device_id),
            heartbeat_interval_seconds=float(raw.get("heartbeat_interval_seconds", 3.0)),
            task_handlers=_load_task_handlers(raw.get("task_handlers")),
            processor_plugins=_load_processor_plugins(raw.get("processor_plugins")),
            outputs=_load_outputs(raw.get("outputs"), config_path=config_path, repo_root=Path(repo_root).resolve(), device_id=device_id),
        )


def _load_task_handlers(raw: object) -> dict[str, PhoneMockTaskHandler]:
    """解析任务处理器配置。"""

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("phone-mock 配置 task_handlers 必须是 JSON object")
    handlers: dict[str, PhoneMockTaskHandler] = {}
    for task_type, handler_raw in raw.items():
        normalized_task_type = str(task_type or "").strip()
        if not normalized_task_type:
            raise ValueError("phone-mock task_handlers 存在空任务类型")
        data = handler_raw if isinstance(handler_raw, dict) else {}
        events = [_load_event(item) for item in data.get("events", []) if isinstance(item, dict)]
        params = data.get("params")
        if not isinstance(params, dict):
            params = {}
        handlers[normalized_task_type] = PhoneMockTaskHandler(
            task_type=normalized_task_type,
            events=events,
            task_class=str(data.get("task_class") or "").strip(),
            params=dict(params),
        )
    return handlers


def _load_processor_plugins(raw: object) -> dict[str, PhoneMockProcessorPlugin]:
    """解析手机 mock 处理器插件配置。"""

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("phone-mock 配置 processor_plugins 必须是 JSON object")
    plugins: dict[str, PhoneMockProcessorPlugin] = {}
    for processor_type, plugin_raw in raw.items():
        normalized_processor_type = str(processor_type or "").strip()
        if not normalized_processor_type:
            raise ValueError("phone-mock processor_plugins 存在空处理器类型")
        data = plugin_raw if isinstance(plugin_raw, dict) else {}
        processor_class = str(data.get("processor_class") or data.get("class") or "").strip()
        if not processor_class:
            raise ValueError(f"phone-mock processor_plugins.{normalized_processor_type} 缺少 processor_class")
        plugins[normalized_processor_type] = PhoneMockProcessorPlugin(
            processor_type=normalized_processor_type,
            processor_class=processor_class,
        )
    return plugins


def _load_event(raw: dict[str, Any]) -> PhoneMockEvent:
    """解析单条 mock 事件。"""

    event_name = str(raw.get("event_name") or "").strip()
    if not event_name:
        raise ValueError("phone-mock task_handlers.events 缺少 event_name")
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    return PhoneMockEvent(
        event_name=event_name,
        payload=dict(payload),
        delay_ms=int(raw.get("delay_ms", 0) or 0),
    )


def _load_outputs(
    raw: object,
    *,
    config_path: Path,
    repo_root: Path,
    device_id: str,
) -> PhoneMockOutputConfig | None:
    """解析输出文件配置。"""

    if raw is None:
        return None
    data = raw if isinstance(raw, dict) else {}
    default_path = repo_root / "openaiglass-for-blind/runs/phone-mock" / device_id / "events.jsonl"
    event_log = _resolve_output_path(data.get("event_log") or default_path, config_path=config_path, repo_root=repo_root)
    return PhoneMockOutputConfig(event_log=event_log)


def _load_camera_sink(
    raw: object,
    *,
    config_path: Path,
    repo_root: Path,
    control_ws_url: str,
    device_id: str,
) -> CameraSinkConfig:
    """解析相机流接收服务配置。"""

    data = raw if isinstance(raw, dict) else {}
    save_dir_value = data.get("save_dir") or repo_root / "openaiglass-for-blind/runs/phone-mock" / device_id / "camera"
    return CameraSinkConfig(
        enabled=bool(data.get("enabled", True)),
        bind_host=str(data.get("bind_host") or "0.0.0.0"),
        public_host=str(data.get("public_host") or _derive_public_host(control_ws_url)),
        port=int(data.get("port", 0) or 0),
        path=str(data.get("path") or "/ws/camera"),
        save_dir=_resolve_output_path(save_dir_value, config_path=config_path, repo_root=repo_root),
    )


def _resolve_output_path(value: object, *, config_path: Path, repo_root: Path) -> Path:
    raw_path = str(value or "").strip()
    if not raw_path:
        raise ValueError("phone-mock outputs.event_log 不能为空")
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (repo_root / path).resolve() if not str(path).startswith(".") else (config_path.parent / path).resolve()


def _validate_ws_url(value: str, *, field_name: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        raise ValueError(f"phone-mock 配置 {field_name} 不是有效 WebSocket URL: {value}")


def _derive_public_host(control_ws_url: str) -> str:
    """从服务端控制地址推导当前开发机对外地址。"""

    parsed = urlsplit(control_ws_url)
    return parsed.hostname or "127.0.0.1"


def derive_http_base_url(control_ws_url: str) -> str:
    """从控制 WebSocket 地址推导 HTTP API 根地址。"""

    parsed = urlsplit(control_ws_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    return urlunsplit((scheme, parsed.netloc, "", "", ""))
