# 第二期 SDK 最终验收方案

## 1. 文档定位

本文档用于第二期完成后的最终验收。

验收目标不是证明某个业务功能效果完美，而是证明当前仓库已经达到第二期 SDK 形态：

1. 系统细节隐藏在 SDK 中。
2. 官方 `openaiglass-for-blind` 只承载开发者需要关注的业务能力实现。
3. 根目录 `server / phone / glass` 不再承载具体业务能力。
4. 开发者可以基于 `BaseTool / BaseTask / BasePhoneTask / BasePhoneProcessor` 完成第一轮离线开发和验证。
5. Python SDK 可以被构建成 wheel，并通过 `pip install` 安装后导入使用。

## 2. 验收范围

本次验收覆盖：

1. Python SDK 包与服务端运行时。
2. iOS 手机 SDK运行时 边界。
3. ESP32 眼镜 SDK运行时 边界。
4. 官方 `find_object` openaiglass-for-blind。
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

1. 宿主目录 `host/phone/src`、`../../openaiglass-sdk/phone-ios/GlassesVideoReceiver`、`../../openaiglass-sdk/phone-ios/GlassesVideoReceiverTests`、`../../openaiglass-sdk/phone-ios/GlassesVideoReceiver.xcodeproj/project.pbxproj` 和 `../../openaiglass-sdk/glass-esp32` 中没有具体业务能力词汇。
2. `openaiglass-sdk/server-python`、根运行时和端侧运行时没有反向依赖 `openaiglass-for-blind`。

其中 `sdk_package` 必须证明：

1. [openaiglass-sdk/server-python/pyproject.toml](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/openaiglass-sdk/server-python/pyproject.toml) 可以构建 `openaiglasses-sdk` wheel。
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

### 5.1 SDK 不依赖 openaiglass-for-blind

执行：

```bash
rg -n "from capabilities|import capabilities|../../capabilities" openaiglass-sdk/server-python openaiglass-for-blind/host/phone/src openaiglass-sdk/phone-ios/GlassesVideoReceiver openaiglass-sdk/glass-esp32 -g '!**/__pycache__/**'
```

预期结果：

1. 不应出现 SDK 或根运行时依赖 `openaiglass-for-blind` 的结果。
2. 如果命中 `doc` 或 `openaiglass-for-blind` 以外路径，应判定为边界回退。

### 5.2 根运行时不含业务能力

执行：

```bash
rg -n "find_object|FindObject|YoloFindObject|start_find_object|timer_manage|map_manage|Amap|navigation_task" openaiglass-for-blind/host/phone/src openaiglass-sdk/phone-ios/GlassesVideoReceiver openaiglass-sdk/phone-ios/GlassesVideoReceiverTests openaiglass-sdk/phone-ios/GlassesVideoReceiver.xcodeproj openaiglass-sdk/glass-esp32 -g '!**/__pycache__/**'
```

预期结果：

1. 不应出现命中。
2. 如果需要新增业务能力，应放入 `openaiglass-for-blind/` 或外部开发者项目，不应修改根运行时。

### 5.3 openaiglass-for-blind 只写业务扩展

重点检查：

1. `capabilities/find_object/server/tool.py`
2. `capabilities/find_object/server/task.py`
3. `capabilities/find_object/phone/processor.py`
4. `capabilities/find_object/phone/task.py`
5. `capabilities/find_object/scenario.py`

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
2. 文档不再把根目录 `openaiglass-sdk/phone-ios` 描述为具体 `find_object` App。
3. 文档明确说明根 iOS 工程默认只编译通用 SDK运行时。
4. 文档明确说明官方样例能力位于 `openaiglass-for-blind/`，并由外部宿主显式接入。

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

## 9. sdk-v13 后续四项专项验收补充

用户重新排序后的后续工作只验收以下四项，其他欠缺项暂时不纳入本轮验收。

### 9.1 真 iOS 手机视觉资源管理

验收目标：

1. 真 iOS 运行时具备统一 `vision_policy` 解释和资源协调入口。
2. Swift 业务插件不需要自行实现帧率限制、模型并发控制、抢占和功耗降级。
3. 服务端能收到 `vision.resource.denied`、`vision.task.preempted`、`vision.task.degraded` 或 `vision.task.overloaded` 等结构化事件。

建议验收命令：

```bash
xcodebuild test -project openaiglass-sdk/phone-ios/GlassesVideoReceiver.xcodeproj -scheme GlassesVideoReceiver -destination 'platform=iOS Simulator,name=iPhone 16'
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit -q
```

### 9.2 统一播放仲裁和用户打断

验收目标：

1. 普通 Agent 回复、Task 通知、手机视觉告警和用户打断都进入同一个播放仲裁入口。
2. 高优先级告警和用户打断可以终止当前播报，并留下可解释决策日志。
3. 运行态快照能展示当前播放 lease、队列、最近仲裁决策和最近用户打断。

建议验收命令：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_voice_runtime.py openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q
```

### 9.3 账号权限、组织管理和配置中心

验收目标：

1. 本地默认账号模式保持零额外配置。
2. 跨账号、跨组织或无权限访问被拒绝，并写入审计事件。
3. SDK 策略配置可以通过 `ConfigProvider` 读取，且运行态快照包含配置版本。

建议验收命令：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q
```

### 9.4 SQLite 任务持久化

验收目标：

1. SQLite 文件库存储任务、事件和租约。
2. 重启后任务快照和事件幂等记录可恢复。
3. 单机多进程或多 manager 使用同一 SQLite 文件时，租约能避免同一任务被重复恢复执行。
4. 文件型持久化仍兼容，不因 SQLite 引入而退化。

建议验收命令：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q
```
