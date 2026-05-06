from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import urlopen

from audio_chat.config import load_yaml_config
from audio_chat.protocol import CONTROL_EVENTS, STREAM_TYPES


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="")
    parser.add_argument("--require-server", action="store_true")
    parser.add_argument("--report", default="audio-chat/runs/preflight.json")
    args = parser.parse_args(argv)
    server_health = None
    if args.require_server:
        if not args.config:
            raise SystemExit("--require-server requires --config")
        loaded = load_yaml_config(args.config)
        health_url = loaded.server.public_url.rstrip("/") + "/api/health"
        with urlopen(health_url, timeout=5) as response:
            server_health = json.loads(response.read().decode("utf-8"))
        debug_url = loaded.server.public_url.rstrip("/") + "/api/debug/devices"
        with urlopen(debug_url, timeout=5) as response:
            server_debug = json.loads(response.read().decode("utf-8"))
    report = {
        "status": "ok",
        "protocol_version": "audio-chat.v1",
        "control_events": sorted(CONTROL_EVENTS),
        "stream_types": sorted(STREAM_TYPES),
    }
    if server_health is not None:
        report["server_health"] = server_health
        report["server_debug_devices"] = server_debug
    report["not_implemented"] = {
        "audio_pipeline.resample": "loaded but not implemented beyond format validation",
        "audio_pipeline.volume_normalize": "loaded but not implemented",
        "audio_pipeline.vad": "TextAgentCore owns turn boundary; server VAD is not implemented",
        "audio_pipeline.asr_sidecar": "not implemented",
    }
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"preflight ok: {path}")
