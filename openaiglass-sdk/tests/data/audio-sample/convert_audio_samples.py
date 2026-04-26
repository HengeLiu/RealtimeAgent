#!/usr/bin/env python3
"""把 audio-sample 目录下的 m4a 批量转换成 Phase C 可用 wav。"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    current_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="批量转换 audio-sample 目录下的 m4a")
    parser.add_argument(
        "--input-dir",
        default=str(current_dir),
        help="待转换音频目录，默认当前 audio-sample 目录",
    )
    parser.add_argument(
        "--output-dir",
        default=str(current_dir / "wav"),
        help="转换后的 wav 输出目录",
    )
    return parser.parse_args()


def main() -> None:
    """脚本主入口。"""

    args = parse_args()
    input_dir = Path(args.input_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[4]

    sources = sorted(input_dir.glob("*.m4a"))
    if not sources:
        print(f"no m4a files found in {input_dir}")
        return

    for source in sources:
        target = output_dir / f"{source.stem}.wav"
        convert_m4a_to_wav(repo_root, source, target)
        print(f"converted: {source.name} -> {target}")


def convert_m4a_to_wav(repo_root: Path, source: Path, target: Path) -> None:
    """把单个 m4a 转成 16kHz/mono/16bit wav。"""

    swift_converter = repo_root / "script/convert_audio_to_phase_c.swift"
    if swift_converter.exists():
        try:
            subprocess.run(
                ["swift", str(swift_converter), str(source), str(target)],
                cwd=repo_root,
                check=True,
            )
            return
        except subprocess.CalledProcessError:
            pass

    afconvert = shutil.which("afconvert")
    if afconvert:
        subprocess.run(
            [
                afconvert,
                "-f",
                "WAVE",
                "-d",
                "LEI16",
                "-c",
                "1",
                "-r",
                "16000",
                str(source),
                str(target),
            ],
            check=True,
        )
        return

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-sample_fmt",
                "s16",
                str(target),
            ],
            check=True,
        )
        return

    raise RuntimeError("未找到可用转码器：需要 macOS 的 afconvert 或 ffmpeg")


if __name__ == "__main__":
    main()
