# 贡献指南

感谢你考虑参与 `audio_chat`。这个项目目前处在 SDK 开源前后的快速演进阶段，贡献时请优先保证代码、文档和测试能够反映真实实现状态。

## 贡献方向

适合贡献的方向：

1. SDK runtime bug 修复。
2. Tool / Task 开发体验改进。
3. 设备能力 schema 和校验改进。
4. browser-glass、python-phone、python-playback-glass 等开发/测试支持组件完善，以及 iOS、ESP32 端侧参考工程完善。
5. 测试、回放、acceptance 脚本。
6. 文档、教程、排障指南。

不建议直接提交的方向：

1. 大规模重构但没有清晰问题描述。
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
uv run python -m pytest examples/for-blind-app/tests/replay/test_text_route_audio_samples.py -q
uv run audio-chat.device.validate examples/dev-support/devices/browser-glass/device.audio-chat.yaml
```

## 代码风格

1. 使用 Python 3.11+。
2. 保持已有模块边界。
3. 业务能力通过 `BaseTool`、`BaseTask` 和 Context API 扩展。
4. 不在 Tool / Task 中直接操作内部 WebSocket 状态。
5. 新增类、函数和测试说明优先使用中文注释或 docstring。
6. 排障日志使用 DEBUG 级别，并允许通过配置控制全局日志级别。

## 文档风格

文档应该帮助开发者更快理解和验证真实功能，不追求堆字数。

推荐文档类型：

1. `getting-started`：帮助新开发者判断和跑通。
2. `tutorials`：一步步完成一个具体任务。
3. `how-to`：解决一个明确问题。
4. `reference`：记录稳定命令、API、schema。
5. `internal`：保存阶段设计、历史决策和未稳定方案。

复杂流程优先使用 PlantUML。

## Pull Request 说明

PR 建议包含：

1. 变更目的。
2. 影响范围。
3. 测试命令和结果。
4. 是否影响设备协议、运行产物或端侧行为。
5. 如果涉及 UI、iOS、浏览器、ESP32，请附上日志、截图或 runs 产物说明。

## 安全与隐私

不要提交：

1. API key。
2. Wi-Fi 密码。
3. 设备 token。
4. `.env`。
5. 真实用户音频、图片、视频。
6. 本地运行日志和构建产物。

如果新增工具会产生缓存、日志或构建产物，请同步更新 `.gitignore`。
