from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml
from openai import OpenAI


DEFAULT_CONFIG = "examples/device_demo/agent-server/server.yaml"
DEFAULT_IMAGES = [
    "testdata/image-sample/基辅美食.jpeg",
    "testdata/image-sample/刚子等饭吃.jpeg",
    "testdata/image-sample/刚子看电脑.jpeg",
]


def main() -> int:
    """测试 VL 模型是否能在多轮消息中保留并引用较早提交的图片。

    功能：按“提交第一张图 -> 回复收到 -> 提交第二张图 -> 回复收到 -> 提交第三张图
    -> 回复收到 -> 询问第一张图内容”的顺序直接调用 OpenAI-compatible Chat
    Completions。
    主要逻辑：每次请求都携带当前 messages 历史；前三轮把图片作为 `image_url`
    content block 追加到用户消息中，第四轮不新增图片，只询问第一张图内容，并用
    streaming 记录首个文本 token 延迟。
    参数：通过命令行指定图片、模型、base_url、API Key 环境变量和输出路径。
    返回值：进程退出码，0 表示脚本完成并写入诊断报告。
    异常情况：缺少 API Key、图片文件或 provider 调用失败时，会在报告中记录错误。

    注意：OpenAI-compatible Chat Completions 通常是无状态 HTTP 调用。本脚本验证的是
    “历史 messages 中分多轮携带图片，模型能否在后续问题中引用第一张图”，不是验证
    provider 是否已经在服务端提前完成图片预计算。
    """

    args = _parse_args()
    config = _load_config(Path(args.config)) if args.config else {}
    vision_config = ((config.get("agent") or {}).get("vision") or {}) if isinstance(config, dict) else {}

    api_key = os.getenv(args.api_key_env) or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit(f"{args.api_key_env} or OPENAI_API_KEY is required")

    base_url = args.base_url or _default_base_url(vision_config)
    model = args.model or str(vision_config.get("model") or "qwen3.6-flash")
    extra_body = _extra_body(args, vision_config)
    image_paths = [Path(item).expanduser().resolve() for item in args.images]
    _validate_images(image_paths)

    output_path = Path(args.output).expanduser().resolve() if args.output else _default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=float(args.timeout_seconds),
        max_retries=int(args.max_retries),
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": args.system_prompt}]
    report: dict[str, Any] = {
        "model": model,
        "base_url": base_url,
        "config": str(Path(args.config).expanduser().resolve()) if args.config else "",
        "api_key_env": args.api_key_env,
        "extra_body": extra_body,
        "timeout_seconds": args.timeout_seconds,
        "max_retries": args.max_retries,
        "images": [str(path) for path in image_paths],
        "image_preview": {
            "max_side": args.preview_max_side,
            "jpeg_quality": args.jpeg_quality,
        },
        "warning": (
            "OpenAI-compatible Chat Completions is usually stateless; final requests must carry "
            "prior image blocks in message history if the model needs to see them."
        ),
        "steps": [],
    }

    for index, image_path in enumerate(image_paths, start=1):
        image_data = _preview_image_data_url(
            image_path,
            max_side=int(args.preview_max_side),
            jpeg_quality=int(args.jpeg_quality),
        )
        user_message = _image_ack_message(index=index, image_data=image_data)
        result = _call_once(
            client=client,
            model=model,
            messages=[*messages, user_message],
            extra_body=extra_body,
        )
        report["steps"].append(
            {
                "step": f"image_{index}_ack",
                "image_path": str(image_path),
                "image_preview": image_data["metadata"],
                "request_message_count": len(messages) + 1,
                "request_image_block_count": _count_image_blocks([*messages, user_message]),
                **result,
            }
        )
        if not result.get("ok"):
            break
        messages.extend([user_message, {"role": "assistant", "content": result.get("content") or ""}])

    if all(step.get("ok") for step in report["steps"]) and len(report["steps"]) == len(image_paths):
        final_messages = [*messages, {"role": "user", "content": args.question}]
        if args.final_without_images:
            final_messages = _drop_image_blocks(final_messages)
        final_result = _call_streaming_once(
            client=client,
            model=model,
            messages=final_messages,
            extra_body=extra_body,
        )
        report["steps"].append(
            {
                "step": "ask_first_image",
                "question": args.question,
                "final_without_images": bool(args.final_without_images),
                "request_message_count": len(final_messages),
                "request_image_block_count": _count_image_blocks(final_messages),
                **final_result,
            }
        )

    report["summary"] = _summarize(report["steps"])
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(report, output_path=output_path)
    return 0 if report["summary"]["all_ok"] else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证 VL 模型能否在多轮图片提交后回答第一张图片内容。")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="读取默认模型配置的 server.yaml 路径。")
    parser.add_argument("--images", nargs=3, default=DEFAULT_IMAGES, help="按顺序提交的三张图片路径。")
    parser.add_argument("--model", default="", help="覆盖配置中的视觉模型名。")
    parser.add_argument("--base-url", default="", help="覆盖 OpenAI-compatible base_url。")
    parser.add_argument("--api-key-env", default="DASHSCOPE_API_KEY", help="API Key 环境变量名。")
    parser.add_argument("--timeout-seconds", type=float, default=60.0, help="单次请求超时时间。")
    parser.add_argument("--max-retries", type=int, default=0, help="OpenAI SDK 重试次数。")
    parser.add_argument("--enable-thinking", action="store_true", help="不传 enable_thinking=false。")
    parser.add_argument(
        "--preview-max-side",
        type=int,
        default=512,
        help="图片预览压缩后的最大边长；设为 0 表示使用原图。",
    )
    parser.add_argument("--jpeg-quality", type=int, default=80, help="预览图 JPEG 编码质量。")
    parser.add_argument("--output", default="", help="输出 JSON 路径；默认写入 runs/diagnostics。")
    parser.add_argument(
        "--system-prompt",
        default="你是一个严格的中文视觉理解测试助手。收到图片确认轮次时只回复“收到”。回答问题时只基于已提供的图片内容。",
        help="测试用 system prompt。",
    )
    parser.add_argument(
        "--question",
        default="请回答：第一张图片里有什么内容？请只描述第一张图片，不要描述第二张或第三张。",
        help="第四轮用户问题。",
    )
    parser.add_argument(
        "--final-without-images",
        action="store_true",
        help="负向对照：第四轮请求移除历史 image_url，只保留文本历史。Chat Completions 下通常不应依赖此模式。",
    )
    return parser.parse_args()


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _default_base_url(vision_config: dict[str, Any]) -> str:
    provider = str(vision_config.get("provider") or "")
    if provider == "dashscope-compatible":
        return os.getenv("OPENAI_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    return os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"


def _extra_body(args: argparse.Namespace, vision_config: dict[str, Any]) -> dict[str, Any]:
    provider = str(vision_config.get("provider") or "")
    if args.enable_thinking:
        return {}
    if provider in {"dashscope-compatible", ""}:
        return {"enable_thinking": False}
    return {}


def _validate_images(paths: list[Path]) -> None:
    for path in paths:
        if not path.exists():
            raise SystemExit(f"image not found: {path}")
        if not path.is_file():
            raise SystemExit(f"image is not a file: {path}")


def _image_ack_message(*, index: int, image_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": f"这是第{index}张图片。请只回复：收到。"},
            {"type": "image_url", "image_url": {"url": image_data["data_url"]}},
        ],
    }


def _preview_image_data_url(path: Path, *, max_side: int, jpeg_quality: int) -> dict[str, Any]:
    """生成用于 VL 探针的压缩预览图 data URL。

    功能：读取原图，按最大边长等比缩放，并编码为 JPEG data URL。
    主要逻辑：`max_side<=0` 时保留原图；否则使用 OpenCV 的 AREA 插值缩小图片，
    降低网络传输、视觉编码和 prefill 成本。
    参数：`path` 是图片路径，`max_side` 是预览图最大边长，`jpeg_quality` 是 JPEG 质量。
    返回值：包含 data URL 和压缩前后尺寸、字节数的字典。
    异常情况：图片解码或编码失败时终止脚本。
    """

    source_bytes = path.read_bytes()
    if max_side <= 0:
        return {
            "data_url": _data_url(source_bytes, mime_type=mimetypes.guess_type(path.name)[0] or "image/jpeg"),
            "metadata": {
                "source_bytes": len(source_bytes),
                "preview_bytes": len(source_bytes),
                "max_side": 0,
                "jpeg_quality": None,
                "resized": False,
            },
        }

    image_array = np.frombuffer(source_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"failed to decode image: {path}")

    height, width = image.shape[:2]
    scale = min(1.0, float(max_side) / float(max(width, height)))
    if scale < 1.0:
        target_width = max(1, round(width * scale))
        target_height = max(1, round(height * scale))
        image = cv2.resize(image, (target_width, target_height), interpolation=cv2.INTER_AREA)
    else:
        target_width = width
        target_height = height

    quality = max(1, min(100, int(jpeg_quality)))
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise SystemExit(f"failed to encode preview image: {path}")
    preview_bytes = encoded.tobytes()
    return {
        "data_url": _data_url(preview_bytes, mime_type="image/jpeg"),
        "metadata": {
            "source_bytes": len(source_bytes),
            "preview_bytes": len(preview_bytes),
            "source_size": {"width": width, "height": height},
            "preview_size": {"width": target_width, "height": target_height},
            "max_side": max_side,
            "jpeg_quality": quality,
            "resized": scale < 1.0,
        },
    }


def _data_url(data: bytes, *, mime_type: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _call_once(
    *,
    client: OpenAI,
    model: str,
    messages: list[dict[str, Any]],
    extra_body: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if extra_body:
            kwargs["extra_body"] = extra_body
        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        message = choice.message
        return {
            "ok": True,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "finish_reason": getattr(choice, "finish_reason", ""),
            "content": getattr(message, "content", "") or "",
            "usage": _usage_dict(getattr(response, "usage", None)),
        }
    except Exception as exc:  # noqa: BLE001 - 诊断脚本需要保留真实 provider 异常
        return {
            "ok": False,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def _call_streaming_once(
    *,
    client: OpenAI,
    model: str,
    messages: list[dict[str, Any]],
    extra_body: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    first_token_latency_ms: int | None = None
    content_parts: list[str] = []
    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if extra_body:
            kwargs["extra_body"] = extra_body
        stream = client.chat.completions.create(**kwargs)
        finish_reason = ""
        for item in stream:
            choice = item.choices[0]
            finish_reason = getattr(choice, "finish_reason", "") or finish_reason
            delta = getattr(choice, "delta", None)
            token = getattr(delta, "content", None) if delta is not None else None
            if token:
                if first_token_latency_ms is None:
                    first_token_latency_ms = round((time.perf_counter() - started) * 1000)
                content_parts.append(str(token))
        return {
            "ok": True,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "first_token_latency_ms": first_token_latency_ms,
            "finish_reason": finish_reason,
            "content": "".join(content_parts),
        }
    except Exception as exc:  # noqa: BLE001 - 诊断脚本需要保留真实 provider 异常
        return {
            "ok": False,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "first_token_latency_ms": first_token_latency_ms,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def _usage_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return dict(usage.model_dump())
    if isinstance(usage, dict):
        return dict(usage)
    return {}


def _count_image_blocks(messages: list[dict[str, Any]]) -> int:
    count = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            count += sum(1 for block in content if isinstance(block, dict) and block.get("type") == "image_url")
    return count


def _drop_image_blocks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            cleaned.append(dict(message))
            continue
        text_blocks = [block for block in content if not (isinstance(block, dict) and block.get("type") == "image_url")]
        cleaned.append({**message, "content": text_blocks or "[图片已在负向对照中移除]"})
    return cleaned


def _summarize(steps: list[dict[str, Any]]) -> dict[str, Any]:
    ok_steps = [step for step in steps if step.get("ok")]
    return {
        "total_steps": len(steps),
        "ok_steps": len(ok_steps),
        "all_ok": len(steps) == 4 and len(ok_steps) == 4,
        "latency_ms": {
            step["step"]: step.get("first_token_latency_ms", step.get("elapsed_ms")) for step in steps
        },
        "final_answer": (steps[-1].get("content") if steps else "") or "",
    }


def _default_output_path() -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    return Path("runs/diagnostics") / f"vl-incremental-image-context-{stamp}.json"


def _print_summary(report: dict[str, Any], *, output_path: Path) -> None:
    print(f"output={output_path}")
    print(f"model={report['model']} base_url={report['base_url']}")
    for step in report["steps"]:
        if step.get("ok"):
            if step["step"] == "ask_first_image":
                print(
                    f"{step['step']}: ok first_token_latency_ms={step.get('first_token_latency_ms')} "
                    f"image_blocks={step.get('request_image_block_count')} content={step.get('content')!r}"
                )
                continue
            print(
                f"{step['step']}: ok elapsed_ms={step.get('elapsed_ms')} "
                f"image_blocks={step.get('request_image_block_count')} content={step.get('content')!r}"
            )
        else:
            print(
                f"{step['step']}: error elapsed_ms={step.get('elapsed_ms')} "
                f"{step.get('error_type')}: {step.get('error')}"
            )


if __name__ == "__main__":
    raise SystemExit(main())
