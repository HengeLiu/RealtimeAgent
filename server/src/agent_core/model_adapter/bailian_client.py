from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError


@dataclass(slots=True)
class BailianHttpClient:
    endpoint: str
    api_key: str
    timeout_seconds: float = 20.0

    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.endpoint,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:  # noqa: S310
                body = resp.read().decode("utf-8")
                parsed = json.loads(body) if body else {}
                if isinstance(parsed, dict):
                    return parsed
                return {"text": str(parsed), "tool_calls": []}
        except HTTPError as exc:
            message = exc.read().decode("utf-8", errors="ignore")
            return {"text": "", "tool_calls": [], "raw_text": message, "error": f"http_{exc.code}"}
        except URLError as exc:
            return {"text": "", "tool_calls": [], "error": f"network_error:{exc.reason}"}
