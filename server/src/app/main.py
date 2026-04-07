from __future__ import annotations

from app.bootstrap import bootstrap



def main() -> None:
    container = bootstrap()
    print(
        "server bootstrap complete",
        {
            "protocol_version": container.settings.protocol_version,
            "registered_task_types": container.task_registry.list_task_types(),
            "registered_skills": [s["name"] for s in container.skill_registry.list_skills()],
        },
    )


if __name__ == "__main__":
    main()
