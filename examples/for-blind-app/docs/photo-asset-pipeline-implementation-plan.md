# for-blind-app 自定义视觉功能开发计划

更新时间：2026-05-22

对应应用设计文档：[for-blind-app 自定义视觉功能设计](photo-asset-pipeline-design.md)。SDK 通用实现计划见 [SDK 照片资产处理链路开发计划](../../../agent-server/docs/internal/photo-asset-pipeline-implementation-plan.md)。

## 1. 实施原则

1. 应用层只实现业务 Task、自然语言参数提取、observation 历史和用户输出体验。
2. 照片上传、buffer、claim、模型视觉 append 复用 SDK。
3. 不能把 360 识别、万物监测、找物、红绿灯等业务兜底写进 SDK core。
4. Task `run()` 只返回启动结果，后台循环通过 TaskRuntime 推进。
5. 长周期视觉任务默认只把 observation / summary 返回给主模型，不自动 append 每帧原图。

## 2. 当前已落地范围

1. `custom_visual_task`：提供自定义视觉 Task 骨架。
2. `custom_visual_task_query`：支持查询 observation 历史。
3. `VisualObservation` / `TaskSignal`：后台采样后生成可追问结果和触发信号。
4. `capture_photo`：返回 `append_to_agent`，适合短周期看图。
5. 内部图片解读类 Tool：返回 `internal_only`，避免主模型自动看到原图。
6. Vision realtime-video：用户说话期间可自动采集当前画面，模型请求前批量 append。

## 3. 阶段计划

### Phase A：自然语言参数提取

目标：用户一句话定义拍摄、监测或定时任务。

关键任务：

1. 定义 `VisualTaskPlan` schema。
2. 为 360 识别、定时任务、万物监测分别提供参数抽取 prompt。
3. 从自然语言中提取采样间隔、持续时间、最大帧数、目标对象、触发条件、输出方式和停止条件。
4. 对缺省值做应用层约束，例如默认 `interval_seconds=1`、默认 `direction=front`。
5. 对高风险参数做限制，例如最长持续时间、最高采样频率、最大输出频率。

测试范围：

1. “帮我转一圈，看一下房间里都有什么” -> `scene_scan`。
2. “每天中午12点提醒我吃药” -> `scheduled_reminder`。
3. “接下来5分钟帮我看水有没有开” -> `object_monitor`。
4. 模糊表达能补默认值，并在必要时追问。

### Phase B：360 识别

目标：支持短时间多帧采样和多轮环境理解。

关键任务：

1. 使用 SDK 请求 `sensor.rgb` 多帧采样。
2. 按 `captured_at_ms` / `sequence_index` 组织 observation。
3. analyzer 输出场景、物品、方位和不确定项。
4. 生成适合盲人用户听取的简短语音总结。
5. 后续追问从 observation 历史读取，例如“哪个文件在左边”。

测试范围：

1. 房间环境识别。
2. 桌面文件和设备识别。
3. 仓库货架检查。
4. 多帧顺序和 direction 进入 observation。

### Phase C：万物监测

目标：支持条件触发、循环和停止条件。

关键任务：

1. 按计划周期采样。
2. analyzer 输出结构化结果。
3. 判断 `trigger.condition_text` 是否满足。
4. 条件满足时通过 `context.output` 或 TaskSignal 提醒。
5. 未满足时继续循环，直到取消、超时或达到最大帧数。
6. 对重复提醒做节流，避免连续播报。

测试范围：

1. 检测他人情绪。
2. 检测公交车出现。
3. 检测水是否开。
4. 宠物靠近门提醒。
5. 用户取消任务后停止采样。

### Phase D：定时任务

目标：支持会议提醒、吃药提醒和视觉历史总结。

关键任务：

1. 将自然语言时间表达转换成 scheduler 可执行计划。
2. 到点触发提醒类任务。
3. 到点触发历史 observation 总结类任务。
4. 支持语音和震动输出。
5. 任务触发、完成和失败写入 runs。

测试范围：

1. 每周一上午9点会议提醒。
2. 每天中午12点吃药提醒。
3. 每天晚上8点总结看到的物品变化。

### Phase E：已有视觉任务接入 observation 历史

目标：让找物、红绿灯等已有长周期视觉任务逐步对齐自定义视觉任务的数据形态。

关键任务：

1. 找物任务输出 `VisualObservation`。
2. 红绿灯任务输出 `VisualObservation` 或 `TaskSignal`。
3. peer video 保持端侧主链路，不强制回传每帧原图。
4. 用户后续追问可以读取最近 observation。

测试范围：

1. 现有找物测试不回归。
2. 现有红绿灯测试不回归。
3. observation 查询能返回任务摘要。

## 4. 数据和提示词

需要新增或整理：

1. `VisualTaskPlan` schema。
2. `VisualObservation` schema。
3. 参数抽取 prompt。
4. observation 总结 prompt。
5. 条件判断 prompt。
6. 任务查询 Tool 的输出格式。

prompt 设计原则：

1. 参数抽取优先结构化输出。
2. 不确定时让模型标记 `needs_clarification`，不要让代码猜业务意图。
3. 输出面向盲人用户，语音内容要短、明确、可行动。
4. 不把“如何使用功能”的说明播给用户。

## 5. 验收命令

应用能力：

```bash
uv run python -m pytest examples/for-blind-app/app-tests -q
uv run python -m pytest examples/for-blind-app/replay-tests -q
```

相关 SDK 回归：

```bash
uv run python -m pytest protocol/protocol-tests -q
uv run python -m pytest agent-server/protocol-tests -q
uv run python -m pytest devices/python/protocol-tests -q
```

端侧联调建议：

```bash
uv run realtime-agent.server.run --app-name for-blind-app
uv run realtime-agent.web.open --serve
uv run --extra gui python -m realtime_agent_python_phone_mock --config examples/dev-support/devices/python-phone/phone.preview.yaml
```

观察点：

1. `examples/for-blind-app/agent-server/runs/<user>/<device>/assets.jsonl`
2. `task-signals.jsonl`
3. `tool-events.jsonl`
4. `model-request.json`
5. `agent-events.jsonl`

## 6. 后续补齐

1. 将自然语言参数抽取从简单规则升级为模型结构化输出。
2. 为 360 识别和万物监测补图片 fixture / replay 数据。
3. 为定时任务补 scheduler 时间推进测试。
4. 补真实端侧联调记录，区分浏览器模拟、Python phone、iOS 和 ESP32 验证层级。
5. 把找物、红绿灯任务结果逐步适配到 observation 历史。
