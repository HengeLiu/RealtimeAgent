"""眼镜端最小可执行入口。"""

from __future__ import annotations

import argparse
import time


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    返回值：
    1. 命令行参数对象。
    """

    parser = argparse.ArgumentParser(description="眼镜端模拟入口")
    parser.add_argument("--once", action="store_true", help="仅执行一次状态打印后退出")
    parser.add_argument("--interval", type=float, default=3.0, help="状态打印间隔（秒）")
    return parser.parse_args()


def main() -> None:
    """眼镜端主循环。

    主要逻辑：
    1. 打印眼镜端待机状态。
    2. 在 `--once` 模式下立即退出，否则持续循环。
    """

    args = parse_args()
    print("[glass] 眼镜端模拟入口已启动，当前处于待机状态")
    if args.once:
        print("[glass] --once 模式，立即退出")
        return

    try:
        while True:
            print("[glass] 心跳：眼镜端任务循环运行中")
            time.sleep(max(args.interval, 0.5))
    except KeyboardInterrupt:
        print("[glass] 收到中断，眼镜端退出")


if __name__ == "__main__":
    main()
