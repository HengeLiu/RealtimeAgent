from __future__ import annotations

import json
from typing import Any

from protocol.messages.envelope import Envelope


class JsonMessageCodec:
    """Unified JSON codec for protocol envelopes."""

    def encode(self, envelope: Envelope) -> str:
        envelope.validate()
        return json.dumps(envelope.to_dict(), ensure_ascii=False, separators=(",", ":"))

    def decode(self, raw: str | bytes) -> Envelope:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data: Any = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Protocol payload must be a JSON object")
        return Envelope.from_dict(data)
