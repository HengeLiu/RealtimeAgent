#!/usr/bin/env python3
"""VL 模型方位感知探针。

主要功能：
1. 按项目里 Vision 链路的真实调用方式（OpenAI/DashScope-compatible Chat
   Completions + `image_url` content block）给 VL 模型（默认 qwen3.6-flash）
   提交三张内容不同的图片（左/中/右），测试哪种消息组织方式能让模型正确感知方位。
2. 对比三种注入方式：
   - interleaved：单条 user 消息，图文交错，每张图前放方位文字标签。
   - labels_after：单条 user 消息，先放三张图，再用文字说明“第1张左/第2张中/第3张右”。
   - separate_turns：每张图一轮 user 消息带方位标签 + assistant“收到”，最后再提问。
3. 复用 VL 探针的预览压缩逻辑，控制请求体大小。

运行（需要 DASHSCOPE_API_KEY）：
    DASHSCOPE_API_KEY=sk-xxx \
      uv run python tools/vl_direction_probe.py \
      --left  'testdata/image-sample/基辅美食.jpeg' \
      --center 'testdata/image-sample/刚子看电脑.jpeg' \
      --right 'testdata/image-sample/IMG_4674大.jpeg'
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from openai import OpenAI


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="测试 VL 模型对图片方位（左/中/右）的感知。")
    parser.add_argument("--left", type=Path, required=True, help="左侧图片")
    parser.add_argument("--center", type=Path, default=None, help="正前方图片（可选）")
    parser.add_argument("--right", type=Path, required=True, help="右侧图片")
    parser.add_argument("--model", default="qwen3.6-flash")
    parser.add_argument("--api-key-env", default="DASHSCOPE_API_KEY")
    parser.add_argument("--base-url", default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    parser.add_argument(
        "--mode",
        choices=["interleaved", "labels_after", "separate_turns", "all"],
        default="all",
    )
    parser.add_argument("--preview-max-side", type=int, default=768, help="预览图最大边长；0 表示原图")
    parser.add_argument("--jpeg-quality", type=int, default=80)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument(
        "--question",
        default=(
            "请分别回答：我左侧看到的是什么？正前方看到的是什么？右侧看到的是什么？"
            "按‘左侧 / 正前方 / 右侧’的顺序，每个方位用一句话描述画面主要内容，不要混淆方位。"
        ),
    )
    return parser


SYSTEM_PROMPT = (
    "你是中文视觉助手。用户会给你几张画面，并用文字标明每张画面来自哪个方位（左侧、正前方、右侧等）。"
    "请严格按照方位说明，把每个方位和对应那张画面的内容对应起来，不要混淆方位。"
    "只根据画面内容回答，看不清就说看不清。"
)


def _preview_data_url(path: Path, *, max_side: int, jpeg_quality: int) -> str:
    source = path.read_bytes()
    if max_side <= 0:
        return "data:image/jpeg;base64," + base64.b64encode(source).decode("ascii")
    image = cv2.imdecode(np.frombuffer(source, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"无法解码图片：{path}")
    height, width = image.shape[:2]
    scale = min(1.0, float(max_side) / float(max(width, height)))
    if scale < 1.0:
        image = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), max(1, min(100, jpeg_quality))])
    if not ok:
        raise SystemExit(f"无法编码预览图：{path}")
    return "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")


def _img_block(url: str) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": url}}


def _text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _call(client: OpenAI, model: str, messages: list[dict[str, Any]], timeout: float) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=False,
        extra_body={"enable_thinking": False},
        timeout=timeout,
    )
    return getattr(resp.choices[0].message, "content", "") or ""


def _run_interleaved(client, model, frames, question, timeout) -> str:
    content: list[dict[str, Any]] = []
    for direction, url in frames:
        content.append(_text_block(f"下面这张画面来自我的【{direction}】："))
        content.append(_img_block(url))
    content.append(_text_block(question))
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]
    return _call(client, model, messages, timeout)


def _run_labels_after(client, model, frames, question, timeout) -> str:
    content: list[dict[str, Any]] = []
    for _, url in frames:
        content.append(_img_block(url))
    order = "、".join(f"第{i}张来自{d}" for i, (d, _) in enumerate(frames, start=1))
    content.append(_text_block(f"以上按顺序是 {len(frames)} 张画面：{order}。{question}"))
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]
    return _call(client, model, messages, timeout)


def _run_separate_turns(client, model, frames, question, timeout) -> str:
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for direction, url in frames:
        messages.append(
            {
                "role": "user",
                "content": [
                    _text_block(f"这张画面来自我的【{direction}】。请只回复：收到。"),
                    _img_block(url),
                ],
            }
        )
        messages.append({"role": "assistant", "content": "收到"})
    messages.append({"role": "user", "content": question})
    return _call(client, model, messages, timeout)


def main() -> int:
    args = _build_arg_parser().parse_args()
    api_key = os.getenv(args.api_key_env)
    if not api_key:
        print(f"缺少 {args.api_key_env}。", file=sys.stderr)
        return 2

    raw_frames = [("左侧", args.left)]
    if args.center is not None:
        raw_frames.append(("正前方", args.center))
    raw_frames.append(("右侧", args.right))

    frames: list[tuple[str, str]] = []
    for direction, path in raw_frames:
        path = path.resolve()
        if not path.is_file():
            print(f"图片不存在：{path}", file=sys.stderr)
            return 2
        url = _preview_data_url(path, max_side=args.preview_max_side, jpeg_quality=args.jpeg_quality)
        frames.append((direction, url))
        print(f"[prepare] {direction:<4} {path.name}  data_url_len={len(url)}")

    client = OpenAI(api_key=api_key, base_url=args.base_url, timeout=args.timeout_seconds, max_retries=0)

    runners = {
        "interleaved": _run_interleaved,
        "labels_after": _run_labels_after,
        "separate_turns": _run_separate_turns,
    }
    modes = list(runners) if args.mode == "all" else [args.mode]
    for mode in modes:
        print("\n" + "=" * 60)
        print(f"模式：{mode}")
        print("=" * 60)
        try:
            answer = runners[mode](client, args.model, frames, args.question, args.timeout_seconds)
            print(answer.strip() or "(空)")
        except Exception as exc:  # noqa: BLE001 - 诊断脚本保留真实异常
            print(f"[error] {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
