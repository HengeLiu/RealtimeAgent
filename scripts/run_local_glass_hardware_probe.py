"""本机伪眼镜硬件探测脚本。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nextgen.apps.glass.runtime.app import GlassRuntimeApp


def build_argument_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="探测本机伪眼镜硬件能力。")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--camera-output", default="tmp/glass-probe/camera.jpg")
    parser.add_argument("--audio-output", default="tmp/glass-probe/mic.wav")
    parser.add_argument("--record-seconds", type=float, default=2.0)
    parser.add_argument("--speak-text", default="本机伪眼镜喇叭测试")
    return parser


def main() -> None:
    """脚本主入口。"""

    args = build_argument_parser().parse_args()

    runtime = GlassRuntimeApp()
    runtime.start()
    runtime.enable_local_camera(camera_index=args.camera_index)
    runtime.enable_local_microphone()
    runtime.enable_local_speaker()

    report = {
        "camera": runtime.capture_real_camera_frame(output_path=args.camera_output),
        "microphone": runtime.record_real_microphone_audio(
            duration_sec=args.record_seconds,
            output_path=args.audio_output,
        ),
        "speaker": runtime.device_control.execute_speech(args.speak_text),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
