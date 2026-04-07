from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ConversationTurn:
    role: str
    content: str


@dataclass(slots=True)
class ConversationContextStore:
    max_turns: int = 20
    _store: dict[str, list[ConversationTurn]] = field(default_factory=dict)

    def append(self, conversation_id: str, *, role: str, content: str) -> None:
        turns = self._store.setdefault(conversation_id, [])
        turns.append(ConversationTurn(role=role, content=content))
        if len(turns) > self.max_turns:
            del turns[: len(turns) - self.max_turns]

    def snapshot(self, conversation_id: str) -> list[ConversationTurn]:
        return list(self._store.get(conversation_id, []))
