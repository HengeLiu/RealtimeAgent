from __future__ import annotations

import argparse

from audio_chat.endpoints.python_phone_mock import main as phone_mock_main


def mock(argv: list[str] | None = None) -> None:
    """启动 Python phone mock。

    主要逻辑：启动独立 Python phone mock endpoint，使用真实 control / stream
    WebSocket 协议注册设备并消费执行器 stream。
    """

    parser = argparse.ArgumentParser(prog="audio-chat.phone.mock", description="启动 audio-chat Python phone mock")
    parser.add_argument("--config", default="")
    args = parser.parse_args(argv)
    command = []
    if args.config:
        command.extend(["--config", args.config])
    phone_mock_main(command)
