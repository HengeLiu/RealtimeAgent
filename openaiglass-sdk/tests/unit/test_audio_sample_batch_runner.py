"""真实音频样例批量回归工具测试。"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from devtools.audio_sample_batch_runner import (
    build_client_command,
    build_reply_path,
    build_result_path,
    discover_audio_samples,
    parse_client_stdout,
    run_audio_sample_batch,
)


class AudioSampleBatchRunnerTestCase(unittest.TestCase):
    """验证真实音频样例批量回归工具。"""

    def test_discover_audio_samples_returns_sorted_cases(self) -> None:
        with tempfile.TemporaryDirectory(prefix="audio-sample-batch-") as temp_dir:
            samples_dir = Path(temp_dir)
            (samples_dir / "b.wav").write_bytes(b"")
            (samples_dir / "a.wav").write_bytes(b"")
            (samples_dir / "ignore.txt").write_text("", encoding="utf-8")

            cases = discover_audio_samples(samples_dir)

        self.assertEqual([case.sample_name for case in cases], ["a", "b"])

    def test_parse_client_stdout_extracts_reply_and_wav_path(self) -> None:
        stdout = "\n".join(
            [
                "registered: glass-001",
                "voice_session_open: sess_001",
                "reply_text: 这是回复",
                "saved_reply_wav: /tmp/reply.wav",
            ]
        )

        session_id, reply_text, reply_wav_path = parse_client_stdout(stdout)

        self.assertEqual(session_id, "sess_001")
        self.assertEqual(reply_text, "这是回复")
        self.assertEqual(reply_wav_path, "/tmp/reply.wav")

    def test_build_client_command_contains_expected_arguments(self) -> None:
        command = build_client_command(
            host="127.0.0.1",
            port=8765,
            device_id="glass-001",
            pair_token="pair-demo-token",
            wav_path=Path("/tmp/input.wav"),
            save_reply_path=Path("/tmp/reply.wav"),
            timeout_seconds=12.5,
            chunk_interval_ms=30,
        )

        self.assertEqual(command[1:3], ["-m", "devtools.simple_glass_audio_client"])
        self.assertIn("/tmp/input.wav", command)
        self.assertIn("/tmp/reply.wav", command)
        self.assertIn("12.5", command)
        self.assertIn("30", command)

    def test_run_audio_sample_batch_writes_result_and_summary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="audio-sample-batch-") as temp_dir:
            temp_root = Path(temp_dir)
            samples_dir = temp_root / "samples"
            output_root = temp_root / "output"
            samples_dir.mkdir(parents=True, exist_ok=True)
            (samples_dir / "你是谁呀.wav").write_bytes(b"RIFF")
            (samples_dir / "给我讲个笑话吧.wav").write_bytes(b"RIFF")

            def fake_executor(command, text, capture_output, check):
                reply_path = Path(command[command.index("--save-reply") + 1])
                reply_path.parent.mkdir(parents=True, exist_ok=True)
                reply_path.write_bytes(b"RIFF")
                sample_name = Path(command[command.index("--wav") + 1]).stem
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=(
                        f"voice_session_open: sess_{sample_name}\n"
                        f"reply_text: 已收到 {sample_name}\n"
                        f"saved_reply_wav: {reply_path}\n"
                    ),
                    stderr="",
                )

            def fake_session_fetcher(host, port, session_id, timeout_seconds):
                return {
                    "session_id": session_id,
                    "model_request": {
                        "model": "qwen3.6-plus",
                        "messages": [
                            {"role": "system", "content": "你是测试系统提示词"},
                            {"role": "user", "content": "你是谁呀"},
                        ],
                    },
                    "messages": [
                        {
                            "role": "system",
                            "kind": "system_prompt",
                            "text": "你是测试系统提示词",
                        },
                        {
                            "role": "user",
                            "kind": "audio_input",
                            "text": "你是谁呀",
                        },
                    ],
                    "capability_traces": [
                        {"capability_name": "capture_photo", "status": "succeeded"},
                    ],
                }

            summary = run_audio_sample_batch(
                host="127.0.0.1",
                port=8765,
                device_id="glass-001",
                pair_token="pair-demo-token",
                samples_dir=samples_dir,
                output_root=output_root,
                session_fetcher=fake_session_fetcher,
                executor=fake_executor,
            )

            summary_json = json.loads((output_root / "summary.json").read_text(encoding="utf-8"))
            first_result_json = json.loads(
                build_result_path(output_root, "你是谁呀").read_text(encoding="utf-8")
            )

        self.assertEqual(summary.total_count, 2)
        self.assertEqual(summary.success_count, 2)
        self.assertEqual(summary.failure_count, 0)
        self.assertEqual(summary_json["total_count"], 2)
        self.assertTrue(build_reply_path(output_root, "你是谁呀").name.endswith("reply.wav"))
        self.assertEqual(first_result_json["session_id"], "sess_你是谁呀")
        self.assertEqual(first_result_json["reply_text"], "已收到 你是谁呀")
        self.assertEqual(first_result_json["agent_session"]["model_request"]["model"], "qwen3.6-plus")
        self.assertEqual(first_result_json["agent_session"]["model_request"]["messages"][0]["role"], "system")
        self.assertEqual(first_result_json["agent_session"]["messages"][0]["role"], "system")
        self.assertEqual(
            first_result_json["agent_session"]["messages"][0]["text"],
            "你是测试系统提示词",
        )
        self.assertEqual(
            first_result_json["agent_session"]["capability_traces"][0]["capability_name"],
            "capture_photo",
        )


if __name__ == "__main__":
    unittest.main()
