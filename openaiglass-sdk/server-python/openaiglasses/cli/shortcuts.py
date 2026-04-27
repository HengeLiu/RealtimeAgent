"""点分形式命令入口。"""

from __future__ import annotations

import sys

from openaiglasses.cli.main import main


def server_run() -> int:
    """执行 `openaiglass.server.run` 命令。"""

    return main(["server", "local", "all", *sys.argv[1:]])


def server_start() -> int:
    """执行 `openaiglass.server.start` 命令。"""

    return main(["server", "local", "start", *sys.argv[1:]])


def server_stop() -> int:
    """执行 `openaiglass.server.stop` 命令。"""

    return main(["server", "local", "stop", *sys.argv[1:]])


def server_logs() -> int:
    """执行 `openaiglass.server.logs` 命令。"""

    return main(["server", "local", "logs", *sys.argv[1:]])


def phone_open() -> int:
    """执行 `openaiglass.phone.open` 命令。"""

    return main(["phone", "open", *sys.argv[1:]])


def phone_build_sim() -> int:
    """执行 `openaiglass.phone.build-sim` 命令。"""

    return main(["phone", "build-sim", *sys.argv[1:]])


def config_sync() -> int:
    """执行 `openaiglass.config.sync` 命令。"""

    return main(["config", "sync", *sys.argv[1:]])


def sdk_preflight() -> int:
    """执行 `openaiglass.sdk.preflight` 命令。"""

    from openaiglasses.cli.preflight import main as preflight_main

    return preflight_main()


def sdk_live_check() -> int:
    """执行 `openaiglass.sdk.live-check` 命令。"""

    from openaiglasses.cli.live_check import main as live_check_main

    return live_check_main()


def sdk_package_check() -> int:
    """执行 `openaiglass.sdk.package-check` 命令。"""

    from openaiglasses.cli.package_check import main as package_check_main

    return package_check_main()


def sdk_contract_tests() -> int:
    """执行 `openaiglass.sdk.contract-tests` 命令。"""

    from openaiglasses.cli.contract_tests import main as contract_tests_main

    return contract_tests_main()


def sdk_audio_samples() -> int:
    """执行 `openaiglass.sdk.audio-samples` 命令。"""

    from devtools.audio_sample_batch_runner import main as audio_samples_main

    return audio_samples_main()


def glass_start() -> int:
    """执行 `openaiglass.glass.start` 命令。"""

    return main(["glass", *sys.argv[1:]])


def glass_build() -> int:
    """执行 `openaiglass.glass.build` 命令。"""

    return main(["glass", "--build-only", *sys.argv[1:]])
