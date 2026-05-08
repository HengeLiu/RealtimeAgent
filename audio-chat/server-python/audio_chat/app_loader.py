from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path

from audio_chat.app import AudioChatConfig


@dataclass(frozen=True)
class AppLaunch:
    app_name: str
    app_dir: Path
    config_path: Path
    capabilities_dir: Path | None = None


def resolve_app_launch(app_name: str, *, app_root: str | Path = "app-examples") -> AppLaunch:
    """Resolve an app-name into its app directory and server config."""

    normalized = str(app_name or "").strip()
    if not normalized:
        raise ValueError("app_name is required")
    if "/" in normalized or "\\" in normalized or normalized in {".", ".."}:
        raise ValueError(f"invalid app_name: {app_name}")
    root = _resolve_app_root(app_root)
    app_dir = (root / normalized).resolve()
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


def apply_app_launch(config: AudioChatConfig, launch: AppLaunch) -> AudioChatConfig:
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


def load_app_config(app_name: str, *, app_root: str | Path = "app-examples") -> tuple[AudioChatConfig, AppLaunch]:
    launch = resolve_app_launch(app_name, app_root=app_root)
    prepare_app_imports(launch.app_dir)
    config = AudioChatConfig.from_yaml(launch.config_path)
    return apply_app_launch(config, launch), launch


def _resolve_app_root(app_root: str | Path) -> Path:
    raw = Path(app_root)
    if raw.is_dir():
        return raw.resolve()
    prefixed = Path("audio-chat") / raw
    if prefixed.is_dir():
        return prefixed.resolve()
    return raw.resolve()


def _resolve_app_config(app_dir: Path) -> Path:
    candidates = [
        app_dir / "server.yaml",
        app_dir / "config" / "server.yaml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"server.yaml not found under app directory: {app_dir}")
