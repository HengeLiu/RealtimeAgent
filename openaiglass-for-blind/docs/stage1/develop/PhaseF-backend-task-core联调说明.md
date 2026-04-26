# Phase F backend-task-core 联调说明

## 1. 目标

本说明用于验证 Phase F 已完成以下最小闭环能力：

1. `timer_task` 可创建、查询、取消、完成。
2. `TaskEvent` 会进入会话上下文。
3. 高优先级通知可以抢占当前低优先级播报。
4. 眼镜端会回传结构化播放终态：
   - `completed`
   - `interrupted`
   - `failed`

联调重点观察：

1. 任务是否真正进入 `running -> completed/cancelled`
2. 任务完成后是否同时具备“会话回流”和“通知编排”两条链路
3. 高优先级抢占后，旧流是否被显式打断
4. `/api/runtime/devices` 中是否出现 `last_playback_state / reason`

## 2. 自动化验证

优先执行自动化回归：

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest \
  server.test.unit.test_voice_runtime \
  server.test.unit.test_task_event_runtime \
  server.test.unit.test_backend_task_core \
  server.test.unit.test_agent_core \
  server.test.integration.test_control_register_flow \
  server.test.integration.test_agent_phase_e_flow -v
```

预期结果：

1. `test_backend_task_core.*` 通过
2. `test_task_event_runtime.*` 通过
3. `test_voice_runtime.*` 通过
4. `test_control_register_flow.*` 通过

## 3. 服务端启动

在仓库根目录执行：

```bash
export DASHSCOPE_API_KEY="<your-api-key>"
export DEVICE_TOKEN_MAP="glass-001=pair-demo-token"
export LOG_FILE="logs/server.log"
export AGENT_MODEL_NAME="qwen3.6-plus"
export VOICE_MODEL_NAME="qwen3.5-omni-plus"
PYTHONPATH=openaiglass-sdk/server-python uv run python -m app.main --host 0.0.0.0 --port 8765
```

说明：

1. 若只做 Phase F 联调，`AMap` 不需要额外配置。
2. 当前服务端运行态观察接口为：
   - `/api/runtime/devices`
   - `/api/agent/session?session_id=<session_id>`
3. `backend-task-core` 当前仍为内存态，重启服务后任务不会保留。

## 4. 眼镜端启动

在眼镜端工程目录执行：

```bash
idf.py flash monitor
```

前提：

1. 眼镜端配置中的 `GLASS_SERVER_WS_URI` 需指向 `ws://<server-ip>:8765/ws/control`
2. 眼镜端需使用与服务端一致的 `pair_token`

说明：

1. 当前环境若没有 `idf.py`，无法在本机完成固件编译验证。
2. 若仅做服务端协议联调，可先复用测试客户端，不强依赖真机。

## 5. 建议联调步骤

### 5.1 计时器创建与完成

1. 说：`帮我计时 5 秒`
2. 观察：
   - 服务端日志出现 `task.created / task.started`
   - 5 秒后出现 `task.completed`
   - 任务事件进入会话上下文
   - 设备收到播报

### 5.2 计时器查询

1. 先创建一个较长计时器，例如：`帮我计时 1 分钟`
2. 再说：`还有多久`
3. 观察：
   - `query_task_status` 被调用
   - 返回的任务状态为 `running`

### 5.3 计时器取消

1. 创建一个较长计时器
2. 说：`取消计时`
3. 观察：
   - 服务端出现 `task.cancelled`
   - 后续不再出现该任务的 `task.completed`

### 5.4 高优先级通知抢占

1. 先触发一条较长的普通播报，让设备进入播放态。
2. 再触发一条高优先级通知。
3. 观察：
   - 服务端下发 `actuator.audio.interrupt`
   - 眼镜端日志出现 `收到 actuator.audio.interrupt`
   - 旧流被结束
   - 新流开始播放

## 6. 运行态观察点

联调时建议持续查看：

```bash
curl "http://127.0.0.1:8765/api/runtime/devices"
```

重点字段：

1. `voice_sessions.<device_id>.state`
2. `voice_sessions.<device_id>.reply_stream_id`
3. `voice_sessions.<device_id>.last_playback_stream_id`
4. `voice_sessions.<device_id>.last_playback_state`
5. `voice_sessions.<device_id>.last_playback_reason`

预期：

1. 正常播报结束后：
   - `last_playback_state=completed`
   - `last_playback_reason=device_finished` 或设备上报的完成原因
2. 被抢占后：
   - `last_playback_state=interrupted`
   - `last_playback_reason=higher_priority_notification` 或 `interrupt_requested`
3. 播放失败后：
   - `last_playback_state=failed`
   - `last_playback_reason` 为设备侧失败原因

## 7. 会话上下文观察点

可以通过：

```bash
curl "http://127.0.0.1:8765/api/agent/session?session_id=<session_id>"
```

重点查看：

1. `messages`
   - 是否出现 `kind=task_notification`
2. `artifacts`
   - 是否出现 `artifact_type=task_event`
3. `tasks`
   - 是否出现对应 `TaskRef`

## 8. 故障排查

### 8.1 创建了计时器但没有完成事件

优先检查：

1. 服务端是否被提前关闭
2. `duration_seconds` 是否合法
3. `backend-task-core` 是否被错误重建

### 8.2 任务完成了但没有播报

优先检查：

1. 是否被通知协调器去重
2. 是否被排队等待前序播报
3. `agent-core` 回流后是否生成了新的通知申请

### 8.3 发生抢占但设备没有立即停播

优先检查：

1. 服务端是否发出了 `actuator.audio.interrupt`
2. 眼镜端是否收到了该消息
3. 眼镜端播放任务是否已经进入阻塞读取阶段

说明：

1. 当前实现已经是显式打断协议，但设备侧停止速度仍受播放任务轮询周期影响，不是硬实时抢断。

### 8.4 服务端看不到结构化终态

优先检查：

1. 眼镜端是否回了 `actuator.audio.state`
2. `stream_id` 与当前播放流是否一致
3. `session_id` 是否匹配当前会话

## 9. 当前限制

1. 当前只落地了 `timer_task`，还没有 `navigation_task` 等复杂模板。
2. 当前 `backend-task-core` 仍是内存态，不具备重启恢复能力。
3. 当前没有专门的 Phase F 独立联调脚本，主要依赖自动化测试、服务端运行态接口与真机口头触发。
4. 当前环境若没有 `idf.py`，需要在具备 ESP-IDF 环境的机器上完成真机编译和刷机。
