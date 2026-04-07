from __future__ import annotations

import argparse
import json

from app.container import build_container
from infra.config import Settings
from protocol.enums import Priority, TaskSource



def main() -> None:
    parser = argparse.ArgumentParser(description="Task status query CLI")
    parser.add_argument("--task-id", default="", help="Task ID to query")
    parser.add_argument("--seed-task", action="store_true", help="Create one demo timer task before query")
    args = parser.parse_args()

    container = build_container(Settings(runtime_enable_loop=False))
    if args.seed_task:
        container.task_manager.create_task(
            task_type="timer",
            source=TaskSource.SYSTEM,
            priority=Priority.NORMAL,
            input_data={"duration_seconds": 3, "notify_message": "三秒到了"},
        )

    if args.task_id:
        task = container.task_manager.get(args.task_id)
        print(json.dumps(task.to_dict() if task else {"error": "task_not_found"}, ensure_ascii=False))
        return

    print(json.dumps([task.to_dict() for task in container.task_manager.list()], ensure_ascii=False))


if __name__ == "__main__":
    main()
