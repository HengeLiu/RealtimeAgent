"""SDK 命令行通用工具函数。"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Mapping


SECRET_ENV_KEYS = {"DASHSCOPE_API_KEY"}


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
        for key, value in read_env_file(config_file).items():
            if key in SECRET_ENV_KEYS and value == "" and env.get(key, "").strip():
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
