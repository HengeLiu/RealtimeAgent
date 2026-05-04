# sdk-v95 模型自决视觉拍照链路

本轮对应对外 SDK 版本：`sdk-v95`。

## 背景

真机连续对话调试中，SDK 曾通过旁路 ASR 关键词做视觉意图裁决，只在命中“看看、前面、画面、障碍物”等词时才触发并上传照片。这个策略虽然能减少无关看图，但它和 Omni 模型本身的语义理解形成了两套意图系统：SDK 规则可能误判、漏判，也会让业务侧难以解释为什么模型没有拿到照片。

本轮将视觉问答统一收敛到模型工具调用：模型理解用户是否需要当前画面；需要时调用 SDK 内置 `capture_photo`，SDK 完成真实抓拍并把照片交回当前模型链路。

## 主要改动

1. 默认模型工具面重新暴露 `capture_photo`，并标记为全局系统工具；即使 Skill 白名单激活，也不会屏蔽用户请求当前画面的基础能力。
2. 移除语音运行时中的视觉关键词前置裁决。旁路 ASR 仍可在已就绪时处理停止对话和明显助手回声，但不再判断视觉意图。
3. Omni Realtime function calling 调用 `capture_photo` 后，SDK 会读取工具产生的图片资产，并通过 `append_video(...)` 追加到同一条 Realtime 会话，再触发后续响应。
4. 普通 Agent/TTS 链路恢复 `capture_photo` 后的图片解读主链路：工具输出完成后切到多模态图片回答，而不是只把图片路径作为普通 JSON 返回给模型。
5. 系统提示词明确要求：需要当前视觉信息时调用 `capture_photo`；普通聊天、时间天气、记忆维护、导航规划等不需要当前画面时不要调用。

## 验证

已执行：

```bash
uv run --with pytest --python 3.11 python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py::AgentCoreTestCase::test_tool_registry_exposes_expected_model_facing_tools \
  openaiglass-sdk/tests/unit/test_agent_core.py::AgentCoreTestCase::test_skill_runtime_read_skill_activates_session_and_filters_tools \
  openaiglass-sdk/tests/unit/test_voice_runtime.py::VoiceRuntimeTestCase::test_dashscope_omni_realtime_appends_capture_photo_tool_image \
  openaiglass-sdk/tests/unit/test_voice_runtime.py::VoiceRuntimeTestCase::test_voice_turn_intent_does_not_preclassify_visual_query
```

结果：`4 passed`。

## 业务侧影响

1. 业务 Skill 不需要实现“看图意图识别”，也不要注册同名 `capture_photo`。
2. 视觉类提示词应描述业务目标，不要要求业务代码先拍照再问模型。
3. 联调时观察 `Omni Realtime 工具调用请求 tool_name=capture_photo` 和 `Omni Realtime 已追加 capture_photo 工具图片`，即可确认模型自决视觉链路生效。
