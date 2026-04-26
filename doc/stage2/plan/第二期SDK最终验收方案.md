# 第二期 SDK 最终验收方案

## 1. 文档定位

本文档用于第二期完成后的最终验收。

验收目标不是证明某个业务功能效果完美，而是证明当前仓库已经达到第二期 SDK 形态：

1. 系统细节隐藏在 SDK 中。
2. 官方 `example` 只承载开发者需要关注的业务能力实现。
3. 根目录 `server / phone / glass` 不再承载具体业务能力。
4. 开发者可以基于 `BaseTool / BaseTask / BasePhoneTask / BasePhoneProcessor` 完成第一轮离线开发和验证。
5. Python SDK 可以被构建成 wheel，并通过 `pip install` 安装后导入使用。

## 2. 验收范围

本次验收覆盖：

1. Python SDK 包与服务端运行时。
2. iOS 手机 SDK运行时 边界。
3. ESP32 眼镜 SDK运行时 边界。
4. 官方 `find_object` example。
5. SDK 公共契约与金样测试。
6. 离线场景回放与兼容性回归。
7. 真机联调前检查入口。
8. Python SDK 包构建、安装与导入验证。

本次验收不覆盖：

1. 完整导航业务实现。
2. 生产级鉴权、租户隔离和多租户部署。
3. Android / iOS 正式 SDK 打包发布。
4. 插件市场、远程能力分发和 OTA。

## 3. 验收前提

在仓库根目录执行：

```bash
cd /Users/elio/dev/llm-project/OpenAIglassesDemo_2
```

确认使用 `uv` 管理的 Python 环境：

```bash
uv run python --version
```

## 4. 自动化验收

### 4.1 SDK 最终预检

执行：

```bash
uv run python script/run_sdk_preflight.py --report logs/sdk-preflight-stage2-final.json
```

预期结果：

1. `ok` 为 `true`。
2. `compileall` 通过。
3. `entrypoints` 通过。
4. `sdk_boundary` 通过。
5. `scenario_suite` 通过。
6. `contract_suite` 通过。
7. `compatibility_suite` 通过。
8. `pytest_core` 通过。
9. `server_health` 通过。
10. `sdk_package` 通过。

其中 `sdk_boundary` 是第二期最终验收的关键检查项。它必须证明：

1. 根目录 `server/src`、`phone/src`、`phone/ios/GlassesVideoReceiver`、`phone/ios/GlassesVideoReceiverTests`、`phone/ios/GlassesVideoReceiver.xcodeproj/project.pbxproj` 和 `glass/src` 中没有具体业务能力词汇。
2. `sdk/python`、根运行时和端侧运行时没有反向依赖 `example`。

其中 `sdk_package` 必须证明：

1. [sdk/python/pyproject.toml](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/sdk/python/pyproject.toml) 可以构建 `openaiglasses-sdk` wheel。
2. 构建出的 wheel 可以通过 `pip install` 安装到临时环境。
3. 安装后可以从 `openaiglasses` 导入 `OpenAIGlassesSDK / ServerSettings`。
4. 安装后内部运行时模块 `agent_core.skills / infra.clock / api.http_server / runtime.voice_runtime` 可以正常导入。

### 4.1.1 Python SDK 包验收

单独执行：

```bash
python script/run_sdk_package_check.py
```

预期结果：

1. 输出 JSON 中 `ok` 为 `true`。
2. 生成的 wheel 名称形如 `openaiglasses_sdk-0.1.0-py3-none-any.whl`。
3. `import_stdout` 中能看到当前 SDK 版本号。

### 4.2 公共契约验收

执行：

```bash
uv run python script/run_sdk_contract_tests.py
```

预期结果：

1. `server/test/contracts` 全部通过。
2. `testdata/contracts` 中的金样与当前公共对象、控制消息和手机任务事件格式一致。

### 4.3 官方样例兼容性验收

执行：

```bash
uv run python script/run_sdk_compatibility_tests.py
```

预期结果：

1. `testdata/compat/find_object_scenarios.json` 中列出的场景全部校验通过。
2. 这些场景全部可以执行并满足各自 `expected` 断言。

### 4.4 场景回放验收

执行：

```bash
uv run python script/run_sdk_scenario.py --scenario-dir testdata/scenario --pretty
```

预期结果：

1. 全部场景通过。
2. 至少覆盖正向完成、任务取消、缺少手机、视频链路启动失败、传感器辅助输入。

## 5. 代码边界人工验收

### 5.1 SDK 不依赖 example

执行：

```bash
rg -n "from example|import example|example\\." sdk/python server/src phone/src phone/ios/GlassesVideoReceiver glass/src -g '!**/__pycache__/**'
```

预期结果：

1. 不应出现 SDK 或根运行时依赖 `example` 的结果。
2. 如果命中 `doc` 或 `example` 以外路径，应判定为边界回退。

### 5.2 根运行时不含业务能力

执行：

```bash
rg -n "find_object|FindObject|YoloFindObject|start_find_object|timer_manage|map_manage|Amap|navigation_task" server/src phone/src phone/ios/GlassesVideoReceiver phone/ios/GlassesVideoReceiverTests phone/ios/GlassesVideoReceiver.xcodeproj glass/src -g '!**/__pycache__/**'
```

预期结果：

1. 不应出现命中。
2. 如果需要新增业务能力，应放入 `example/` 或外部开发者项目，不应修改根运行时。

### 5.3 example 只写业务扩展

重点检查：

1. `example/capabilities/find_object/server/tool.py`
2. `example/capabilities/find_object/server/task.py`
3. `example/capabilities/find_object/phone/processor.py`
4. `example/capabilities/find_object/phone/task.py`
5. `example/capabilities/find_object/scenario.py`

验收标准：

1. 服务端业务只继承或使用 `BaseTool / BaseTask / TaskContext / DeviceGroupContext`。
2. 手机业务只继承或使用 `BasePhoneTask / BasePhoneProcessor / BaseSensorProvider`。
3. 不直接处理 WebSocket 连接对象、设备绑定表、媒体帧编码和服务端任务存储。

## 6. 文档一致性验收

重点阅读：

1. [第二期-SDK核心运行时与开发者扩展面产品化开发计划.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage2/plan/第二期-SDK核心运行时与开发者扩展面产品化开发计划.md)
2. [第二期下半程-SDK公共契约与SDK运行时产品化收口计划.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage2/plan/第二期下半程-SDK公共契约与SDK运行时产品化收口计划.md)
3. [SDK公共契约设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/SDK公共契约设计.md)
4. [手机SDK运行时设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/手机SDK运行时设计.md)
5. [眼镜SDK运行时设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/眼镜SDK运行时设计.md)

验收标准：

1. 文档中的当前状态与代码目录一致。
2. 文档不再把根目录 `phone/ios` 描述为具体 `find_object` App。
3. 文档明确说明根 iOS 工程默认只编译通用 SDK运行时。
4. 文档明确说明官方样例能力位于 `example/`，并由外部宿主显式接入。

## 7. 真机联调前验收

自动化通过后，再执行：

```bash
bash script/sync_sdk_live_config.sh
bash script/run_sdk_live_check.sh --report logs/sdk-live-check-stage2-final.json
```

预期结果：

1. 本地配置同步完成。
2. 服务端健康检查通过。
3. 设备配置、局域网地址、端口和脚本入口可用。

真机联调时按 [SDK真机联调前检查与联调步骤.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/sdk-design/SDK真机联调前检查与联调步骤.md) 执行。

## 8. 通过标准

第二期最终验收通过需要同时满足：

1. `run_sdk_preflight.py` 全部通过。
2. `sdk_boundary` 无任何违规项。
3. 公共契约测试全部通过。
4. 官方样例兼容性测试全部通过。
5. 场景回放全部通过。
6. Python SDK 包构建、安装和导入检查通过。
7. 人工代码边界检查无反向依赖和根目录业务能力残留。
8. 文档与当前代码边界一致。

如果上述任一项失败，第二期不能判定为 100% 完成。
