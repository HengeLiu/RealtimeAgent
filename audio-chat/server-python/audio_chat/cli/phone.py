from __future__ import annotations

import argparse

from audio_chat.endpoints.python_playback import main as playback_main


def mock(argv: list[str] | None = None) -> None:
    """启动 Python phone mock。

    主要逻辑：当前 P0-A 先复用 Python playback endpoint 的真实协议闭环；后续 phone
    mock 线路可以在此入口下替换为更完整的多设备 mock。
    """

    parser = argparse.ArgumentParser(prog="audio-chat.phone.mock", description="启动 audio-chat Python phone mock")
    parser.add_argument("--config", default="")
    parser.add_argument("--help-playback", action="store_true", help="显示底层 playback 入口帮助")
    args = parser.parse_args(argv)
    if args.help_playback:
        playback_main(["--help"])
        return
    command = []
    if args.config:
        command.extend(["--config", args.config])
    playback_main(command)

