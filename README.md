# OpenAI Glasses Demo 2

本仓库当前是 OpenAI 眼镜多端 Demo 与 SDK 产品化原型，主要包含服务端 Python 运行时、ESP32 眼镜端、iOS 手机端、SDK 包骨架和官方 example。

开发人员优先从 [doc/README.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/README.md) 开始阅读。该入口会按“当前实现状态、架构设计、阶段实施、SDK 使用、联调验证”的顺序串起文档。

## 当前主要入口

| 入口 | 说明 |
| --- | --- |
| [sdk/python/app/main.py](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/sdk/python/app/main.py) | SDK 内真实服务端入口，启动 HTTP、控制 WebSocket、音频 WebSocket 和语音运行时。 |
| [example/server/main.py](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/example/server/main.py) | SDK 官方示例服务端入口，会注册 `find_object` 示例能力。 |
| [sdk/python/openaiglasses](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/sdk/python/openaiglasses) | Python SDK 包骨架与运行时抽象。 |
| [phone/ios](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/phone/ios) | iOS 手机端通用 SDK运行时，负责注册、控制消息、视频接收和手机任务承载。 |
| [glass/src](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/glass/src) | ESP32 眼镜端工程。 |
| [example](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/example) | SDK 官方示例工程，包含 `find_object` 能力与回放场景。 |

## Python SDK 安装

SDK 已按独立 Python 包组织在 [sdk/python](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/sdk/python) 下。发布后外部开发者使用：

```bash
pip install openaiglasses-sdk
```

仓库内或发布前本地验证使用：

```bash
pip install ./sdk/python
python script/run_sdk_package_check.py
```

安装后的公开导入入口是 `openaiglasses`，开发者不需要配置 `PYTHONPATH=server/src:sdk/python`。

## 常用命令

```bash
PYTHONPATH=sdk/python:. uv run python sdk/python/app/main.py
PYTHONPATH=sdk/python:. uv run python example/server/main.py
uv run python script/run_sdk_scenario.py --scenario-dir testdata/scenario --pretty
uv run python script/run_sdk_preflight.py --report logs/sdk-preflight-stage2-package.json
```

跨设备联调前先看 [SDK真机联调前检查与联调步骤.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/sdk-design/SDK真机联调前检查与联调步骤.md)。
