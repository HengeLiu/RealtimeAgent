#!/usr/bin/env python3
"""Omni 方位理解探针。

主要功能：
1. 按当前 Omni Manual 链路的方式调用 Qwen Omni Realtime：图片走 DashScope
   `append_video`，方位标签走 `create_item` 的 `input_text`，问题走
   `create_response` 的 instructions / 文本会话项。
2. 给定左/中/右三张内容不同的图片，分别在每张图片前注入一条方位说明文本，
   然后提问“左边/正前方/右边分别看到了什么”，观察模型能否把方位和正确的图片
   内容对应起来。
3. 复用主链路的 `_prepare_omni_realtime_image` 压缩逻辑，保持与真实链路一致。

运行（需要 Omni 专用 DashScope key）：
    DASHSCOPE_API_KEY_OMNI_CAP=sk-xxx \
      uv run python tools/omni_direction_probe.py \
      --left  'testdata/image-sample/刚子看电脑.jpeg' \
      --center 'testdata/image-sample/基辅美食.jpeg' \
      --right 'testdata/image-sample/刚子蹲守.jpeg'
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

# 复用主链路的图片压缩逻辑，保证与真实 append_image 一致。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent-server"))
from realtime_agent.conversation.core.omni_host import _prepare_omni_realtime_image  # noqa: E402


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="测试 Omni 模型对图片方位（左/中/右）的理解。")
    parser.add_argument("--left", type=Path, required=True, help="左侧图片")
    parser.add_argument("--center", type=Path, default=None, help="正前方图片（可选）")
    parser.add_argument("--right", type=Path, required=True, help="右侧图片")
    parser.add_argument("--model", default="qwen3.5-omni-plus-realtime")
    parser.add_argument(
        "--api-key-env",
        default="DASHSCOPE_API_KEY_OMNI_CAP",
        help="读取 DashScope key 的环境变量名",
    )
    parser.add_argument(
        "--question",
        default=(
            "刚才依次给你看了三张画面，分别来自我的左侧、正前方、右侧。"
            "请分别说出：我左边看到的是什么？正前方看到的是什么？右边看到的是什么？"
            "请按‘左侧 / 正前方 / 右侧’的顺序，每个方位用一句话描述画面里的主要内容。"
        ),
    )
    parser.add_argument("--websocket-url", default="wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
    parser.add_argument("--wait-seconds", type=float, default=30.0)
    parser.add_argument("--settle-seconds", type=float, default=2.0, help="最后一张图片 commit 后、提问前的等待秒数")
    parser.add_argument("--trailing-spacer", action="store_true", help="3 张图片后补一轮静音 commit 占位，保护最后一张真实图片")
    parser.add_argument(
        "--mode",
        choices=["append_video", "item_image"],
        default="append_video",
        help="append_video=按当前链路逐帧 append_video（方位走独立文本项）；"
        "item_image=每个方位用一条 create_item，把方位文本和图片放进同一条 message 的 content 里",
    )
    return parser


class _Collector:
    """收集模型文本输出与关键事件。"""

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.transcript_parts: list[str] = []
        self.session_updated = threading.Event()
        self.response_done = threading.Event()
        self.errors: list[str] = []
        self._lock = threading.Lock()
        self.committed_count = 0

    def wait_committed(self, target: int, timeout: float) -> bool:
        """轮询等待已确认 commit 的输入缓冲数量达到 target。"""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self.committed_count >= target:
                    return True
            time.sleep(0.05)
        return False

    def on_event(self, message: dict[str, Any]) -> None:
        etype = str(message.get("type") or "")
        if etype == "session.updated":
            self.session_updated.set()
        elif etype in {"input_audio_buffer.committed", "input_image_buffer.committed"}:
            # 一轮 commit 会同时 ack 音频和图片缓冲，这里只按音频缓冲计数避免重复。
            if etype == "input_audio_buffer.committed":
                with self._lock:
                    self.committed_count += 1
        elif etype in {"response.text.delta", "response.output_text.delta"}:
            self.text_parts.append(str(message.get("delta") or ""))
        elif etype == "response.audio_transcript.delta":
            self.transcript_parts.append(str(message.get("delta") or ""))
        elif etype == "response.done":
            self.response_done.set()
        elif etype == "error":
            err = message.get("error") if isinstance(message.get("error"), dict) else {}
            self.errors.append(str(err.get("message") or message.get("message") or message))
            print(f"[error] {self.errors[-1]}", file=sys.stderr)


def main() -> int:
    args = _build_arg_parser().parse_args()
    api_key = os.getenv(args.api_key_env)
    if not api_key:
        print(f"缺少 {args.api_key_env}，无法连接 DashScope Omni Realtime。", file=sys.stderr)
        return 2

    try:
        import dashscope
        from dashscope.audio.qwen_omni import (
            MultiModality,
            OmniRealtimeCallback,
            OmniRealtimeConversation,
        )
    except ImportError as exc:
        print(f"需要 dashscope SDK：{exc}", file=sys.stderr)
        return 2

    frames = [("左侧", args.left)]
    if args.center is not None:
        frames.append(("正前方", args.center))
    frames.append(("右侧", args.right))
    prepared: list[tuple[str, bytes]] = []
    for direction, path in frames:
        path = path.resolve()
        if not path.is_file():
            print(f"图片不存在：{path}", file=sys.stderr)
            return 2
        image_bytes, meta = _prepare_omni_realtime_image(path.read_bytes())
        prepared.append((direction, image_bytes))
        print(f"[prepare] {direction:<4} {path.name}  bytes={len(image_bytes)} compressed={meta.get('image_compressed')}")

    dashscope.api_key = api_key
    collector = _Collector()

    class _Callback(OmniRealtimeCallback):
        def on_open(self) -> None:  # pragma: no cover
            print("[ws] opened")

        def on_close(self, code: Any, msg: Any) -> None:  # pragma: no cover
            print(f"[ws] closed code={code} msg={msg}")

        def on_event(self, message: dict[str, Any]) -> None:  # pragma: no cover
            collector.on_event(message)

    conversation = OmniRealtimeConversation(
        model=args.model,
        callback=_Callback(),
        url=args.websocket_url.rstrip("/"),
        api_key=api_key,
    )
    from dashscope.audio.qwen_omni import AudioFormat

    conversation.connect()
    # 只取文本输出，便于直接判读模型是否理解方位；voice 必填，否则 provider 报 Voice 'null'。
    conversation.update_session(
        voice="Tina",
        output_modalities=[MultiModality.TEXT],
        input_audio_format=AudioFormat.PCM_16000HZ_MONO_16BIT,
        enable_turn_detection=False,
        instructions=(
            "你是中文视觉助手。用户会依次给你看几张画面，并在每张画面前用文字说明该画面来自哪个方位"
            "（左侧、正前方、右侧等）。请严格按照方位说明，把每个方位和对应那张画面的内容对应起来，"
            "不要混淆方位。只根据画面内容回答，看不清就说看不清。"
        ),
    )
    if not collector.session_updated.wait(timeout=5):
        print("[warn] session.updated 未在 5s 内返回，继续尝试。", file=sys.stderr)

    commit = getattr(conversation, "commit", None) or getattr(conversation, "commit_input_audio", None)
    if args.mode == "append_video":
        # Qwen Omni Realtime 把同一轮 append_video 的多张图片塞进一个 input_image_buffer，
        # 当成连续视频帧 commit，无法给每帧单独绑方位。这里改为“一图一轮”：每个方位
        # 单独 append 一段静音音频 + 一张图片 + commit，形成 1:1 的会话项序列，让方位文本
        # 和图片在会话历史里顺序对应。
        silence = b"\x00\x00" * 16000  # 1s @ 16k mono pcm16
        for index, (direction, image_bytes) in enumerate(prepared, start=1):
            conversation.create_item(
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": f"下面这张画面来自我的【{direction}】。"}],
                }
            )
            conversation.append_audio(base64.b64encode(silence).decode("ascii"))
            conversation.append_video(base64.b64encode(image_bytes).decode("ascii"))
            if callable(commit):
                commit()
            # 等本轮 commit 被 provider ack 再发下一张，确保最后一张图片不被漏吃。
            acked = collector.wait_committed(index, timeout=10.0)
            print(f"[send] {direction} 图片已追加并 commit（ack={acked}）")

        if args.trailing_spacer:
            # provider 会丢掉“最后一张被 commit 的图片”。补一轮重复图片占位，
            # 让被丢的那一张是占位副本，保护最后一张真实图片。
            _, last_image = prepared[-1]
            conversation.create_item(
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "（占位帧，忽略）"}],
                }
            )
            conversation.append_audio(base64.b64encode(silence).decode("ascii"))
            conversation.append_video(base64.b64encode(last_image).decode("ascii"))
            if callable(commit):
                commit()
            collector.wait_committed(len(prepared) + 1, timeout=10.0)
            print("[send] 追加一轮重复图片占位 commit")
    else:
        # item_image：每个方位一条 create_item，把方位文本和图片放进同一条 message
        # 的 content 里，让方位标签与图片在同一会话项内强绑定。
        for direction, image_bytes in prepared:
            data_uri = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
            conversation.create_item(
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": f"下面这张画面来自我的【{direction}】："},
                        {"type": "input_image", "image_url": data_uri},
                    ],
                }
            )
            print(f"[send] {direction} message item（文本+图片）已创建")
            time.sleep(0.3)

    # commit ack 之后再给一点点缓冲处理时间，然后提问。
    time.sleep(args.settle_seconds)

    # 提问走独立文本会话项，再显式创建响应。
    conversation.create_item(
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": args.question}],
        }
    )
    conversation.create_response(output_modalities=[MultiModality.TEXT])
    print("[send] 已请求模型回答，等待响应…\n")

    collector.response_done.wait(timeout=args.wait_seconds)
    time.sleep(0.5)

    answer = "".join(collector.text_parts).strip()
    transcript = "".join(collector.transcript_parts).strip()
    print("=" * 60)
    print("模型文本回答：")
    print(answer or "(无 response.text，下面是 audio transcript)")
    if not answer and transcript:
        print(transcript)
    print("=" * 60)
    if collector.errors:
        print(f"\n出现 {len(collector.errors)} 条错误：")
        for err in collector.errors:
            print(f"  - {err}")

    try:
        conversation.close()
    except Exception:
        pass
    return 0 if (answer or transcript) else 1


if __name__ == "__main__":
    raise SystemExit(main())
