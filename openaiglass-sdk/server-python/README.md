# openaiglasses-sdk

`openaiglasses-sdk` 是面向“眼镜 + 手机 + 服务器”组合设备模式的 Python SDK。

SDK 的目标是让业务开发者只关注能力实现，例如 Tool、Task、PhoneProcessor 和 PhoneTask，不需要直接处理设备注册、设备组绑定、WebSocket、视频链路、任务状态机和运行上下文维护。高频自测通过设备级 `glass-playback` 完成。

## 安装

开发者项目中使用：

```bash
pip install openaiglasses-sdk
```

当前仓库内本地验证使用：

```bash
pip install ./openaiglass-sdk/server-python
```

## 最小服务端能力

```python
from typing import Any

from pydantic import BaseModel, Field

from openaiglasses import BaseTool, CapabilityResult, OpenAIGlassesSDK, ServerSettings


class EchoInput(BaseModel):
    text: str = Field(description="用户输入")


class EchoTool(BaseTool):
    name = "echo"
    description = "返回用户输入"
    input_model = EchoInput

    def run(self, context, input_data: dict[str, Any]) -> CapabilityResult:
        text = str(input_data.get("text") or "").strip()
        if not text:
            return CapabilityResult.failed(code="invalid_input", message="text 不能为空")
        return CapabilityResult.success(data={"text": text}, message=text)


sdk = OpenAIGlassesSDK()
sdk.register_tool(EchoTool())

settings = ServerSettings.from_env()
sdk.run_server(settings)
```

## 最小后台任务

```python
from openaiglasses import BaseTask, OpenAIGlassesSDK, TaskContext, TaskEvent


class DemoTask(BaseTask):
    task_type = "demo_task"
    description = "演示任务"

    def on_start(self, context: TaskContext) -> None:
        context.emit_state("running", {"phase": "started"})

    def on_event(self, context: TaskContext, event: TaskEvent) -> None:
        if event.name == "done":
            context.complete({"ok": True})


sdk = OpenAIGlassesSDK()
sdk.register_task(DemoTask())
```

## 最小手机侧能力

```python
from typing import Any

from openaiglasses import BasePhoneProcessor, BasePhoneTask, OpenAIGlassesSDK, PhoneProcessorContext, PhoneTaskContext


class DemoProcessor(BasePhoneProcessor):
    processor_type = "demo_processor"
    description = "演示手机处理器"

    def on_frame(self, context: PhoneProcessorContext, frame: Any) -> None:
        context.emit_result({"event_name": "phone.demo.result", "text": str(frame)})


class DemoPhoneTask(BasePhoneTask):
    task_type = "demo_phone_task"
    description = "演示手机任务"

    def on_frame(self, context: PhoneTaskContext, frame: Any) -> None:
        result = context.process_frame("demo_processor", frame)
        if result:
            context.emit_result(result)


sdk = OpenAIGlassesSDK()
sdk.register_phone_processor(DemoProcessor())
sdk.register_phone_task(DemoPhoneTask())
```

## 设备级回放

`glass-playback` 是与 `server-python` 同级的设备组件，主体代码位于 `openaiglass-sdk/glass-playback`。`server-python` 只提供统一启动命令。

```bash
openaiglass.glass.start --runtime playback \
  --config openaiglass-for-blind/host/glass-playback/config/glass.water_cup.json
```

更完整的开发步骤见仓库文档：`openaiglass-sdk/docs/sdk-design/SDK开发者快速开始.md`。
