# AGENTS.md

本文件是给 coding agent 的项目工作说明。进入本仓库后，先读本文件，再按需阅读 `README.md`、`openaiglass-for-blind/SDK安装与能力开发指南.md` 和相关子目录文档。

## 项目定位

本仓库是 OpenAI Glasses Demo 的重构工程，当前按两个边界维护：

- `openaiglass-sdk`：眼镜、手机、服务器三端 SDK 底座，负责协议、设备绑定、通信、日志、异常处理、Agent/Tool/Task/Skill、MCP、上下文、硬件能力抽象、回放测试和联调工具。
- `openaiglass-for-blind`：基于 SDK 的盲人 AI 眼镜业务工程，负责找物、红绿灯、导航、计时器等真实业务能力，以及业务侧三端宿主配置。

默认功能开发应优先改 `openaiglass-for-blind/capabilities` 和 `openaiglass-for-blind/host`。只有 SDK 公开抽象无法表达业务需求时，才改 `openaiglass-sdk`，并同步更新对应设计文档或阻塞点文档。

## 关键目录

- `openaiglass-sdk/server-python`：服务端 Python SDK 包源码，导入名为 `openaiglasses`。
- `openaiglass-sdk/phone-ios`：通用 iOS 手机运行时，不是业务 App 的默认入口。
- `openaiglass-sdk/glass-esp32`：ESP32 眼镜运行时。
- `openaiglass-sdk/tests`：SDK 单元测试、集成测试、契约测试。
- `openaiglass-for-blind/capabilities`：业务能力实现，新增能力优先参考现有样板。
- `openaiglass-for-blind/host`：盲人业务工程的服务端、手机端、眼镜端薄宿主和本地配置。
- `openaiglass-for-blind/docs`：业务计划、实施记录、验收状态和当前实现状态。
- `openaiglass-for-blind/tests`：业务能力和设备级回放测试。

## 开发环境

- 默认开发机是 macOS，主要使用 Python 3.11。
- 使用 `uv` 管理 Python 环境，优先使用仓库根目录的 `.venv`。
- 服务器部署环境通常是 Linux，可能是 Ubuntu 或 CentOS。
- 本项目包含多端代码：服务端 Python、手机 iOS、眼镜 ESP32。跨端改动要说明联调顺序和观察点。

初始化环境：

```bash
uv sync --python 3.11
uv pip install -e openaiglass-sdk/server-python
```

如果 `uv run openaiglass...` 找不到命令，重新执行 editable 安装。

## 常用命令

同步本机联调配置：

```bash
uv run openaiglass.config.sync --app-root openaiglass-for-blind
```

启动服务端：

```bash
uv run openaiglass.server.run \
  --app-module host.server.main \
  --app-root openaiglass-for-blind
```

启动手机 mock：

```bash
uv run openaiglass.phone.mock \
  --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json
```

打开业务侧 iOS 工程：

```bash
uv run openaiglass.phone.open --app-root openaiglass-for-blind
```

启动真实眼镜或回放眼镜：

```bash
uv run openaiglass.glass.start \
  --app-root openaiglass-for-blind \
  --sdk-root openaiglass-sdk \
  --port '/dev/tty.usbmodem*'

uv run openaiglass.glass.start \
  --runtime playback \
  --config <glass-playback.json> \
  --sdk-root openaiglass-sdk
```

预检和包检查：

```bash
uv run openaiglass.sdk.preflight \
  --report openaiglass-for-blind/logs/sdk-preflight-current.json

uv run openaiglass.sdk.package-check --repo-root .
```

## 测试要求

测试的目标是暴露真实问题、确认功能是否完成，不是只追求测例变绿。改动后优先运行与改动面匹配的最小测试，再按风险扩大范围。

常用测试命令：

```bash
uv run python -m pytest openaiglass-sdk/tests -q
uv run python -m pytest openaiglass-for-blind/tests -q
uv run python -m pytest -q
```

如果直接运行 `pytest` 遇到 Python 版本、路径或 entry point 问题，优先使用 `uv run python -m pytest`。

### 设备级自动化回放验证

完成服务端、Tool、Task、Skill、设备协议或业务能力代码后，不能只看单元测试结果。应尽量把本地服务真正拉起来，用 `phone-mock`、`glass-playback` 和 `openaiglass-for-blind/testdata` 中已有样例做一次设备级数据回放，观察真实链路日志和运行产物。

推荐流程：

1. 同步本机联调配置，确保服务端地址、设备号和配对令牌一致，不过不用每次都执行同步。
2. 以 `DEBUG` 日志启动服务端，并把日志写入 `openaiglass-for-blind/logs/`。
3. 启动 `phone-mock`，把 mock 手机端日志也写入文件。
4. 启动 `glass-playback`，使用 `openaiglass-for-blind/host/glass-playback/config` 中的配置和 `openaiglass-for-blind/testdata` 中的音频、图片、视频、传感器样例完成真实数据回放。
5. 通过 `/api/runtime/devices`、服务端日志、`phone-mock` 日志、`glass-playback` 日志、`runs/playback/.../events.jsonl`、`runs/playback/.../actuators.jsonl` 和服务端业务产物判断变更是否有效。
6. 回放失败时，优先定位真实链路中的失败点，例如设备注册、绑定、语音会话打开、触发音频上传、ASR、Agent、Tool 调用、Task 事件、手机任务、执行器播放或通知输出，并尝试修复到可用。

示例命令：

```bash
mkdir -p openaiglass-for-blind/logs

uv run openaiglass.config.sync --app-root openaiglass-for-blind

LOG_LEVEL=DEBUG uv run openaiglass.server.start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env \
  --log-file openaiglass-for-blind/logs/server-agent-check.log

uv run openaiglass.phone.mock \
  --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json \
  > openaiglass-for-blind/logs/phone-mock-agent-check.log 2>&1 &

uv run openaiglass.glass.start \
  --runtime playback \
  --config openaiglass-for-blind/host/glass-playback/config/look_look.json \
  > openaiglass-for-blind/logs/glass-playback-agent-check.log 2>&1 &

curl http://127.0.0.1:8765/api/runtime/devices
```

如果本次能力不适合 `look_look.json`，应选用或补充与能力匹配的 `glass-playback` 配置，但输入资产优先放在 `openaiglass-for-blind/testdata` 下。验证完成后，应停止后台服务，避免残留进程影响下一轮测试：

```bash
uv run openaiglass.server.stop
```

最终交付说明中要写明本次是否执行了设备级回放、使用了哪个配置和样例数据、关键日志文件位置、观察到的成功事件或失败点。单元测试通过但设备级回放失败时，不能把任务视为已经完成。

新增或修改单元测试时，测试注释应说明：

- 测试目标：要验证什么能力或异常。
- 测试方法：如何构造输入、mock 或回放数据。
- 预期结果：期望的状态、事件、返回值或副作用。

涉及跨设备能力时，最终答复或文档中必须给出联调方案，至少包含：

1. 同步配置。
2. 启动服务端。
3. 启动 iOS App 或 `phone-mock`。
4. 启动 ESP32 眼镜或 `glass-playback`。
5. 触发业务能力。
6. 观察服务端、手机端、眼镜端日志和任务事件。

## 代码规范

- Python 代码优先沿用现有模块结构和公开 SDK API，不绕过 `DeviceGroupContext`、Tool/Task/Skill 注册面直接访问内部状态。
- 新增类必须写中文注释，说明主要功能、主要方法和主要属性。
- 新增函数必须写中文注释，说明函数功能、主要逻辑、参数、返回值和异常情况。
- 注释应解释业务意图或复杂逻辑，避免只复述代码。
- 不确定或复杂实现先搜索成熟方案或同类项目做法，再决定是否自研。
- 新业务能力优先复制现有样板：`find_object`、`traffic_light`、`navigation`、`timer`。
- 不要在业务迭代中把 SDK 内部实现当作临时扩展点；如果 SDK 能力不足，先记录阻塞点并收敛公开接口。

## 日志与配置

- 排障日志使用 `DEBUG` 级别，不要用 `print` 替代正式日志。
- 运行状态、用户可见异常和设备链路关键事件应使用合适的日志级别。
- 全局日志级别应能通过配置文件调整，本地开发优先支持 `DEBUG`。
- 不要提交本地配置、设备令牌、WiFi 密码、音频样例、视频样例或生成日志。

常见本地配置文件已经在 `.gitignore` 中忽略，包括：

- `openaiglass-for-blind/config/local_server.env`
- `openaiglass-for-blind/host/glass/config/local_build.env`
- `openaiglass-for-blind/host/phone/config/AppConfig.plist`

## 文档规范

- 写文档是为了记录重要事实、设计边界、联调结果或阻塞点，不为了堆字数。
- 文档必须和真实代码、真实命令、真实测试结果保持一致。
- 复杂架构、流程、时序优先使用 PlantUML。
- 修改协议、三端职责、Task/Tool/Skill 语义、设备启动链路时，同步更新相关文档。
- 当前实现状态优先参考 `openaiglass-for-blind/docs/当前实现状态.md` 和 `SDK对功能开发支持情况的说明.md`。

## Git 规范

- 提交信息使用中文，简短描述代码总变更。
- 不允许将任何分支直接 push 到远程。
- 不要提交构建产物、缓存、设备日志、真实媒体数据或本地私有配置。
- 如果新增工具链会产生编译结果或缓存目录，必须同步检查 `.gitignore`。
- 工作区可能已有其他人改动。不要回滚自己没有修改的文件；如果这些改动影响当前任务，先读懂并基于现状继续。

## 安全与隐私

- 不要把 API Key、设备 token、WiFi 密码、局域网设备身份信息提交到仓库。
- 不要把真实用户音频、图片、视频直接加入测试数据；需要样例时使用脱敏或合成数据。
- 涉及摄像头、麦克风、定位、导航等能力时，代码和文档都要明确触发条件、数据流向和关闭方式。

## 交付前检查

完成代码改动前，至少确认：

- 改动落在正确边界：业务能力在 `openaiglass-for-blind`，SDK 底座在 `openaiglass-sdk`。
- 已运行与改动相关的最小测试，或说明为什么当前无法运行。
- 涉及跨端链路时，已给出服务端、手机端、眼镜端的启动和观察方案。
- 文档、配置模板、`.gitignore` 与实现保持一致。
- 没有提交本地配置、密钥、生成产物或真实媒体数据。
