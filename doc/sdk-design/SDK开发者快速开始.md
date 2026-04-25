# SDK 开发者快速开始

## 1. 文档目标

本文档用于说明当前仓库中已经可用的 SDK 最小接入方式。

当前阶段的目标不是提供完整生产级 SDK 文档，而是明确：

1. 开发者如何注册一个 `Tool`。
2. 开发者如何注册一个 `Task`。
3. 开发者如何注册一个 `PhoneProcessor`。
4. 开发者如何注册一个 `PhoneTask`。
5. 开发者如何启动真实服务端。
6. 开发者如何执行离线回放测试。

---

## 2. 当前可用能力

当前 `sdk/python/openaiglasses` 已提供以下最小能力：

1. 服务端扩展面
   - `BaseTool`
   - `BaseTask`
   - `DeviceGroupContext`
   - `OpenAIGlassesSDK`
2. 手机侧扩展面
   - `BasePhoneProcessor`
   - `BasePhoneTask`
   - `BaseSensorProvider`
   - `PhoneRuntime`
3. 运行时能力
   - `DeviceGroupRuntime`
   - `TaskRuntimeManager`
   - `HybridTaskGateway`
4. 测试能力
   - `ScenarioRunner`

### 2.1 当前验证状态

截至 2026-04-25，当前仓库已经通过 SDK 联调前预检：

```bash
uv run python script/run_sdk_preflight.py --report logs/sdk-preflight-current.json
```

本次预检结果：

1. `compileall` 通过。
2. `example/server`、`example/phone`、`example/glass` 入口检查通过。
3. `testdata/scenario` 下 4 个回放场景全部通过。
4. 第二期核心 pytest 通过。
5. 服务端 `/api/health` 健康检查通过。

这表示当前可以进入官方 `find_object` 样例的三端真机联调阶段。

---

## 3. 最小服务端接入

### 3.1 创建 SDK

```python
from openaiglasses import OpenAIGlassesSDK

sdk = OpenAIGlassesSDK()
```

### 3.2 注册 Tool

```python
from typing import Any

from pydantic import BaseModel, Field

from openaiglasses import BaseTool, CapabilityResult


class MyToolInput(BaseModel):
    query: str = Field(description="用户请求")


class MyTool(BaseTool):
    name = "my_tool"
    description = "一个最小示例工具"
    input_model = MyToolInput

    def run(self, context, input_data: dict[str, Any]) -> CapabilityResult:
        text = str(input_data.get("query") or "").strip()
        if not text:
            return CapabilityResult.failed(code="invalid_input", message="query 不能为空")
        return CapabilityResult.success(
            data={"echo": text},
            message=f"已处理请求：{text}",
        )


sdk.register_tool(MyTool())
```

### 3.3 注册 Task

```python
from openaiglasses import BaseTask, TaskContext, TaskEvent


class MyTask(BaseTask):
    task_type = "my_task"
    description = "一个最小示例后台任务"

    def on_start(self, context: TaskContext) -> None:
        context.emit_state("running", {"phase": "started"})

    def on_event(self, context: TaskContext, event: TaskEvent) -> None:
        if event.name == "done":
            context.complete({"ok": True})


sdk.register_task(MyTask())
```

### 3.4 启动真实服务端

```python
from infra.config import ServerSettings

settings = ServerSettings.from_env()
settings.validate()

sdk.run_server(settings)
```

如果只想拿到句柄而不立即阻塞，可以使用：

```python
handle = sdk.build_server_handle(settings)
handle.start()
```

---

## 4. 最小手机侧接入

### 4.1 注册 PhoneProcessor

```python
from typing import Any

from openaiglasses.phone import BasePhoneProcessor, PhoneProcessorContext


class MyProcessor(BasePhoneProcessor):
    processor_type = "my_processor"
    description = "最小手机处理器"

    def on_frame(self, context: PhoneProcessorContext, frame: Any) -> None:
        context.emit_result(
            {
                "event_name": "phone.processor.result",
                "text": str(frame),
            }
        )


sdk.register_phone_processor(MyProcessor())
```

### 4.2 注册 PhoneTask

```python
from typing import Any

from openaiglasses.phone import BasePhoneTask, PhoneTaskContext


class MyPhoneTask(BasePhoneTask):
    task_type = "my_phone_task"
    description = "最小手机任务"

    def on_start(self, context: PhoneTaskContext) -> None:
        context.emit_state("running", {"processor_type": "my_processor"})

    def on_frame(self, context: PhoneTaskContext, frame: Any) -> None:
        result = context.process_frame(
            processor_type="my_processor",
            frame=frame,
        )
        if result:
            context.emit_result(result)


sdk.register_phone_task(MyPhoneTask())
```

### 4.3 注册 SensorProvider

```python
from openaiglasses import SensorReading
from openaiglasses.phone import BaseSensorProvider


class HeadingSensorProvider(BaseSensorProvider):
    sensor_type = "heading"

    def read(self) -> SensorReading:
        return SensorReading(
            sensor_type="heading",
            payload={"heading_degrees": 90},
        )


sdk.register_sensor_provider(HeadingSensorProvider())
```

### 4.4 执行手机任务

```python
snapshot = sdk.phone_runtime.start_task(
    task_type="my_phone_task",
    params={},
)

snapshot = sdk.phone_runtime.process_task_frame(
    task_id=snapshot.task_id,
    frame="demo frame",
)
```

---

## 5. 设备组上下文使用方式

`Tool` 和 `Task` 中最重要的对象是 `DeviceGroupContext`。

当前可直接使用的高层能力包括：

1. `require_glass()`
2. `require_phone()`
3. `query_devices()`
4. `capture_photo()`
5. `start_phone_video_link()`
6. `stop_phone_video_link()`
7. `submit_notification()`
8. `create_task()`
9. `query_task()`
10. `cancel_task()`

开发者不应直接处理：

1. WebSocket 连接对象
2. 设备绑定表
3. 媒体链路底层协议
4. 任务存储细节

---

## 6. 离线回放测试

当前最小回放入口有两种：

### 6.1 直接运行 example 下的最小场景

```python
from pathlib import Path

from openaiglasses.testing import ScenarioRunner

result = ScenarioRunner(sdk).run_find_object(
    Path("example/scenario/find_object_basic.json")
)
```

### 6.2 运行 testdata 资产化场景

当开发者希望把帧样例、传感器样例、模型 mock 返回拆分到 `testdata/` 目录复用时，可以直接运行 manifest：

```python
from pathlib import Path

from openaiglasses.testing import ScenarioRunner

result = ScenarioRunner(sdk).run(
    Path("testdata/scenario/find_object_with_testdata.json")
)
```

返回结果中包含：

1. `task_state`
2. `task_result`
3. `notifications`
4. `glass_commands`
5. `phone_results`
6. `assertions`

这意味着开发者可以在没有真机眼镜和手机的情况下，先验证：

1. Tool 是否能创建任务
2. 手机处理器是否能输出结构化结果
3. 任务是否能被事件推进到完成
4. 通知是否能正确产出
5. 场景 manifest 中的 `expected` 是否满足预期

当前推荐的样例资产目录为：

```text
testdata/
  audio/
  image/
  video/
  sensor/
  map/
  text/
  task_event/
  scenario/
```

当前仓库已经提供最小示例：

1. `testdata/text/find_object_frames_water_cup.json`
2. `testdata/scenario/find_object_with_testdata.json`
3. `testdata/task_event/find_object_cancel_timeline.json`
4. `testdata/scenario/find_object_cancelled.json`
5. `testdata/scenario/find_object_missing_phone.json`
6. `testdata/scenario/find_object_video_link_start_failed.json`

推荐直接使用脚本执行回放：

```bash
uv run python script/run_sdk_scenario.py --scenario testdata/scenario/find_object_with_testdata.json --pretty
uv run python script/run_sdk_scenario.py --scenario testdata/scenario/find_object_cancelled.json --pretty
uv run python script/run_sdk_scenario.py --scenario-dir testdata/scenario --pretty
uv run python script/run_sdk_preflight.py --report logs/sdk-preflight.json
uv run python script/sync_sdk_live_config.py
uv run python script/run_sdk_live_check.py --report logs/sdk-live-check.json
```

脚本特点：

1. 回放通过时返回退出码 `0`。
2. 回放断言失败时返回退出码 `1`。
3. 支持 `--report <path>` 输出 JSON 报告，方便联调记录留档。
4. `run_sdk_preflight.py` 会统一执行编译检查、批量 scenario 回放、核心 pytest 和服务健康检查。
5. `sync_sdk_live_config.py` 会把 `config/local_server.env` 中的局域网地址和配对令牌同步到手机与眼镜端本地配置。
6. `run_sdk_live_check.py` 会检查服务端、手机端和眼镜端的设备编号、配对令牌、局域网地址是否一致。

---

## 7. 当前官方示例

当前唯一官方示例位于：

1. `example/server/main.py`
2. `example/capabilities/find_object/server/tool.py`
3. `example/capabilities/find_object/server/task.py`
4. `example/capabilities/find_object/phone/processor.py`
5. `example/capabilities/find_object/phone/task.py`

建议新增能力时，优先参考这一套结构，而不是直接修改 `server/src` 内部实现。

---

## 8. 当前边界说明

当前已经完成：

1. SDK 主入口与真实服务端装配
2. `Tool / Task / PhoneProcessor / PhoneTask / SensorProvider` 注册面
3. `DeviceGroupContext` 高层能力
4. `find_object` 官方样例
5. 最小离线回放测试

当前边界检查结果：

1. `sdk/python/openaiglasses` 不反向依赖 `example`。
2. `example/capabilities/find_object` 不直接处理 WebSocket、设备绑定表和底层媒体协议。
3. `find_object` 业务代码通过 `DeviceGroupContext` 启动视频链路、提交通知和推进任务。
4. 设备缺失、视频链路启动失败、任务取消和正向完成路径均已有 scenario 覆盖。

当前尚未完成：

1. 完整导航样例
2. 手机原生 SDK 发布形态
3. 生产级鉴权与租户隔离
4. 更完整的样例数据目录和 manifest 体系
5. 更丰富的手机侧本地任务调度策略
