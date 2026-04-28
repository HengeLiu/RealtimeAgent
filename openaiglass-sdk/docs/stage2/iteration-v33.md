# iteration-v33：SDK v34 语音结束自动照片

## 本轮目标

修复真实眼镜视觉问答中“模型说正在拍照，但实际解读的是几秒前镜头位置”的体验问题，将视觉照片采集从模型主动触发改为语音段结束后 SDK 自动异步触发。

本轮对应对外 SDK 版本：`sdk-v34`。

## 问题原因

旧链路中，模型需要先输出或决策到 `capture_photo` 工具调用，服务端才向眼镜发送 `sensor.camera.capture`。真实播放和抓拍并行时，用户听到“我拍一张看一下”时，抓拍可能已经完成，容易误以为照片应该对应播报时刻的镜头方向。

## 主要改动

1. `VoiceRuntime` 在每个语音段结束并进入服务端处理后，立即后台启动一次 `utterance_finished` 抓拍。
2. 新增 `UtterancePhotoStore`，用于保存语音轮次、后台抓拍状态、上传结果和错误信息。
3. 新增模型可见工具 `get_latest_utterance_photo`，只读取本轮语音结束后的自动照片，必要时等待数秒上传完成。
4. `capture_photo` 仍作为 SDK 内部兼容工具保留，但不再注册到模型工具列表。
5. 图片解读主链路继续复用现有多模态 follow-up，不要求业务能力自行上传或管理图片。

## 当前边界

1. 自动照片通过控制连接中的 `sensor.camera.capture` / `sensor.camera.captured` 完成，仍不是独立二进制图片通道。
2. 每个语音段都会尝试后台抓拍；如果设备没有相机网关或端侧抓拍失败，语音主链路继续执行，只有模型调用 `get_latest_utterance_photo` 时才会看到结构化错误。
3. 业务 Skill 不应再把 `capture_photo` 写入 `allowed_tools`。需要视觉问答时使用 `get_latest_utterance_photo`。
4. SDK 只在内存中保留最近若干轮自动照片记录，避免非视觉对话持续抓拍导致内存无界增长。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py \
  openaiglass-sdk/tests/unit/test_sdk_phase_two.py::test_build_agent_facade_from_sdk_preloads_agent_resources \
  openaiglass-sdk/tests/unit/test_sdk_phase_two.py::test_openai_glasses_sdk_registers_skill_runtime -q
```
