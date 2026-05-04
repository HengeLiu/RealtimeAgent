# sdk-v89 Omni semantic_vad 主链路恢复

## 背景

真机联调发现，`sdk-v87` 到 `sdk-v88` 的 ASR 前置裁决虽然能挡住部分空段和误触发，但它把旁路 ASR 放到了 Omni Realtime 前面，带来三个问题：

1. 正常问答首响会被旁路 ASR 最终文本拖慢。
2. 空转写、背景音、附和声这类问题重复实现了 Omni `semantic_vad` 已经承担的职责。
3. “结束对话、安静、先这样”等自然指令更适合由模型理解后调用系统工具关闭连续窗口，而不是只靠 ASR 硬规则。

回声抑制仍然是端侧音频工程问题，不能交给 Omni 代替。当前版本只调整服务端连续对话主链路和关闭语义。

## 变更

1. `omni_realtime + realtime_semantic_vad` 不再强制降级为 `segment_turn`。预连接的 Omni 会话会使用真实服务端配置，让 Omni 官方 `semantic_vad` 负责是否自动响应。
2. 旁路 ASR 改为非阻塞辅助链路：
   - 默认只做日志、转写回填和调试观测。
   - 如果进入 Omni 前已经完成，可顺手处理停止指令、明显助手回声和明确视觉关键词。
   - 不再因为空 ASR、语气词或 ASR 等待超时阻塞 Omni。
3. 增加模型可见系统工具 `close_continuous_dialog`。模型识别到用户要求结束连续对话时调用该工具，SDK 会在当前回复播放完成后下发 `voice.dialog.close`。
4. 保留极轻系统硬保护：没有音频帧、没有 PCM 字节、极短异常段会直接丢弃并关闭端侧连续窗口。
5. 如果 Omni `semantic_vad` 返回没有自动响应，SDK 将本轮视为无有效用户请求，关闭连续窗口并恢复待命。

## 验证

已执行：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  -q
```

结果通过。

本轮尚未完成真实设备 A/B 对比。下一轮真机测试应对比：

1. 旧 ASR 前置策略与 `sdk-v89` Omni `semantic_vad` 直连策略的误触发率。
2. 用户说完到首段下行音频的首响延迟。
3. 背景音、助手回声和空段造成的 token 消耗。
4. 用户通过“结束对话、安静、先这样”自然结束连续窗口的成功率。
