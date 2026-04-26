# SDK 安装与能力开发指南

## 1. 文档目标

本文面向使用 SDK 的外部开发者。

开发者目标不是理解 SDK 内部系统实现，而是基于 SDK 提供的公开基类完成业务能力开发：

1. 服务端 Tool：让模型可以调用一个业务能力入口。
2. 服务端 Task：承接长时间运行的业务流程。
3. 手机端 PhoneProcessor：处理手机侧视频帧、传感器或本地模型结果。
4. 手机端 PhoneTask：承接手机侧持续任务。
5. Scenario：在没有真机设备时做离线回放验证。

开发者不需要直接处理：

1. 设备注册。
2. 设备组绑定。
3. 控制 WebSocket。
4. 眼镜和手机之间的视频链路。
5. 任务状态机存储。
6. 运行上下文维护。

## 2. 安装 SDK

正式发布后，在开发者项目中执行：

```bash
pip install openaiglasses-sdk
```

当前仓库本地开发或发布前验证，可以执行：

```bash
pip install ./openaiglass-sdk/python
```

安装后，公开导入入口是：

```python
import openaiglasses
```

常用公开 API：

```python
from openaiglasses import (
    BasePhoneProcessor,
    BasePhoneTask,
    BaseTask,
    BaseTool,
    CapabilityResult,
    OpenAIGlassesSDK,
    PhoneProcessorContext,
    PhoneTaskContext,
    ScenarioRunner,
    ServerSettings,
    TaskContext,
    TaskEvent,
)
```

## 3. 推荐能力项目结构

外部项目建议采用如下目录：

```text
my-glasses-capability/
  pyproject.toml
  src/
    my_capability/
      __init__.py
      server/
        tool.py
        task.py
      phone/
        processor.py
        task.py
      scenario.py
      main.py
  testdata/
    scenario/
      my_capability_basic.json
```

最小 `pyproject.toml`：

```toml
[project]
name = "my-glasses-capability"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "openaiglasses-sdk>=0.1.0",
    "pydantic>=2,<3",
]
```

## 4. 开发服务端 Tool

Tool 是模型可以调用的业务入口。它应该只表达“业务想做什么”，不要处理底层设备连接。

示例：

```python
from typing import Any

from pydantic import BaseModel, Field

from openaiglasses import BaseTool, CapabilityResult


class StartDemoInput(BaseModel):
    target: str = Field(description="用户希望处理的目标")


class StartDemoTool(BaseTool):
    name = "start_demo"
    description = "启动一个演示能力"
    input_model = StartDemoInput

    def run(self, context, input_data: dict[str, Any]) -> CapabilityResult:
        target = str(input_data.get("target") or "").strip()
        if not target:
            return CapabilityResult.failed(code="invalid_input", message="target 不能为空")

        task = context.create_task(
            task_type="demo_task",
            input_data={"target": target},
        )
        return CapabilityResult.success(
            data={"task_id": task.task_id, "target": target},
            message=f"已启动演示任务：{target}",
        )
```

Tool 中可以使用 `context` 提供的高层能力，例如：

1. `context.require_glass()`
2. `context.require_phone()`
3. `context.query_devices()`
4. `context.capture_photo()`
5. `context.start_phone_video_link()`
6. `context.stop_phone_video_link()`
7. `context.create_task()`
8. `context.query_task()`
9. `context.cancel_task()`
10. `context.start_phone_task()`
11. `context.stop_phone_task()`
12. `context.submit_notification()`

## 5. 开发服务端 Task

Task 适合承接长流程能力，例如找物、导航、识别、持续观察。

示例：

```python
from openaiglasses import BaseTask, TaskContext, TaskEvent


class DemoTask(BaseTask):
    task_type = "demo_task"
    description = "演示后台任务"

    def on_start(self, context: TaskContext) -> None:
        target = str(context.input_data.get("target") or "")
        context.update({"target": target})
        context.emit_state("running", {"phase": "started", "target": target})
        context.start_phone_task(
            task_type="demo_phone_task",
            params={"target": target},
        )

    def on_event(self, context: TaskContext, event: TaskEvent) -> None:
        if event.name == "phone.demo.result":
            context.complete(dict(event.payload))
```

Task 不应该直接处理 WebSocket、设备绑定表或 HTTP 连接对象。

## 6. 开发手机端 PhoneProcessor

PhoneProcessor 负责处理手机侧输入，例如视频帧、传感器数据、本地模型结果。

示例：

```python
from typing import Any

from openaiglasses import BasePhoneProcessor, PhoneProcessorContext


class DemoProcessor(BasePhoneProcessor):
    processor_type = "demo_processor"
    description = "演示手机处理器"

    def on_frame(self, context: PhoneProcessorContext, frame: Any) -> None:
        text = str(frame)
        context.emit_result(
            {
                "event_name": "phone.demo.result",
                "summary": f"已处理帧：{text}",
            }
        )
```

## 7. 开发手机端 PhoneTask

PhoneTask 负责组织手机侧持续任务，例如持续处理眼镜推送的视频帧。

示例：

```python
from typing import Any

from openaiglasses import BasePhoneTask, PhoneTaskContext


class DemoPhoneTask(BasePhoneTask):
    task_type = "demo_phone_task"
    description = "演示手机任务"

    def on_start(self, context: PhoneTaskContext) -> None:
        context.emit_state("running", {"phase": "waiting_frame"})

    def on_frame(self, context: PhoneTaskContext, frame: Any) -> None:
        result = context.process_frame(
            processor_type="demo_processor",
            frame=frame,
        )
        if result:
            context.emit_result(result)
```

## 8. 装配 SDK 并启动服务端

开发者项目应提供一个装配入口，只注册业务能力，然后交给 SDK 启动系统运行时。

示例：

```python
from openaiglasses import OpenAIGlassesSDK, ServerSettings

from my_capability.phone.processor import DemoProcessor
from my_capability.phone.task import DemoPhoneTask
from my_capability.server.task import DemoTask
from my_capability.server.tool import StartDemoTool


def create_sdk() -> OpenAIGlassesSDK:
    sdk = OpenAIGlassesSDK()
    sdk.register_tool(StartDemoTool())
    sdk.register_task(DemoTask())
    sdk.register_phone_processor(DemoProcessor())
    sdk.register_phone_task(DemoPhoneTask())
    return sdk


def main() -> None:
    settings = ServerSettings.from_env()
    create_sdk().run_server(settings)


if __name__ == "__main__":
    main()
```

启动：

```bash
python -m my_capability.main
```

## 9. 离线回放验证

开发者可以先不接真机，通过 `ScenarioRunner` 验证能力逻辑。

最小调用：

```python
from pathlib import Path

from openaiglasses import ScenarioRunner

from my_capability.main import create_sdk


result = ScenarioRunner(create_sdk()).run(
    Path("testdata/scenario/my_capability_basic.json")
)
assert result.assertions["passed"]
```

场景文件应描述：

1. 设备组中有哪些 mock 设备。
2. 要启动哪个任务或触发哪个 Tool。
3. 输入帧、传感器、任务事件的时间线。
4. 期望的任务状态、结果、通知和设备命令。

可以参考本仓库已有样例：

1. [capabilities/find_object/server/tool.py](./capabilities/find_object/server/tool.py)
2. [capabilities/find_object/server/task.py](./capabilities/find_object/server/task.py)
3. [capabilities/find_object/phone/processor.py](./capabilities/find_object/phone/processor.py)
4. [capabilities/find_object/phone/task.py](./capabilities/find_object/phone/task.py)
5. [capabilities/find_object/scenario.py](./capabilities/find_object/scenario.py)
6. [testdata/scenario/find_object_basic.json](./testdata/scenario/find_object_basic.json)

## 10. 本仓库业务工程验证命令

如果开发者直接使用本仓库盲人业务工程学习，可以执行：

```bash
python ../openaiglass-sdk/scripts/run_sdk_package_check.py
python scripts/run_sdk_scenario.py --scenario-dir testdata/scenario --pretty
python scripts/run_sdk_preflight.py --report ../logs/sdk-preflight-blind-dev.json
```

其中：

1. `run_sdk_package_check.py` 验证 SDK 可以构建 wheel 并通过 pip 安装后导入。
2. `run_sdk_scenario.py` 验证离线回放能力。
3. `run_sdk_preflight.py` 验证 SDK 包、边界、契约、兼容性和服务健康检查。

## 11. 真机联调顺序

离线回放通过后，再做真机联调：

1. 启动服务端能力项目。
2. 启动手机端 SDK运行时。
3. 启动眼镜端 SDK运行时。
4. 确认设备注册、绑定和心跳正常。
5. 通过语音或调试入口触发 Tool。
6. 观察 Task 状态、手机端结果回传、眼镜端播报或提示。

本仓库真机前检查命令：

```bash
bash scripts/sync_sdk_live_config.sh
bash scripts/run_sdk_live_check.sh --report ../logs/sdk-live-check-blind-dev.json
```

## 12. 开发者不要做的事

为了保持能力可移植，业务代码不要：

1. 修改 `openaiglass-sdk/python` 内部运行时。
2. 修改 `openaiglass-sdk/python` 内部运行时。
3. 直接读写设备绑定表。
4. 直接拼控制 WebSocket 消息。
5. 为单个能力新增专用系统接口。
6. 在 `host/phone` 或 `host/glass` 的宿主运行时代码里写具体业务逻辑。

如果一个能力需要新的系统级能力，应先把需求抽象成 SDK 的公开接口，再由 SDK 维护者实现。
