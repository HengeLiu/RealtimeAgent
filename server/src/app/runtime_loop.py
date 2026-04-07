from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.container import AppContainer
from infra.logging import log_event


@dataclass(slots=True)
class RuntimeLoop:
    container: AppContainer

    def run(self) -> None:
        tick = 0
        max_ticks = self.container.settings.runtime_max_ticks
        interval = self.container.settings.runtime_tick_seconds

        while True:
            tick += 1
            self._tick(tick)

            if max_ticks > 0 and tick >= max_ticks:
                break
            time.sleep(interval)

    def _tick(self, tick: int) -> None:
        trace_id = f"trace_runtime_{tick}"
        try:
            self.container.system_handler.reconcile_device_health()
        except Exception as exc:  # pragma: no cover - defensive path
            log_event(
                self.container.logger,
                logging.ERROR,
                "runtime.health_reconcile_failed",
                trace_id=trace_id,
                error=str(exc),
            )

        try:
            started = self.container.task_manager.start_next()
            if started:
                log_event(
                    self.container.logger,
                    logging.INFO,
                    "runtime.task_started",
                    task_id=started.task_id,
                    trace_id=trace_id,
                    status=started.status.value,
                )
            else:
                log_event(
                    self.container.logger,
                    logging.DEBUG,
                    "runtime.idle_tick",
                    trace_id=trace_id,
                )
        except Exception as exc:  # pragma: no cover - defensive path
            log_event(
                self.container.logger,
                logging.ERROR,
                "runtime.task_tick_failed",
                trace_id=trace_id,
                error=str(exc),
            )
