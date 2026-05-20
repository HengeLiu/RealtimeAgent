# 测试体系说明

本文档是项目级测试入口，说明代码变更后如何选择回归范围，以及新增功能时如何补测试。更详细的阶段计划见 [回归测试分层设计文档](internal/regression-test-strategy.md)。

## 1. 总体模型

当前测试体系分为“协议资产 + 三层系统级回归”：

```text
P0 协议资产检查
L1 事件行为一致性测试
L2 大模型能力测试
L3 应用能力测试
```

协议本身不是运行时，不会执行动作。P0 只检查协议资产是否完整、可解析、可版本化；真正的系统级行为测试放在 L1，由 `audio-server` 和 `audio-device` 的运行时代码验证“收到什么事件后应该返回什么事件、触发什么处理流程、写出什么运行产物”。

```text
P0 协议资产是共同输入
        ↓
L1 事件行为一致性通过，server/device 可以互认
        ↓
L2 大模型能力通过，真实模型链路具备运行前提
        ↓
L3 应用能力通过，自动化产品功能具备运行前提
        ↓
最终产品整体验收
```

## 2. 目录模型

测试目录按模块拆分，每个模块下面再区分单元测试和层级测试：

| 模块 | 单元测试 | 层级测试 |
| --- | --- | --- |
| 协议资产 | `protocol/unit-tests/` | `protocol/protocol-tests/` |
| Server SDK | `audio-server/unit-tests/` | `audio-server/protocol-tests/`、`audio-server/model-provider-tests/` |
| Python Device SDK | `audio-device/python/unit-tests/` | `audio-device/python/protocol-tests/` |
| for-blind-app | `examples/for-blind-app/unit-tests/` | `examples/for-blind-app/app-tests/`、`replay-tests/`、`hardware-tests/` |
| dev-support | `examples/dev-support/unit-tests/` | `examples/dev-support/app-tests/`、`replay-tests/`、`hardware-tests/` |

协议资产位于：

```text
protocol/
  docs/protocol.md
  data/fixtures/
  behavior/
```

## 3. 协议的两类版本

协议资产包含两类版本，二者独立演进：

| 类型 | 建议版本字段 | 说明 |
| --- | --- | --- |
| 数据结构协议 | `protocol.data.version` | 事件信封、事件名、payload schema、stream header、错误码、golden fixture、反例 fixture。 |
| 事件处理规范 | `protocol.behavior.version` | server 和 device 收到某类事件后应该返回什么事件、触发什么流程、更新什么状态、写出什么产物。 |

数据结构协议主要通过 JSON / YAML / schema / fixture 显式存储。事件处理规范是 L1 conformance 的输入，真正的行为验证发生在 `audio-server/protocol-tests/` 和 `audio-device/python/protocol-tests/`。

## 4. 各层范围

| 层级 | 测试目标 | 主要位置 |
| --- | --- | --- |
| P0 协议资产检查 | 检查协议文档、schema、fixture、错误码、行为规范引用和版本号。 | `protocol/protocol-tests/` |
| L1 事件行为一致性 | 检查 Server SDK / Device SDK 面对协议事件时是否按事件处理规范执行动作。 | `audio-server/protocol-tests/`、`audio-device/python/protocol-tests/` |
| L2 大模型能力 | 检查真实 ASR、TTS、Vision/Text、Realtime provider 的能力、稳定性、延迟和错误诊断。 | `audio-server/model-provider-tests/` |
| L3 应用能力 | 检查 for-blind-app、dev-support、真实样例回放和端侧参考工程。 | `examples/for-blind-app/app-tests/`、`examples/for-blind-app/replay-tests/`、`examples/dev-support/app-tests/` |

## 5. 常用回归命令

P0 协议资产：

```bash
uv run python -m pytest protocol/protocol-tests -q
uv run python -m pytest -m protocol_spec -q
uv run python -m pytest -m protocol -q
```

L1 事件行为一致性：

```bash
uv run python -m pytest audio-server/protocol-tests -q
uv run python -m pytest audio-device/python/protocol-tests -q
uv run python -m pytest -m sdk -q
uv run python -m pytest -m device_sdk -q
uv run python -m pytest -m interop -q
```

L2 大模型能力：

```bash
uv run python -m pytest audio-server/model-provider-tests -q
uv run python -m pytest -m model_provider -q
```

如果真实 Realtime provider 受外部服务状态影响，可以先跑非 Realtime 子集：

```bash
REALTIME_AGENT_TEST_REPORT_DIR=runs/regression-reports/l2-nonrealtime \
  uv run python -m pytest -m model_provider -k 'not qwen_omni' -q
```

L3 应用能力：

```bash
uv run python -m pytest examples/for-blind-app/app-tests -q
uv run python -m pytest examples/for-blind-app/replay-tests -q
uv run python -m pytest examples/dev-support/app-tests examples/dev-support/unit-tests -q
uv run python -m pytest -m app -q
uv run python -m pytest -m replay -q
```

完整本地回归：

```bash
uv run python -m pytest
```

## 6. 变更后如何选择测试

| 变更类型 | 必跑测试 | 建议加跑 |
| --- | --- | --- |
| 协议数据结构、事件名、payload、stream header | `protocol/protocol-tests` 或 `-m protocol` | 如果改变处理动作，加跑 `-m sdk`、`-m device_sdk`。 |
| 事件处理规范 | `-m sdk`、`-m device_sdk` | `-m interop`。 |
| Server SDK control / stream / output / task / Context API | `audio-server/protocol-tests` 或 `-m sdk` | `-m interop`。 |
| Device SDK | `audio-device/python/protocol-tests` 或 `-m device_sdk` | `-m interop`。 |
| Server/Device WebSocket 互操作 | `-m interop` | `-m sdk`、`-m device_sdk`。 |
| ASR / TTS / Vision / Realtime provider adapter | `audio-server/model-provider-tests` 或 `-m model_provider` | 相关 L1 SDK 测试。 |
| for-blind-app 业务能力 | `examples/for-blind-app/app-tests` | `examples/for-blind-app/replay-tests`。 |
| dev-support 端侧参考工程 | `examples/dev-support/unit-tests`、`examples/dev-support/app-tests` | 相关 L1 interop 或 L3 replay。 |

## 7. 新增测试用例规则

新增功能时先判断它改变的是哪一类能力：

1. 如果改变协议数据结构，补协议版本、schema、fixture、反例 fixture 和 P0 检查。
2. 如果改变事件处理动作，补 L1 Server SDK / Device SDK 事件行为一致性测试。
3. 如果改变真实 provider，补 L2 smoke 和 artifact。
4. 如果改变应用功能，补 L3 app / replay / hardware 测试。

新增测试要求：

- 测试文件命名为 `test_*.py`。
- 新测试用中文 docstring 写清楚测试目标、测试方法和预期结果。
- 系统级测试优先从协议事件、stream chunk、WebSocket 消息或 fixture 输入开始。
- fake 只能替代外部不稳定依赖，不能替代 SDK 内部核心逻辑。
- 真实 provider 测试必须产出可复查 artifact。
- 真机或硬件能力必须说明自动化覆盖范围和人工验收缺口。

## 8. 报告和产物

按 marker 执行测试时，根目录 `conftest.py` 会写入轻量报告：

```text
runs/regression-reports/latest/
  protocol-spec-report.json
  l0-protocol-report.json
  l1-server-sdk-report.json
  l1-device-sdk-report.json
  l1-interop-report.json
  l2-model-provider-report.json
  l3-app-report.json
  l3-replay-report.json
  summary.md
```

L2 provider 测试会额外写入：

```text
runs/provider-tests/latest/
```

这些产物用于本地排查和回归证据，不提交到 git。
