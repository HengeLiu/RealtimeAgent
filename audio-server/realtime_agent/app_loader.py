from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path

from realtime_agent.app import RealtimeAgentConfig


@dataclass(frozen=True)
class AppLaunch:
    app_name: str
    app_dir: Path
    config_path: Path
    capabilities_dir: Path | None = None


def resolve_app_launch(app_name: str, *, app_root: str | Path = "examples") -> AppLaunch:
    """Resolve an app-name into its app directory and server config."""

    normalized = str(app_name or "").strip()
    if not normalized:
        raise ValueError("app_name is required")
    if "/" in normalized or "\\" in normalized or normalized in {".", ".."}:
        raise ValueError(f"invalid app_name: {app_name}")
    root = _resolve_app_root(app_root)
    app_dir = (root / normalized / "audio-server").resolve()
    if not app_dir.is_dir():
        raise FileNotFoundError(f"app directory not found: {app_dir}")
    config_path = _resolve_app_config(app_dir)
    capabilities_dir = app_dir / "capabilities"
    return AppLaunch(
        app_name=normalized,
        app_dir=app_dir,
        config_path=config_path,
        capabilities_dir=capabilities_dir if capabilities_dir.is_dir() else None,
    )


def prepare_app_imports(app_dir: str | Path) -> None:
    """Put the app directory on sys.path so `capabilities` can be imported."""

    for name in list(sys.modules):
        if name == "capabilities" or name.startswith("capabilities."):
            sys.modules.pop(name, None)
    path = str(Path(app_dir).resolve())
    if path not in sys.path:
        sys.path.insert(0, path)


def apply_app_launch(config: RealtimeAgentConfig, launch: AppLaunch) -> RealtimeAgentConfig:
    """Attach app metadata and default capabilities discovery to config."""

    updates = {
        "app_name": launch.app_name,
        "app_dir": str(launch.app_dir),
        "config_path": str(launch.config_path),
    }
    if launch.capabilities_dir is not None:
        packages = ("capabilities",)
        updates.update(
            {
                "tools_discover_enabled": True,
                "tools_discover_packages": packages,
                "tools_discover_recursive": True,
                "tasks_discover_enabled": True,
                "tasks_discover_packages": packages,
                "tasks_discover_recursive": True,
            }
        )
    return replace(config, **updates)


def load_app_config(app_name: str, *, app_root: str | Path = "examples") -> tuple[RealtimeAgentConfig, AppLaunch]:
    launch = resolve_app_launch(app_name, app_root=app_root)
    prepare_app_imports(launch.app_dir)
    config = RealtimeAgentConfig.from_yaml(launch.config_path)
    return apply_app_launch(config, launch), launch


def load_config_as_app(config_path: str | Path) -> tuple[RealtimeAgentConfig, AppLaunch]:
    """按根目录 `server.yaml` 加载 app 配置。

    主要逻辑：把 `server.yaml` 的父目录视为 app 根目录；当 YAML 没有配置 app_name
    时使用父目录名；同时准备 `capabilities` 自动发现元数据。
    参数：`config_path` 为 app 根目录下的 `server.yaml`。
    返回值：补齐 app 元数据后的配置和启动描述。
    异常情况：文件名不是 `server.yaml` 或路径位于 `config/` 下时抛出 ValueError。
    """

    path = _resolve_server_yaml_path(config_path)
    if path.name != "server.yaml":
        raise ValueError(f"app config file must be named server.yaml: {path}")
    if path.parent.name == "config":
        raise ValueError(f"server.yaml must be placed at app root, not under config/: {path}")
    app_dir = path.parent.resolve()
    prepare_app_imports(app_dir)
    config = RealtimeAgentConfig.from_yaml(path)
    app_name = config.app_name or (app_dir.parent.name if app_dir.name == "audio-server" else app_dir.name)
    capabilities_dir = app_dir / "capabilities"
    launch = AppLaunch(
        app_name=app_name,
        app_dir=app_dir,
        config_path=path,
        capabilities_dir=capabilities_dir if capabilities_dir.is_dir() else None,
    )
    return apply_app_launch(config, launch), launch


def _resolve_app_root(app_root: str | Path) -> Path:
    raw = Path(app_root)
    if raw.is_dir():
        return raw.resolve()
    prefixed = Path("realtime-agent") / raw
    if prefixed.is_dir():
        return prefixed.resolve()
    return raw.resolve()


def _resolve_app_config(app_dir: Path) -> Path:
    config_path = app_dir / "server.yaml"
    if config_path.is_file():
        return config_path
    raise FileNotFoundError(f"server.yaml not found under app directory: {app_dir}")


def _resolve_server_yaml_path(path: str | Path) -> Path:
    raw = Path(path)
    if raw.is_file():
        return raw.resolve()
    if raw.parts and raw.parts[0] == "realtime-agent":
        trimmed = Path(*raw.parts[1:])
        if trimmed.is_file():
            return trimmed.resolve()
    prefixed = Path("realtime-agent") / raw
    if prefixed.is_file():
        return prefixed.resolve()
    return raw.resolve()
