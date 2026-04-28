# iteration-v32：SDK v33 视觉拍照播报去重

## 本轮目标

修复真实眼镜视觉问答中，图片解读内容已经播出后，又播报“好的，你保持别动，我拍一张帮你看”的重复和倒序体验问题。

本轮对应对外 SDK 版本：`sdk-v33`。

## 问题原因

视觉链路中存在两个播报来源：

1. 模型在调用 `capture_photo` 前通过普通流式文本输出拍照提示，例如“我来拍张照，看看你面前有什么”。
2. SDK 在观察到 `capture_photo` 工具调用事件时，又通过 `progress_callback` 注入固定播报“好的，你保持别动，我拍一张帮你看。”

这两路会进入不同的 TTS/播放请求，真实设备上可能发生排队倒序，导致用户先听到图片解读结果，随后又听到拍照提示。

## 主要改动

1. `StreamedAgentTurnObserver` 不再在 `capture_photo` 工具调用事件上注入固定中间播报。
2. 视觉链路仍保留模型流式文本增量和图片解读主链路流式输出。
3. 调整 agent-core 单测，验证拍照工具调用不会额外产生 SDK 固定 progress 播报。

## 当前边界

1. 模型仍可能自行输出“我来拍张照”这类文本，这是 Agent 回复的一部分，会按普通流式 TTS 播放。
2. 后续如果要更强约束，可在模型 prompt 或 stream observer 中对“工具调用前文本”做策略化过滤；本轮只去掉 SDK 额外注入的重复固定播报。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py -q
```
