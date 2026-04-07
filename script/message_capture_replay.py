from __future__ import annotations

import argparse
import json
from pathlib import Path

from protocol.codec import JsonMessageCodec



def capture_message(raw: str, output: Path) -> None:
    envelope = JsonMessageCodec().decode(raw)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as f:
        f.write(json.dumps(envelope.to_dict(), ensure_ascii=False) + "\n")



def replay_messages(input_file: Path) -> list[str]:
    codec = JsonMessageCodec()
    encoded: list[str] = []
    for line in input_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        envelope = codec.decode(line)
        encoded.append(codec.encode(envelope))
    return encoded



def main() -> None:
    parser = argparse.ArgumentParser(description="Capture/replay protocol envelopes")
    sub = parser.add_subparsers(dest="cmd", required=True)

    capture = sub.add_parser("capture")
    capture.add_argument("--raw", required=True, help="Raw JSON envelope")
    capture.add_argument("--output", required=True, type=Path)

    replay = sub.add_parser("replay")
    replay.add_argument("--input", required=True, type=Path)

    args = parser.parse_args()
    if args.cmd == "capture":
        capture_message(args.raw, args.output)
        print(f"captured -> {args.output}")
        return

    encoded = replay_messages(args.input)
    print(json.dumps({"count": len(encoded), "messages": encoded}, ensure_ascii=False))


if __name__ == "__main__":
    main()
