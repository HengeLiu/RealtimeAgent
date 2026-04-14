# Phase D agent-core 联调说明

## 1. 目标

本说明用于验证 Phase D 的最小 `agent-core` 已接入当前语音主链路，重点观察：

1. 一轮语音转写文本是否会进入 `AgentFacade`。
2. `agent-core` 是否能返回统一 `AgentTurnResult`。
3. `voice-runtime` 是否只负责播报，不再直接承担对话决策。

## 2. 自动化验证

在仓库根目录执行：

```bash
bash script/run_phase_d_tests.sh
```

预期结果：

1. 新增 `unit.test_agent_core.*` 全部通过。
2. `integration.test_voice_dialog_flow.*` 通过。
3. 总体测试结果为 `OK`。

## 3. 服务端启动

在仓库根目录执行：

```bash
export DASHSCOPE_API_KEY="<your-api-key>"
export DEVICE_TOKEN_MAP="glass-001=pair-demo-token"
PYTHONPATH=server/src uv run python -m app.main --host 0.0.0.0 --port 8765
```

## 4. 联调观察点

### 4.1 服务端日志

应看到：

1. `ASR 转写结果: ...`
2. `Agent 输出: action=... traces=[...]`
3. `Agent 最终回复: ...`
4. `语音回复已准备: ... transcript_artifact=...`

说明：

1. 若看到第 1 条和第 4 条，但看不到第 2 条、第 3 条，说明 `agent-core` 未正确接入。
2. 若看到 `agent-core 运行失败`，优先检查 `DASHSCOPE_API_KEY` 和模型兼容性。

### 4.2 运行态接口

执行：

```bash
curl http://127.0.0.1:8765/api/runtime/devices
```

预期返回：

1. `voice_sessions.glass-001.state` 最终回到 `listening`
2. `voice_sessions.glass-001.audio_connection_online=true`

## 5. 当前限制

1. 当前联调说明只覆盖 Phase D，不包含 Skill / MCP / Task 的真实联调。
2. 若需要验证真实 Tool 决策，建议后续在 Phase E 增加专门的脚本化对话样例。
