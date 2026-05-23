# SDK 照片资产处理链路开发计划

更新时间：2026-05-22

对应设计文档：[SDK 照片资产处理链路设计](photo-asset-pipeline-design.md)。

本文只记录 SDK 范围的实施计划和当前状态。for-blind-app 的自定义视觉功能、自然语言参数提取、360 识别、万物监测和定时任务计划放在 `examples/for-blind-app/docs/`。

## 1. 实施原则

1. 先协议资产和测试，再改运行时。
2. 保持事件名和 stream 二进制帧格式稳定。
3. 新字段只作为可选 metadata / payload 扩展。
4. 新语义落地时清理旧的自动 append 路径。
5. SDK core 只提供照片资产、buffer、claim、appender 和协议能力，不写业务 Task 逻辑。

## 2. 阶段划分

### Phase 0：协议变更预备

目标：让协议资产表达照片 metadata 和采集请求目标状态。

改动范围：

1. 更新 `protocol/docs/protocol.md`。
2. 更新 `protocol/data/fixtures/events/stream-open-requested.json`。
3. 更新 `protocol/data/fixtures/streams/rgb-header.json`。
4. 保持控制 payload 禁止媒体 bytes。
5. 更新 `protocol/protocol-tests/`。

验收命令：

```bash
uv run python -m pytest protocol/protocol-tests -q
uv run python -m pytest -m protocol_spec -q
```

### Phase 1：PhotoAsset 与 TurnPhotoBuffer

目标：在 server 内建立统一照片资产生命周期。

模块：

```text
agent-server/realtime_agent/asset/
  photo_asset.py
  turn_buffer.py
  service.py
```

关键任务：

1. 定义 `PhotoAsset`、`PhotoAssetClaim`。
2. 引入 `TurnPhotoBuffer`。
3. `AssetService.store_chunk()` 对 `sensor.rgb` 生成 PhotoAsset 并放入 buffer。
4. 磁盘写入改为异步归档。
5. `ttl_seconds` 只控制 buffer 有效期。
6. turn 正常完成、失败、用户打断时清空当前 buffer。

验收命令：

```bash
uv run python -m pytest agent-server/protocol-tests -q
uv run python -m pytest -m sdk -q
```

### Phase 2：消费 claim 与视觉资产语义

目标：照片是否 append 给主模型必须显式声明。

关键任务：

1. 新增 `VisualAssetRef`。
2. 引入 `visibility=append_to_agent | internal_only`。
3. 移除对 `ToolResult.assets` 的视觉自动 append 依赖。
4. claim 事件写入 `assets.jsonl`。

测试范围：

1. `append_to_agent` 会把图片 append 给主模型。
2. `internal_only` 不会把原图 append 给主模型。
3. 同一资产不能被两个业务消费者重复 claim。

### Phase 3：ModelVisualAppender

目标：把 Omni 和 Vision/VL append 差异从 Tool / Task 中抽出。

模块：

```text
agent-server/realtime_agent/agent_core/visual/
  appender.py
```

关键任务：

1. 定义 `ModelVisualAppender` 接口。
2. 实现 `OmniVisualAppender`。
3. 实现 `VlVisualAppender`。
4. 工具返回视觉资产走 `append_visual_assets()`。
5. 当前 turn 自动采样图片走 `flush_turn_assets()`。

测试范围：

1. Omni 每张 realtime-video 图片即时 append。
2. Vision/VL 模型请求前批量 append。
3. `model-request.json` 不落完整 base64。

### Phase 4：realtime-video 全局配置和采样器统一

目标：Omni / Vision 使用同一组视觉采样配置和 buffer。

配置：

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

测试范围：

1. Omni speech started 启动采样，图片即时 append。
2. Vision/VL 用户语音 turn 内主动请求 `sensor.rgb`。
3. Vision/VL ASR final 后批量 flush 当前 turn buffer。
4. turn 结束停止采样并清 buffer。
5. 超过 `max_frames_per_turn` 后不再 append 给模型。

### Phase 5：Device SDK 和参考端 metadata 对齐

目标：端侧逐步支持新 metadata，旧端侧仍可运行。

改动范围：

1. Python Device SDK metadata round-trip。
2. browser-glass 上传 `ttl_seconds`、`capture_reason`、`captured_at_ms`、`sequence_index`、`direction`。
3. python-phone mock / fallback 上传 metadata。
4. iOS / ESP32 先对齐文档和 fixture。

验收命令：

```bash
uv run python -m pytest devices/python/protocol-tests -q
uv run python -m pytest examples/dev-support/unit-tests examples/dev-support/app-tests -q
```

## 3. 当前实现状态

状态：2026-05-22 已完成首轮落地。

已落地范围：

1. Phase 0：协议文档、fixture 和协议测试已更新。
2. Phase 1：`PhotoAsset`、`PhotoAssetClaim`、`TurnPhotoBuffer` 已实现；`AssetService` 会把 `sensor.rgb` 上传放入 turn buffer；磁盘归档异步执行。
3. Phase 2：`VisualAssetRef` 已实现；`ToolResult.assets` 不再作为视觉自动 append 依据。
4. Phase 3：`agent_core.visual` appender 层已实现；Omni 即时 append，Vision/VL 批量 append。
5. Phase 4：`agent.visual.realtime_video` 配置已接入 Omni 和 Vision。
6. Phase 5：browser-glass、python-phone mock 和 fallback 已透传主要 RGB metadata。

当前约束：

1. SDK 不提供业务 analyzer。
2. Task observation 的业务结构由应用层定义。
3. iOS / ESP32 参考端未做真机 metadata 联调。

## 4. 已执行验证

```bash
uv run python -m py_compile agent-server/realtime_agent/asset/photo_asset.py agent-server/realtime_agent/asset/turn_buffer.py agent-server/realtime_agent/asset/service.py agent-server/realtime_agent/tools.py agent-server/realtime_agent/agent_core/vision.py agent-server/realtime_agent/agent_core/multimodal/messages.py agent-server/realtime_agent/agent_core/multimodal/assets.py agent-server/realtime_agent/config.py agent-server/realtime_agent/app.py
uv run python -m pytest protocol/protocol-tests -q
uv run python -m pytest agent-server/protocol-tests -q
uv run python -m pytest devices/python/protocol-tests -q
uv run python -m pytest examples/dev-support/unit-tests examples/dev-support/app-tests -q
uv run python -m pytest -m protocol_spec -q
uv run python -m pytest -m sdk -q
uv run python -m pytest -m device_sdk -q
uv run python -m pytest -m model_provider -k 'not qwen_omni' -q
git diff --check
```

完整回归 `uv run python -m pytest -q` 本地只剩真实 DashScope Omni Realtime smoke 曾返回 `Internal service error: null`，非 realtime provider 子集通过。
