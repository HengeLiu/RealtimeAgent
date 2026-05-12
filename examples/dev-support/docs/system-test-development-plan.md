# 录制式系统测试开发计划

更新时间：2026-05-12

文档状态：开发计划。本文面向下一阶段实现，目标是先完成最小可用的录制式系统测试闭环，再逐步扩展到多设备、视频、Task 和真实 provider。

## 1. 总体目标

开发一套系统级集成测试能力：

1. 支持从 `browser-glass` 手动测试结果生成 Case YAML 草稿。
2. 支持从 Case YAML 自动回放 `testdata/audio-sample` 和 `testdata/image-sample`。
3. 支持检查事件、stream、Tool、Task、资产、输出和系统错误。
4. 支持输出结构化 `report.json`。
5. 支持被 pytest 或本地命令调用，但自身不是普通单元测试。

## 2. 推荐目录

```text
examples/dev-support/system-tests/
  README.md
  cases/
    smoke/
    draft/
  suites/
    smoke.yaml
  reports/

audio-server/audio_chat/system_test/
  __init__.py
  case_schema.py
  recorder.py
  runner.py
  assertions.py
  report.py

audio-server/audio_chat/cli/system_test.py
```

CLI 入口建议新增：

```toml
"audio-chat.system-test.record" = "audio_chat.cli.system_test:record"
"audio-chat.system-test.run" = "audio_chat.cli.system_test:run"
```

## 3. 阶段拆分

### 阶段 0：确认现有基础

目标：不改业务行为，确认当前可复用基础。

任务：

1. 梳理 `PythonPlaybackEndpoint.run_scripted()` 当前可支持的 action 和 assert。
2. 梳理 `NetworkPythonPlaybackEndpoint` 与 `python-phone` 可复用能力。
3. 确认 `testdata/audio-sample/` 文件路径和 mock ASR 文件名转写规则。
4. 确认 `testdata/image-sample/` 作为 `sensor.rgb` fixture 时是否需要复制、压缩或格式校验。

验收：

```bash
uv run python -m pytest examples/dev-support/tests/playback/test_python_playback.py -q
uv run python -m pytest examples/dev-support/tests/test_network_server_playback.py -q
```

### 阶段 1：Case Schema 和手写最小 Case

目标：先定义 Case 格式，并用 1 到 3 个手写 Case 跑通 runner。

任务：

1. 新增 `case_schema.py`，定义 Case、Device、Input、Expect 的数据结构。
2. 新增 `examples/dev-support/system-tests/cases/smoke/`。
3. 新增一个最小 Case，例如“你是谁呀”。
4. 新增一个视觉 Case，例如“看一下我前面有什么” + `image-sample` fixture。
5. Runner 第一版可以复用 in-process `PythonPlaybackEndpoint`，减少网络不稳定因素。

验收：

```bash
uv run audio-chat.system-test.run \
  --case examples/dev-support/system-tests/cases/smoke/who_are_you.yaml \
  --report runs/system-tests/who_are_you.json
```

最小断言：

1. Case 加载成功。
2. `sensor.mic` 输入 stream 存在。
3. speaker 输出 chunk 大于 0。
4. `result.json` 和 `report.json` 写出。
5. 失败时能指出具体断言。

### 阶段 2：Runner 断言能力

目标：让 Case 能表达系统层预期行为。

任务：

1. 实现 `events.includes`。
2. 实现 `streams.includes`。
3. 实现 `tools.called`。
4. 实现 `tasks.signals.includes`。
5. 实现 `assets.<stream_type>.min_count`。
6. 实现 `output.min_audio_chunks`。
7. 实现 `errors.disallow_system_error`。
8. 实现 `model_request.tools.includes`。

验收：

1. 视觉 Case 能断言调用 `capture_photo`。
2. 视觉 Case 能断言 `sensor.rgb` asset 数量大于 0。
3. 故意写错工具名时，runner 返回失败并在 report 中指出缺失工具。
4. 故意写错 stream 类型时，runner 返回失败并指出缺失 stream。

### 阶段 3：Recorder CLI

目标：从现有 runs 产物生成 Case 草稿。

任务：

1. 新增 `audio-chat.system-test.record`。
2. 支持通过 `--runs-root --user-id --device-id` 定位最近 session。
3. 支持通过 `--session-id` 精确指定 session。
4. 读取 `events.jsonl`、`stream-events.jsonl`、`tool-events.jsonl`、`task-signals.jsonl`、`assets.jsonl`、`model-request.json`、`output-decisions.jsonl`。
5. 归纳出 `expect`。
6. 根据录制元数据或用户参数填入音频、图片 fixture。
7. 输出 `case-draft.yaml`。

示例：

```bash
uv run audio-chat.system-test.record \
  --runs-root examples/for-blind-app/audio-server/runs \
  --user-id user-browser-glass-001 \
  --device-id dev-browser-glass-001 \
  --audio testdata/audio-sample/看一下我前面有什么.wav \
  --image sensor.rgb=testdata/image-sample/刚子看电脑.jpeg \
  --out examples/dev-support/system-tests/cases/draft/look_front.yaml
```

验收：

1. 从一次 browser-glass 手动测试产物生成 YAML。
2. 生成 YAML 不包含 `event_id`、`timestamp_ms`、`stream_id`、`asset_id`。
3. 生成 YAML 能被 runner 加载。
4. 生成 YAML 回放后至少通过基础断言。

### 阶段 4：browser-glass 录制入口

目标：降低录制 Case 的操作成本。

任务：

1. `browser-glass` 页面增加录制状态区。
2. 支持“开始录制 Case”和“结束录制 Case”。
3. 开始录制时发送一个轻量控制事件或调用 debug API，记录 case metadata。
4. 页面记录用户选择的音频样例和图片样例路径或文件名。
5. 结束录制后展示推荐的 `audio-chat.system-test.record` 命令。
6. 后续再支持从 server 获取 YAML 文本并显示在页面中。

第一阶段不要让浏览器直接写仓库文件。

验收：

1. 页面能显示当前录制状态。
2. 页面能显示本次录制的 user_id、device_id、可能的 session_id。
3. 页面能生成可复制的 record 命令。
4. 现有 browser-glass 静态测试更新通过。

### 阶段 5：Suite 和 pytest 集成

目标：支持“每次改代码后跑一组系统 Case”。

任务：

1. 新增 suite YAML。
2. Runner 支持 `--suite`。
3. 支持 `--fail-fast` 和 `--keep-runs`。
4. 新增 pytest 包装测试，例如 `examples/dev-support/tests/system/test_smoke_system_cases.py`。
5. pytest 只调用 runner，不在测试里散落业务断言。

示例：

```yaml
id: smoke
name: 开发冒烟系统测试
cases:
  - examples/dev-support/system-tests/cases/smoke/who_are_you.yaml
  - examples/dev-support/system-tests/cases/smoke/look_front.yaml
```

验收：

```bash
uv run audio-chat.system-test.run \
  --suite examples/dev-support/system-tests/suites/smoke.yaml \
  --report runs/system-tests/smoke/report.json

uv run python -m pytest examples/dev-support/tests/system -q
```

## 4. 首批建议 Case

优先选择 mock provider 下稳定的 Case：

| Case | 音频 | 是否需要图片 | 主要断言 |
| --- | --- | --- | --- |
| `who_are_you` | `你是谁呀.wav` | 否 | speaker 输出、无系统错误 |
| `query_device_state` | `帮我查一下我眼镜的状态.wav` | 否 | 调用设备状态工具 |
| `look_front` | `看一下我前面有什么.wav` | 是 | 调用 `capture_photo`、产生 `sensor.rgb` asset |
| `timer_1min` | `一分钟后提醒我.wav` | 否 | 启动 timer task 或输出相关 task 信号 |
| `memory_name` | `我叫文刀文字的文刀锋的刀.wav` | 否 | 写入 memory 或产生可观察消息 |

如果某些 Case 依赖尚未稳定的业务工具，先放到 `acceptance`，不要放进每次都跑的 `smoke`。

## 5. 实现注意点

1. 所有新增 Python 类、函数和测试 docstring 使用中文，说明目标、方法、预期结果。
2. Case YAML 只引用媒体文件路径，不内嵌媒体字节。
3. 录制器默认生成宽松断言，人工确认后再加严格断言。
4. runner 不应绕过协议直接调用业务 Tool，除非该 Case 明确标注为 in-process 能力回放。
5. 真实网络模式和 in-process 模式都要保留，但第一阶段优先 in-process。
6. 失败报告要指向 runs 目录，方便继续排查。
7. `reports/`、临时 runs 和录制草稿如果会产生大量文件，应确认 `.gitignore` 覆盖。
8. 不把真实用户音频、图片或视频自动复制进可提交目录。
9. 真实 provider lane 只做链路断言，不逐字断言模型回复。
10. 新 CLI 需要同步 README 或 dev-support README，并补 `test_docs_commands` 相关检查。

## 6. 开发顺序建议

推荐先实现：

1. `case_schema.py`
2. `runner.py`
3. 两个手写 smoke Case
4. `audio-chat.system-test.run`
5. `recorder.py`
6. `audio-chat.system-test.record`
7. browser-glass 录制入口
8. suite 和 pytest 包装

原因是：runner 是验证 Case 是否有价值的核心；没有 runner，录制器生成 YAML 也无法证明可用。

## 7. 完成标准

第一阶段完成标准：

1. 可以用命令跑至少 2 个系统 Case。
2. 至少 1 个 Case 使用音频样例。
3. 至少 1 个 Case 使用图片样例。
4. 每个 Case 都生成独立 result 和统一 report。
5. 故意破坏 Case 断言时能得到明确失败原因。
6. 从一次 browser-glass 手动 runs 产物能生成 Case 草稿。

第二阶段完成标准：

1. browser-glass 页面可以辅助录制 Case。
2. smoke suite 可以通过 pytest 一条命令运行。
3. 多设备 Case 能验证 RGB 从眼镜端转发到 phone preview。
4. Task Case 能验证 `task-signals.jsonl`。
5. 文档、CLI、示例 Case 和测试结果一致。

