"""服务端 CLI 启动环境测试。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


SDK_ROOT = Path(__file__).resolve().parents[2] / "server-python"
if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))

from openaiglasses.cli import server


def test_server_env_disables_inner_file_handler_for_background_logs(tmp_path: Path) -> None:
    """测试目标：避免后台启动时同一条服务端日志写入两次。

    测试方法：
    1. 构造本地后台启动参数并指定日志文件。
    2. 调用 `_server_env(...)` 构造子进程环境。
    3. 同时检查 stdout 重定向目标仍是指定日志文件。

    预期结果：
    1. 子进程环境中的 `LOG_FILE` 为空，服务端 logger 不再额外挂 FileHandler。
    2. 启动器自身仍把 stdout/stderr 重定向到指定日志文件。
    """

    log_file = tmp_path / "server.log"
    args = argparse.Namespace(
        repo_root=str(tmp_path),
        sdk_python_root=str(SDK_ROOT),
        app_root="",
        config="",
        host=None,
        port=None,
        log_dir="",
        log_file=str(log_file),
    )

    env = server._server_env(args)  # noqa: SLF001 - CLI 回归测试需要验证内部环境拼装

    assert env["LOG_FILE"] == ""
    assert server._log_file(args) == log_file.resolve()  # noqa: SLF001
