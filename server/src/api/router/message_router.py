from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from protocol.messages.envelope import Envelope

Handler = Callable[[Envelope], list[Envelope]]


@dataclass(slots=True)
class MessageRouter:
    _exact_handlers: dict[str, Handler] = field(default_factory=dict)
    _module_handlers: dict[str, Handler] = field(default_factory=dict)
    _domain_handlers: dict[str, Handler] = field(default_factory=dict)

    def register_message(self, message_name: str, handler: Handler) -> None:
        self._exact_handlers[message_name] = handler

    def register_domain(self, domain: str, handler: Handler) -> None:
        self._domain_handlers[domain] = handler

    def register_module(self, module_name: str, handler: Handler) -> None:
        self._module_handlers[module_name] = handler

    def route(self, envelope: Envelope) -> list[Envelope]:
        handler = self._exact_handlers.get(envelope.message_name)
        if handler:
            return handler(envelope)

        module_handler = self._module_handlers.get(envelope.target.module)
        if module_handler:
            module_result = module_handler(envelope)
            if module_result:
                return module_result

        domain = envelope.message_name.split(".", 1)[0]
        domain_handler = self._domain_handlers.get(domain)
        if domain_handler:
            return domain_handler(envelope)
        raise KeyError(f"No handler registered for {envelope.message_name}")
