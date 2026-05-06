from __future__ import annotations

import argparse
import json
from pathlib import Path

from audio_chat.protocol import CONTROL_EVENTS, STREAM_TYPES


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="audio-chat/runs/preflight.json")
    args = parser.parse_args(argv)
    report = {
        "status": "ok",
        "protocol_version": "audio-chat.v1",
        "control_events": sorted(CONTROL_EVENTS),
        "stream_types": sorted(STREAM_TYPES),
    }
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"preflight ok: {path}")
