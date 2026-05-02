"""SDK 命令行通用工具函数。"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping


SECRET_ENV_KEYS = {
    "AMAP_API_KEY",
    "AMAP_MAPS_API_KEY",
    "BOCHA_API_KEY",
    "BOCHA_SEARCH_API_KEY",
    "DASHSCOPE_API_KEY",
}

YAML_ENV_KEY_MAP: dict[tuple[str, ...], str] = {
    ("app", "environment"): "APP_ENV",
    ("server", "host"): "HOST",
    ("server", "port"): "PORT",
    ("server", "public_host"): "SERVER_PUBLIC_HOST",
    ("logging", "level"): "LOG_LEVEL",
    ("logging", "file"): "LOG_FILE",
    ("devices", "server_device_id"): "SERVER_DEVICE_ID",
    ("devices", "glass_device_id"): "GLASS_DEVICE_ID",
    ("devices", "phone_device_id"): "PHONE_DEVICE_ID",
    ("heartbeat", "interval_ms"): "HEARTBEAT_INTERVAL_MS",
    ("heartbeat", "timeout_ms"): "HEARTBEAT_TIMEOUT_MS",
    ("models", "base_url"): "VOICE_MODEL_BASE_URL",
    ("models", "agent", "model"): "AGENT_MODEL_NAME",
    ("models", "voice", "model"): "VOICE_MODEL_NAME",
    ("models", "voice", "voice"): "VOICE_MODEL_VOICE",
    ("models", "omni_realtime", "model"): "VOICE_OMNI_REALTIME_MODEL_NAME",
    ("models", "omni_realtime", "url"): "VOICE_OMNI_REALTIME_URL",
    ("models", "omni_realtime", "photo_wait_ms"): "VOICE_OMNI_PHOTO_WAIT_MS",
    ("models", "asr", "mode"): "VOICE_ASR_MODE",
    ("models", "asr", "batch_model"): "VOICE_ASR_MODEL_NAME",
    ("models", "asr", "realtime", "model"): "VOICE_ASR_REALTIME_MODEL_NAME",
    ("models", "asr", "realtime", "timeout_ms"): "VOICE_ASR_REALTIME_TIMEOUT_MS",
    ("models", "asr", "realtime", "max_sentence_silence_ms"): (
        "VOICE_ASR_REALTIME_MAX_SENTENCE_SILENCE_MS"
    ),
    ("models", "tts", "model"): "TTS_MODEL_NAME",
    ("models", "tts", "voice"): "TTS_VOICE",
    ("models", "tts", "websocket_api_url"): "TTS_WEBSOCKET_API_URL",
    ("models", "tts", "sample_rate_hz"): "TTS_SAMPLE_RATE_HZ",
    ("voice", "session_mode"): "VOICE_SESSION_MODE",
    ("voice", "reply_mode"): "VOICE_REPLY_MODE",
    ("voice", "input_mode"): "VOICE_INPUT_MODE",
    ("voice", "conversation_mode"): "VOICE_CONVERSATION_MODE",
    ("voice", "system_prompt"): "VOICE_SYSTEM_PROMPT",
    ("voice", "max_segment_audio_bytes"): "MAX_SEGMENT_AUDIO_BYTES",
    ("voice", "runs_root"): "VOICE_RUNS_ROOT",
    ("voice", "realtime_turn_detection", "type"): "VOICE_REALTIME_TURN_DETECTION",
    ("voice", "realtime_turn_detection", "semantic_vad_threshold"): (
        "VOICE_REALTIME_SEMANTIC_VAD_THRESHOLD"
    ),
    ("voice", "realtime_turn_detection", "silence_duration_ms"): (
        "VOICE_REALTIME_SILENCE_DURATION_MS"
    ),
    ("voice", "realtime_turn_detection", "prefix_padding_ms"): (
        "VOICE_REALTIME_PREFIX_PADDING_MS"
    ),
    ("tools", "progress_audio", "enabled"): "TOOL_PROGRESS_AUDIO_ENABLED",
    ("tools", "progress_audio", "mode"): "TOOL_PROGRESS_AUDIO_MODE",
    ("agent", "memory", "enabled"): "AGENT_MEMORY_ENABLED",
    ("agent", "memory", "store_path"): "AGENT_MEMORY_STORE_PATH",
    ("agent", "memory", "max_prompt_items"): "AGENT_MEMORY_MAX_PROMPT_ITEMS",
    ("business", "navigation", "amap", "api_key"): "AMAP_API_KEY",
    ("business", "navigation", "amap", "default_city"): "AMAP_DEFAULT_CITY",
    ("business", "navigation", "amap", "default_origin"): "AMAP_DEFAULT_ORIGIN",
    ("business", "navigation", "amap", "disable_mock_fallback"): "AMAP_DISABLE_MOCK_FALLBACK",
    ("business", "navigation", "amap", "http_timeout_seconds"): "AMAP_HTTP_TIMEOUT_SECONDS",
    ("business", "search", "web", "provider"): "WEB_SEARCH_PROVIDER",
    ("business", "search", "web", "timeout_seconds"): "WEB_SEARCH_TIMEOUT_SECONDS",
    ("business", "search", "bocha", "api_key"): "BOCHA_SEARCH_API_KEY",
    ("business", "search", "bocha", "api_url"): "BOCHA_SEARCH_API_URL",
    ("business", "search", "bocha", "freshness"): "BOCHA_SEARCH_FRESHNESS",
}


def read_env_file(path: Path) -> dict[str, str]:
    """读取简单 env 配置文件。

    功能：
    1. 支持 `KEY=value`、单引号和双引号包裹的值。
    2. 忽略空行和注释行。

    参数：
    1. `path`：配置文件路径。

    返回值：
    1. 配置键值字典。文件不存在时返回空字典。

    异常情况：
    1. 文件无法读取时由 `Path.read_text` 抛出异常。
    """

    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[key] = value
    return values


def read_config_file(path: Path) -> dict[str, str]:
    """读取服务端配置文件，支持 env 和轻量 YAML。

    功能：
    1. `.env` / `.env.example` 沿用 `KEY=value` 格式。
    2. `.yaml` / `.yml` 使用分组配置，并转换为现有运行时环境变量。
    3. 文件不存在时返回空字典，保持启动器旧行为。

    参数：
    1. `path`：配置文件路径。

    返回值：
    1. 可合并到子进程环境变量中的键值字典。
    """

    if is_yaml_config_path(path):
        return read_yaml_config_file(path)
    return read_env_file(path)


def is_yaml_config_path(path: Path) -> bool:
    """判断配置文件是否应按 YAML 解析。"""

    name = path.name.lower()
    return name.endswith((".yaml", ".yml", ".yaml.example", ".yml.example"))


def read_yaml_config_file(path: Path) -> dict[str, str]:
    """读取项目约定的 YAML 配置文件。

    主要逻辑：
    1. 使用项目内置的轻量 YAML 解析器，避免为了本地配置额外引入依赖。
    2. 只支持当前配置模板需要的缩进字典和标量值。
    3. 将有层次的 YAML 键转换为旧环境变量名，降低迁移风险。

    参数：
    1. `path`：YAML 配置文件路径。

    返回值：
    1. 环境变量键值字典。
    """

    if not path.exists():
        return {}
    payload = _parse_simple_yaml(path.read_text(encoding="utf-8"), path=path)
    return _yaml_payload_to_env(payload)


def _parse_simple_yaml(text: str, *, path: Path) -> dict[str, Any]:
    """解析本项目配置使用的 YAML 子集。

    异常情况：
    1. 出现列表、多行字符串或缩进结构不合法时抛出 `ValueError`。
    """

    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            raise ValueError(f"{path}:{line_no} 当前配置 YAML 不支持列表语法")
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if "\t" in raw_line[:indent]:
            raise ValueError(f"{path}:{line_no} YAML 缩进不能使用 Tab")
        if ":" not in stripped:
            raise ValueError(f"{path}:{line_no} YAML 行缺少冒号")
        key, raw_value = stripped.split(":", 1)
        key = key.strip().strip('"').strip("'")
        if not key:
            raise ValueError(f"{path}:{line_no} YAML 键不能为空")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError(f"{path}:{line_no} YAML 缩进结构不合法")
        parent = stack[-1][1]
        value = raw_value.strip()
        if not value:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
            continue
        parent[key] = _parse_yaml_scalar(value)
    return root


def _parse_yaml_scalar(value: str) -> str | int | float | bool | None:
    """解析 YAML 标量值。"""

    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    normalized = value.lower()
    if normalized in {"true", "false"}:
        return normalized == "true"
    if normalized in {"null", "~"}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _yaml_payload_to_env(payload: Mapping[str, Any]) -> dict[str, str]:
    """把 YAML 配置对象转换为运行时环境变量。"""

    env: dict[str, str] = {}
    for path, value in _walk_mapping(payload):
        if path == ("devices", "tokens") and isinstance(value, Mapping):
            token_pairs = [f"{device_id}={token}" for device_id, token in value.items() if token is not None]
            env["DEVICE_TOKEN_MAP"] = ",".join(token_pairs)
            continue
        env_key = YAML_ENV_KEY_MAP.get(path)
        if env_key is None or isinstance(value, Mapping) or value is None:
            continue
        env[env_key] = _stringify_config_value(value)
    return env


def _walk_mapping(payload: Mapping[str, Any], prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    """展开嵌套字典。"""

    items: list[tuple[tuple[str, ...], Any]] = []
    for key, value in payload.items():
        path = (*prefix, str(key))
        items.append((path, value))
        if isinstance(value, Mapping):
            items.extend(_walk_mapping(value, path))
    return items


def _stringify_config_value(value: object) -> str:
    """把 YAML 标量转成环境变量字符串。"""

    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def merged_env(config_file: Path | None, defaults: Mapping[str, str]) -> dict[str, str]:
    """合并当前环境变量、配置文件和默认值。

    主要逻辑：
    1. 先继承当前进程环境变量。
    2. 再用配置文件覆盖。
    3. 最后补齐缺失默认值。

    参数：
    1. `config_file`：可选 env 配置文件。
    2. `defaults`：默认配置。

    返回值：
    1. 合并后的环境变量字典。
    """

    env = dict(os.environ)
    if config_file is not None:
        for key, value in read_config_file(config_file).items():
            if key in SECRET_ENV_KEYS and value == "" and env.get(key, "").strip():
                continue
            env[key] = value
        secret_file = config_file.parent / ".env"
        for key, value in read_env_file(secret_file).items():
            if key in SECRET_ENV_KEYS and env.get(key, "").strip():
                continue
            env[key] = value
    for key, value in defaults.items():
        env.setdefault(key, value)
    return env


def ensure_pythonpath(env: dict[str, str], paths: list[Path]) -> None:
    """把路径追加到 `PYTHONPATH` 前部。

    参数：
    1. `env`：待修改的环境变量字典。
    2. `paths`：要加入的路径列表。

    返回值：
    1. 无。
    """

    existing = env.get("PYTHONPATH", "")
    path_texts = [str(path.resolve()) for path in paths if path]
    if existing:
        path_texts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(path_texts)


def require_command(command: str) -> None:
    """检查系统命令是否存在。

    参数：
    1. `command`：命令名称。

    异常情况：
    1. 命令不存在时抛出 `RuntimeError`。
    """

    import shutil

    if shutil.which(command) is None:
        raise RuntimeError(f"缺少必要命令: {command}")


def run_command(args: list[str], *, env: Mapping[str, str] | None = None, cwd: Path | None = None) -> int:
    """以前台方式执行系统命令。

    参数：
    1. `args`：命令参数。
    2. `env`：可选环境变量。
    3. `cwd`：可选工作目录。

    返回值：
    1. 子进程退出码。
    """

    completed = subprocess.run(args, env=dict(env) if env is not None else None, cwd=str(cwd) if cwd else None)
    return int(completed.returncode)


def shell_join(args: list[str]) -> str:
    """把命令参数转成可安全嵌入 shell 的文本。

    参数：
    1. `args`：命令参数列表。

    返回值：
    1. 已转义命令字符串。
    """

    return " ".join(shlex.quote(item) for item in args)


def current_python_command() -> list[str]:
    """返回当前 Python 解释器命令。

    返回值：
    1. 可传给 `subprocess` 的命令列表。
    """

    return [sys.executable]
