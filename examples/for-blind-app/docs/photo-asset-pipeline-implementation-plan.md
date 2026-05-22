# 照片资产处理链路重构开发计划

更新时间：2026-05-22

对应设计文档：[照片资产处理链路重构设计](photo-asset-pipeline-design.md)。

## 1. 实施原则

1. 先协议资产和测试，再改运行时。
2. 先兼容扩展，不删除现有事件名和 stream 帧格式。
3. 新语义落地时同步清理不符合架构的旧自动路径；协议兼容只限事件名、可选字段和旧端侧忽略未知字段。
4. 每一阶段都要能独立回滚。
5. 不能把业务视觉任务逻辑放进 SDK core；SDK core 只提供照片资产、buffer、append adapter 和协议能力。

## 2. 阶段划分

### Phase 0：协议变更预备

目标：冻结协议变更范围，先让协议资产和测试表达目标状态。

改动范围：

1. 更新 `protocol/docs/protocol.md`，补充 `sensor.rgb` metadata 扩展。
2. 更新 `protocol/data/fixtures/events/stream-open-requested.json`，增加照片采集字段样例。
3. 更新 `protocol/data/fixtures/streams/rgb-header.json`，增加照片 metadata 样例。
4. 必要时补充 invalid fixture，确保控制 payload 仍不能携带媒体 bytes。
5. 更新 `protocol/protocol-tests/`，覆盖新字段可解析、旧 fixture 仍通过。

验收命令：

```bash
uv run python -m pytest protocol/protocol-tests -q
uv run python -m pytest -m protocol_spec -q
```

退出标准：

1. 协议文档、schema / fixture 和 P0 测试一致。
2. 新字段全部是可选字段。
3. 不新增 `photo.*` 事件，不改变 stream chunk 二进制格式。

### Phase 1：Server SDK PhotoAsset 与 TurnPhotoBuffer

目标：在 server 内建立统一照片资产生命周期。

建议新增或改造模块：

```text
agent-server/realtime_agent/asset/
  photo_asset.py
  turn_buffer.py
  service.py
```

关键任务：

1. 定义 `PhotoAsset`、`PhotoAssetClaim`。
2. 引入 `TurnPhotoBuffer`，按 `user_id + session_id + turn_id` 管理内存照片。
3. `AssetService.store_chunk()` 对 `sensor.rgb` 生成 PhotoAsset 并放入 buffer。
4. 磁盘写入改为异步归档；主链路先拿到内存资产引用。
5. `ttl_seconds` 只控制 buffer 有效期，不能控制 runs 归档。
6. turn 正常完成、失败、用户打断时清空当前 turn buffer。

测试范围：

1. `sensor.rgb` 上传后能进入 buffer。
2. `ttl_seconds` 到期后不能被业务 claim。
3. turn 结束会清空未消费资产。
4. 异步落盘失败不阻断 buffer 消费，但会记录错误。

建议命令：

```bash
uv run python -m pytest agent-server/protocol-tests -q
uv run python -m pytest -m sdk -q
```

### Phase 2：业务消费 claim 与 Tool 视觉资产语义

目标：明确每张照片只能被一个业务消费路径 claim，且 Tool 返回资产是否 append 给主模型必须显式声明。

关键任务：

1. 在 SDK 结果结构中新增视觉资产描述，不再把 `ToolResult.assets` 作为视觉 append 通道。
2. 引入 `visibility=append_to_agent | internal_only`。
3. 移除 `ModelMessageManager` 对 `ToolResult.assets` 的自动 append 依赖。
4. `capture_photo` 返回 `append_to_agent`。
5. `interpret_image` / `interpret_current_view` 返回 `internal_only`，主模型只看到文本结果。
6. 记录 claim 事件到 `agent-events.jsonl` 或 `assets.jsonl`，便于排障。

测试范围：

1. `append_to_agent` 会把图片和工具文本一起 append 给主模型。
2. `internal_only` 不会把原图 append 给主模型。
3. 同一资产不能被两个业务消费者重复 claim。
4. runs 读取不改变 claim 状态。

建议命令：

```bash
uv run python -m pytest agent-server/protocol-tests -q
uv run python -m pytest examples/for-blind-app/app-tests -q
```

### Phase 3：ModelVisualAppender

目标：把 Omni 和 Vision/VL 的图片 append 差异从 Tool / Task 中抽出来。

建议新增模块：

```text
agent-server/realtime_agent/agent_core/visual/
  appender.py
  omni_appender.py
  vl_appender.py
```

关键任务：

1. 定义 `ModelVisualAppender` 接口。
2. 实现 `OmniVisualAppender`：图片上传后即时 append 到 Realtime provider。
3. 实现 `VlVisualAppender`：turn 结束前批量组装当前 turn 图片和说明文本。
4. 把现有 `agent_core/multimodal/messages.py` 的工具资产 append 逻辑迁到 appender。
5. 把 `agent_core/omni.py` 中 visual sampler 的 provider append 封到 appender。

Vision/VL 批量 append 细分任务：

1. 定义 `flush_turn_assets(context)` 的输入输出契约，明确只处理当前 `turn_id` 未 claim 的 `append_to_agent` 资产。
2. 在 ASR final / turn close 后按 `captured_at_ms, sequence_index` 排序 claim 图片。
3. 构造用户 turn 文本，写明图片顺序、相对时间和 `direction`，然后追加 image blocks。
4. `model-request.json` 只写图片摘要、asset_id、时间戳和 direction，不落完整 base64。
5. 覆盖用户打断、图片超出 `max_frames_per_turn`、部分图片过期、无图片四类边界。

Omni 即时 append 细分任务：

1. 每张 realtime-video 图片上传后由 `OmniVisualAppender` 立即 claim。
2. append 到 Realtime provider 时保留上传顺序，不在业务层重新批处理。
3. turn 结束、用户打断或 provider 失败时停止采样，并清理未消费 buffer。
4. 运行产物记录每次 append 的 asset_id、captured_at_ms、direction 和 provider 回执摘要。

测试范围：

1. Omni 每张 realtime-video 图片即时 append。
2. Vision/VL 只在模型请求前批量 append。
3. Vision/VL 的文本说明包含图片顺序、时间戳和方位信息。
4. model-request 产物不落完整 base64，只保留脱敏摘要。

建议命令：

```bash
uv run python -m pytest agent-server/protocol-tests -q
uv run python -m pytest agent-server/model-provider-tests -q
```

如果真实 provider 不稳定，可以先跑非 realtime 子集：

```bash
REALTIME_AGENT_TEST_REPORT_DIR=runs/regression-reports/l2-nonrealtime \
  uv run python -m pytest -m model_provider -k 'not qwen_omni' -q
```

### Phase 4：realtime-video 全局配置和采样器统一

目标：新增全局 realtime-video 配置，并让 Omni / Vision 使用同一个采样配置和 buffer。

配置建议：

```yaml
agent:
  visual:
    realtime_video:
      enabled: true
      frame_interval_seconds: 1.0
      frame_timeout_seconds: 1.5
      frame_ttl_seconds: 5
      max_frames_per_turn: 8
      direction: front
```

迁移策略：

1. 新配置替换现有 `agent.omni.visual_frame_interval_seconds` 等视觉采样字段。
2. 配置加载不保留历史别名；如必须短期过渡，只提供明确的配置错误和迁移文档。
3. 全文检索更新示例配置、测试 fixture 和文档，避免双配置长期并存。

测试范围：

1. Omni 开启 realtime-video 后，provider speech started 启动采样，图片即时 append 到 Realtime provider。
2. Vision/VL 开启 realtime-video 后，用户语音 turn 内主动请求 `sensor.rgb`，图片先进入 turn buffer，不经过 Tool。
3. Vision/VL 在 ASR final 后、模型请求前批量 flush 当前 turn buffer，并写入图片顺序、时间戳和 direction。
4. turn 结束停止采样并清 buffer。
5. 超过 `max_frames_per_turn` 后不再 append 给模型。
6. 用户非视觉问题时，策略可以采集但模型提示不得主动描述图片，除非用户问题需要视觉。

建议命令：

```bash
uv run python -m pytest agent-server/protocol-tests -q
uv run python -m pytest examples/for-blind-app/app-tests -q
```

### Phase 5：自定义视觉 Task 运行时接入

目标：让长周期视觉 Task 使用统一照片资产和 observation 结果，不把每帧图像直接塞给主模型。

关键任务：

1. 定义 `VisualTaskPlan`，表达自然语言解析出的采样频率、持续时间、触发条件、提醒方式和停止条件。
2. 定义 `VisualObservation`，包含 observation_id、task_id、asset_id、captured_at_ms、direction、analyzer、structured_result、summary、confidence。
3. 明确 Task 启动语义：`run()` 只返回启动阶段 `TaskRunResult` / `TaskRef`，长周期过程通过后台采样、`TaskSignal`、`task.event.*` 和 observation 历史推进。
4. 实现通用自定义视觉 Task 骨架，覆盖 360 识别、万物监测、定时视觉总结三类模式。
5. 自定义 360 / 万物监测 Task 使用 `TaskContext.devices.sensors.rgb.stream()` 或 server 请求采样。
6. Task analyzer 可选择 Tool 内部模型、专用模型或端侧模型，但默认不把每帧原图 append 给主模型。
7. Task 只向主模型返回 observation、summary、TaskSignal 和可追问历史。
8. 找物 / 红绿灯 peer video 保持现状，但结果可适配到 `VisualObservation`。
9. 增加 `custom_visual_task_query` 或等价查询能力，让用户后续追问能读取 observation 历史，而不是重新消费旧照片。

测试范围：

1. 360 识别可连续采样并生成多条 observation。
2. 万物监测满足条件才通知，未满足条件时继续循环或按停止条件退出。
3. 定时视觉总结能按触发时间生成 summary，不依赖用户当轮追问。
4. 找物 / 红绿灯现有测试不回归。
5. 后续追问从 Task observation 历史读取，不重新消费旧照片。
6. 测试使用可复现图片 fixture / replay 数据，不把纯 mock 当成场景验收。

建议命令：

```bash
uv run python -m pytest examples/for-blind-app/app-tests -q
uv run python -m pytest examples/for-blind-app/replay-tests -q
```

### Phase 6：端侧参考实现和 Device SDK 对齐

目标：让 browser-glass、python-phone、iOS、ESP32 逐步支持新 metadata，但旧端侧仍可运行。

改动范围：

1. Python Device SDK：stream metadata 透传 `ttl_seconds`、`turn_id`、`capture_reason`。
2. browser-glass：响应新采集 payload 字段，上传 metadata。
3. python-phone：保持 peer video 主线，同时支持普通 `sensor.rgb` metadata。
4. iOS / ESP32：先更新文档和 fixture，代码可分阶段支持。

测试范围：

1. 新 Device SDK 能 round-trip metadata。
2. 老 fixture 继续通过。
3. 端侧忽略未知字段不失败。

建议命令：

```bash
uv run python -m pytest devices/python/protocol-tests -q
uv run python -m pytest examples/dev-support/unit-tests examples/dev-support/app-tests -q
```

## 3. 协议变更风险清单

| 风险 | 阶段 | 缓解 |
| --- | --- | --- |
| 旧端侧不认识新采集字段 | Phase 0 / 6 | 新字段全部可选，测试旧 fixture。 |
| `ttl_seconds` 被误解为磁盘保留时间 | Phase 0 / 1 | 文档和测试明确只影响 turn buffer。 |
| 异步落盘丢失排障证据 | Phase 1 | 落盘失败写 system event，并保留内存消费成功事件。 |
| 移除 `ToolResult.assets` 自动 append 后主模型看不到图 | Phase 2 | `capture_photo` 明确设置 `append_to_agent`，补 app test。 |
| Omni / VL append 语义混用 | Phase 3 | appender 分实现，分别测试。 |
| 长周期 Task 把大量图片塞进主模型 | Phase 5 | Task 默认只输出 observation，不自动 append 原图。 |

## 4. 推荐提交拆分

1. `补充照片资产协议设计`
   - 只改设计文档、协议文档、fixture 和 P0 测试。
2. `实现照片资产buffer`
   - 改 AssetService / TurnPhotoBuffer / Server SDK 测试。
3. `收敛视觉资产消费语义`
   - 改视觉资产描述、移除 `ToolResult.assets` 自动 append 依赖、更新 ModelMessageManager 和 for-blind app tests。
4. `拆分模型视觉append适配器`
   - 改 Omni / Vision appender 和模型请求产物。
5. `接入realtime-video采样配置`
   - 改配置、采样器和应用测试。
6. `接入自定义视觉任务`
   - 改 for-blind-app VisualTaskPlan / Task / observation / replay tests。

## 5. 首轮最小切片

第一轮不要同时迁移所有视觉能力，但后续批量 append 和自定义视觉 Task 已纳入 Phase 3 / Phase 5。建议首轮只做最小闭环：

1. 协议扩展 `sensor.rgb` metadata。
2. 实现 `TurnPhotoBuffer`。
3. 迁移 `capture_photo` 为 `append_to_agent`。
4. 保持 `interpret_current_view` denylist 状态，只补 `internal_only` 测试。
5. Omni realtime-video 继续使用现有采样器，但改成写入统一 buffer 和 claim。
6. 跑 P0 + L1 Server + for-blind app 定向测试。

首轮完成后，按 Phase 3 进入 Vision/VL 批量 append，再按 Phase 5 接入自定义视觉 Task。

## 6. 验收命令总表

协议资产：

```bash
uv run python -m pytest protocol/protocol-tests -q
uv run python -m pytest -m protocol_spec -q
```

Server SDK：

```bash
uv run python -m pytest agent-server/protocol-tests -q
uv run python -m pytest -m sdk -q
```

Device SDK：

```bash
uv run python -m pytest devices/python/protocol-tests -q
uv run python -m pytest -m device_sdk -q
```

应用能力：

```bash
uv run python -m pytest examples/for-blind-app/app-tests -q
uv run python -m pytest examples/for-blind-app/replay-tests -q
```

大模型能力：

```bash
uv run python -m pytest agent-server/model-provider-tests -q
uv run python -m pytest -m model_provider -q
```

完整回归：

```bash
uv run python -m pytest
```

## 7. 本次实施记录

状态：2026-05-22 已完成实现对齐复查后的补齐。

已落地范围：

1. Phase 0：补齐 `sensor.rgb` 采集 payload / stream metadata 文档、fixture 和协议测试。
2. Phase 1：新增 `PhotoAsset`、`PhotoAssetClaim`、`TurnPhotoBuffer`，`AssetService.store_chunk()` 会把 `sensor.rgb` 上传统一放入 turn buffer；磁盘写入改为后台异步归档，主链路可先通过内存 payload 消费照片。
3. Phase 2：新增 `VisualAssetRef`，`capture_photo` 显式返回 `append_to_agent`，内部解读类工具返回 `internal_only`；`ToolResult.assets` 不再作为自动 append 依据。
4. Phase 3：新增 `agent_core.visual` appender 层，`VlVisualAppender` 在模型请求前批量 flush 当前 turn buffer 中未消费图片；Tool 返回的显式视觉资产仍走 tool follow-up message；`OmniVisualAppender` 对 realtime-video 图片立即 claim 并 append 到 Realtime provider。
5. Phase 4：新增 `agent.visual.realtime_video` 配置，Omni realtime-video 使用统一配置、TTL、direction 和每 turn 最大帧数；Vision/VL 也使用同一配置在语音 turn 内主动采集 RGB 帧，再由 `VlVisualAppender` 在模型请求前批量 append。
6. Phase 5：for-blind-app 新增 `custom_visual_task` 和 `custom_visual_task_query`，Task `run()` 只返回启动结果，后台采样生成 `VisualObservation` 和 `TaskSignal`。
7. Phase 6：browser-glass、python-phone mock 和 fallback 上传 RGB 时透传 `turn_id`、`ttl_seconds`、`capture_reason`、`captured_at_ms`、`sequence_index`、`direction`。

复查补齐项：

1. 补齐异步落盘：`AssetStore.put()` 先返回 `AssetRef` 并缓存内存 payload，后台线程归档到 runs；归档失败记录 `asset.archive.failed` 和 system event。
2. 补齐内存读取：Vision/VL 和 Omni append 优先通过 `AssetService.get_asset_payload()` 读取内存 payload，异步归档未完成时不依赖磁盘文件。
3. 补齐 appender 抽象：新增 `ModelVisualAppender`、`VlVisualAppender`、`OmniVisualAppender`，把 provider 差异从 Tool / Task 中隔离出来。
4. 补齐 Vision realtime-video 主动采样：`VisionRealtimeAgentCore` 在音频 chunk / speech started 时启动采样器，ASR final 前停止采样并补一次短语音兜底采样，采集结果进入 turn buffer。
5. 补齐回归：增加 TTL 过期、异步归档未完成时内存可读、`ToolResult.assets`/`internal_only` 不自动 append、Omni realtime-video claim、Vision 不经 Tool 自动采集并 append 的测试。

当前约束：

1. 自定义视觉 Task 当前先提供通用骨架和 observation 历史；真实 analyzer 可在应用层继续接入端侧模型、Tool 内部模型或专用模型，不能写进 SDK core。
2. iOS / ESP32 参考端本轮只完成协议和文档对齐，未做真机 metadata 联调。

已执行验证：

```bash
uv run python -m py_compile agent-server/realtime_agent/asset/photo_asset.py agent-server/realtime_agent/asset/turn_buffer.py agent-server/realtime_agent/asset/service.py agent-server/realtime_agent/tools.py agent-server/realtime_agent/agent_core/vision.py agent-server/realtime_agent/agent_core/multimodal/messages.py agent-server/realtime_agent/agent_core/multimodal/assets.py agent-server/realtime_agent/config.py agent-server/realtime_agent/app.py examples/for-blind-app/agent-server/capabilities/tools.py examples/for-blind-app/agent-server/capabilities/tasks.py examples/for-blind-app/app-tests/capabilities/test_peer_video_tasks.py
uv run python -m pytest protocol/protocol-tests -q
uv run python -m pytest agent-server/protocol-tests -q
uv run python -m pytest examples/for-blind-app/app-tests -q
uv run python -m pytest devices/python/protocol-tests -q
uv run python -m pytest examples/dev-support/unit-tests examples/dev-support/app-tests -q
uv run python -m pytest examples/for-blind-app/replay-tests -q
uv run python -m pytest -m protocol_spec -q
uv run python -m pytest -m sdk -q
uv run python -m pytest -m device_sdk -q
uv run python -m pytest -m model_provider -k 'not qwen_omni' -q
git diff --check
```

完整回归补充：

```bash
uv run python -m pytest -q
```

结果：本地代码回归完成到 1 个外部真实 provider smoke 失败。失败用例为
`test_qwen_omni_realtime_provider_smoke_opens_and_closes_session`，DashScope websocket
返回 `Internal service error: null`；排除 realtime Omni 后的 `model_provider` 子集通过。
