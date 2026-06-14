# 贡献指南

感谢你考虑参与 `realtime-agent`。这个项目目前处在 SDK 开源前后的快速演进阶段，贡献时请优先保证代码、文档和测试能够反映真实实现状态。

## 贡献方向

这个 SDK 项目最需要的贡献来自四个方向：

1. **Server SDK 运行时优化**

   包括 Agent Core 优化、Omni / VL 链路优化、模型 provider 接入、上下文管理、输出播放仲裁、Tool 运行时和运行产物可观测性改进。

2. **Device SDK 和端侧能力**

   支持更多语言和设备类型，完善不同端侧的协议实现，优化端侧音频、视频和传感器处理。尤其欢迎端侧回声抑制、唤醒、打断、播放缓冲、相机帧采集和弱网重连等问题的真实设备验证与修复。

3. **开发者支持功能**

   包括自动化测试验收、协议一致性检查、数据回放测试平台、运行产物查看工具、联调诊断工具、配置同步、示例工程和开发者文档。

4. **基础通用 Tool**

   提交足够基础、通用、可复用的 Tool，例如设备能力演示、计时提醒、基础导航/搜索接入、通用视觉采集、常见传感器读取和标准执行器控制。业务强绑定能力建议放在独立应用仓库中维护。

不建议直接提交的方向：

1. 大规模重构但没有清晰问题描述、测试计划和迁移边界。
2. 把某个业务能力硬编码进 SDK 核心包。
3. 绕过 Context API 直接在业务能力里操作底层 WebSocket。
4. 提交真实用户音频、图片、视频、日志或 API key。

## 开发环境

```bash
uv sync --python 3.11
uv pip install -e .
```

运行测试：

```bash
uv run python -m pytest -q
```

针对文档或开发者体验变更，建议至少跑：

```bash
uv run python -m pytest examples/device_app_demo/app-tests -q
uv run realtime-agent.device.validate dev-support/devices/browser-glass/device.realtime-agent.yaml
```

完整测试策略见 [docs/testing.md](docs/testing.md)。

## 代码风格

1. 使用 Python 3.11+。
2. 保持已有模块边界。
3. 业务能力通过 `BaseTool` 和 Context API 扩展。
4. 不在 Tool 中直接操作内部 WebSocket 状态。
5. 新增类、函数和测试说明优先使用中文注释或 docstring。
6. 排障日志使用 DEBUG 级别，并允许通过配置控制全局日志级别。

## 文档风格

文档应该帮助开发者更快理解和验证真实功能，不追求堆字数。

复杂流程优先使用 PlantUML。文档中的命令和测试结果必须来自真实执行结果，不能只写设计预期。

## Pull Request 说明

PR 建议包含：

1. 变更目的。
2. 影响范围。
3. 测试命令和结果。
4. 是否影响设备协议、运行产物或端侧行为。
5. 如果涉及 UI、iOS、浏览器、ESP32 或其他真实端侧，请附上日志、截图或 runs 产物说明。

## 安全与隐私

不要提交：

1. API key。
2. Wi-Fi 密码。
3. 设备 token。
4. `.env`。
5. 真实用户音频、图片、视频。
6. 本地运行日志和构建产物。

如果新增工具会产生缓存、日志或构建产物，请同步更新 `.gitignore`。
