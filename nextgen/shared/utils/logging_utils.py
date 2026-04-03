"""共享日志工具。"""

from __future__ import annotations

import logging
from pathlib import Path


def setup_file_logger(name: str, log_path: str) -> logging.Logger:
    """配置一个同时输出到终端和文件的日志器。

    参数：
    - name：日志器名称
    - log_path：日志文件路径

    返回值：
    - 配置完成的日志器对象
    """

    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s %(message)s")

    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger
