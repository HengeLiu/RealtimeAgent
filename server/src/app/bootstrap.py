from __future__ import annotations

from app.container import AppContainer, build_container



def bootstrap() -> AppContainer:
    return build_container()
