"""业务能力单元测试。

测试目标：
1. 验证业务侧 Tool 只通过 SDK 上下文创建任务或调用 MCP。
2. 验证业务侧 Task 只通过 SDK TaskContext 与 device_group 推进状态和提交通知。
3. 在不启动真实服务端、手机和眼镜的情况下，尽早暴露业务逻辑回归。

测试方法：
1. 使用轻量 Fake 上下文模拟 SDK 已公开的能力入口。
2. 直接调用 Tool.run、Task.on_start、Task.on_event 和 Task.on_cancel。
3. 断言任务创建参数、MCP 调用顺序、通知优先级和任务状态。

预期结果：
1. 所有测试均可用标准库 unittest 运行。
2. 测试不依赖 phone-mock、真实 iPhone、真实眼镜或模型服务。
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (
    REPO_ROOT / "openaiglass-sdk" / "server-python",
    REPO_ROOT / "openaiglass-for-blind",
    REPO_ROOT,
):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from openaiglasses import CapabilityResult, TaskContext, TaskEvent

from capabilities.find_object.server.task import FindObjectTask
from capabilities.navigation.server.task import NavigationTask
from capabilities.navigation.server.tool import PrepareNavigationTool
from capabilities.search.mcp.web_search_adapter import WebSearchMcpAdapter
from capabilities.search.server.tool import SearchWebTool
from capabilities.timer.server.task import TimerTask
from capabilities.timer.server.tool import StartTimerTool
from capabilities.traffic_light.server.task import TrafficLightTask


@dataclass(slots=True)
class FakeTaskRuntime:
    """测试用任务运行时快照。

    主要功能：
    1. 模拟 SDK create_task 返回的任务对象。
    2. 保留 task_id、task_type、state 和 data 字段，供 Tool 输出使用。
    """

    task_type: str
    input_data: dict[str, Any]
    task_id: str = ""
    state: str = "created"
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """补齐默认 task_id 和 data。

        参数：
        1. 无。

        返回值：
        1. 无。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        if not self.task_id:
            self.task_id = f"{self.task_type}-001"
        if not self.data:
            self.data = dict(self.input_data)


class FakeToolContext:
    """测试用 Tool 上下文。

    主要功能：
    1. 记录业务 Tool 创建任务的参数。
    2. 按测试预置返回 MCP 调用结果。
    """

    def __init__(self, mcp_responses: dict[str, CapabilityResult] | None = None) -> None:
        """初始化测试上下文。

        参数：
        1. `mcp_responses`：按 MCP 方法名配置的返回结果。

        返回值：
        1. 无。

        异常情况：
        1. 未配置的 MCP 方法会返回结构化失败结果。
        """

        self.created_tasks: list[FakeTaskRuntime] = []
        self.mcp_calls: list[tuple[str, dict[str, Any]]] = []
        self._mcp_responses = dict(mcp_responses or {})

    def create_task(self, *, task_type: str, input_data: dict[str, Any]) -> FakeTaskRuntime:
        """模拟 SDK 创建任务。

        参数：
        1. `task_type`：任务类型。
        2. `input_data`：任务输入。

        返回值：
        1. `FakeTaskRuntime`。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        task = FakeTaskRuntime(task_type=task_type, input_data=dict(input_data))
        self.created_tasks.append(task)
        return task

    def mcp(self, name: str, params: dict[str, Any]) -> CapabilityResult:
        """模拟 SDK MCP 调用。

        参数：
        1. `name`：MCP 方法名。
        2. `params`：MCP 调用参数。

        返回值：
        1. `CapabilityResult`。

        异常情况：
        1. 未配置方法时返回 `mcp_not_configured`。
        """

        self.mcp_calls.append((name, dict(params)))
        result = self._mcp_responses.get(name)
        if result is None:
            return CapabilityResult.failed(
                code="mcp_not_configured",
                message=f"测试未配置 MCP 方法：{name}",
            )
        return result


class FakeDeviceGroup:
    """测试用设备组上下文。

    主要功能：
    1. 记录业务 Task 对手机视频链路、手机任务和通知能力的调用。
    2. 避免单元测试依赖真实设备或 phone-mock。
    """

    def __init__(self) -> None:
        """初始化调用记录。

        参数：
        1. 无。

        返回值：
        1. 无。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        self.video_link_starts: list[dict[str, Any]] = []
        self.video_link_stops: list[dict[str, Any]] = []
        self.phone_task_starts: list[dict[str, Any]] = []
        self.phone_task_stops: list[dict[str, Any]] = []
        self.notifications: list[dict[str, str]] = []

    def start_phone_video_link(self, *, reason: str, params: dict[str, Any]) -> dict[str, Any]:
        """记录启动手机视频链路请求。

        参数：
        1. `reason`：启动链路的业务原因。
        2. `params`：视频链路参数。

        返回值：
        1. 模拟 SDK 返回的结构化结果。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        payload = {"reason": reason, "params": dict(params)}
        self.video_link_starts.append(payload)
        return {"ok": True, **payload}

    def stop_phone_video_link(self, *, reason: str) -> dict[str, Any]:
        """记录停止手机视频链路请求。

        参数：
        1. `reason`：停止链路的业务原因。

        返回值：
        1. 模拟 SDK 返回的结构化结果。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        payload = {"reason": reason}
        self.video_link_stops.append(payload)
        return {"ok": True, **payload}

    def start_phone_task(self, *, task_type: str, params: dict[str, Any]) -> dict[str, Any]:
        """记录启动手机任务请求。

        参数：
        1. `task_type`：手机侧业务任务类型。
        2. `params`：手机任务参数。

        返回值：
        1. 模拟 SDK 返回的结构化结果。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        payload = {"task_type": task_type, "params": dict(params)}
        self.phone_task_starts.append(payload)
        return {"ok": True, **payload}

    def stop_phone_task(self, *, task_type: str, reason: str) -> dict[str, Any]:
        """记录停止手机任务请求。

        参数：
        1. `task_type`：手机侧业务任务类型。
        2. `reason`：停止任务的业务原因。

        返回值：
        1. 模拟 SDK 返回的结构化结果。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        payload = {"task_type": task_type, "reason": reason}
        self.phone_task_stops.append(payload)
        return {"ok": True, **payload}

    def submit_notification(self, *, text: str, priority: str) -> dict[str, str]:
        """记录提交给眼镜的通知。

        参数：
        1. `text`：通知文本。
        2. `priority`：通知优先级。

        返回值：
        1. 被记录的通知对象。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        payload = {"text": text, "priority": priority}
        self.notifications.append(payload)
        return payload


class TimerCapabilityTests(unittest.TestCase):
    """计时器能力单元测试。"""

    def test_start_timer_rejects_invalid_duration(self) -> None:
        """测试目标：非法时长应返回结构化失败。

        测试方法：直接调用 StartTimerTool.run，传入 0 秒时长。
        预期结果：不创建任务，错误码为 invalid_input。
        """

        context = FakeToolContext()
        result = StartTimerTool().run(context, {"duration_seconds": 0})

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code if result.error else "", "invalid_input")
        self.assertEqual(context.created_tasks, [])

    def test_start_timer_creates_sdk_task(self) -> None:
        """测试目标：合法时长应通过 SDK 上下文创建 timer_task。

        测试方法：传入 30 秒和自定义提示文本。
        预期结果：Tool 输出 task_id，创建任务参数完整保留。
        """

        context = FakeToolContext()
        result = StartTimerTool().run(
            context,
            {
                "duration_seconds": 30,
                "label": "喝水提醒",
                "notify_text": "该喝水了",
                "enable_background_timer": False,
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["task_type"], "timer_task")
        self.assertEqual(result.data["task_id"], "timer_task-001")
        self.assertEqual(len(context.created_tasks), 1)
        self.assertEqual(context.created_tasks[0].input_data["notify_text"], "该喝水了")

    def test_timer_task_lifecycle(self) -> None:
        """测试目标：计时器 Task 能响应启动、tick、完成和取消事件。

        测试方法：构造 TaskContext 后直接调用生命周期方法。
        预期结果：状态和通知随事件推进，完成事件写入 result。
        """

        group = FakeDeviceGroup()
        context = TaskContext(
            task_id="timer-001",
            input={
                "duration_seconds": 10,
                "label": "厨房计时",
                "notify_text": "关火",
                "enable_background_timer": False,
            },
            device_group=group,
        )
        task = TimerTask()

        task.on_start(context)
        self.assertEqual(context.state, "running")
        self.assertEqual(context.data["remaining_seconds"], 10)
        self.assertEqual(group.notifications[-1]["priority"], "normal")

        task.on_event(context, TaskEvent(name="timer.tick", payload={"remaining_seconds": 3}))
        self.assertEqual(context.data["remaining_seconds"], 3)

        task.on_event(context, TaskEvent(name="timer.finished", payload={}))
        self.assertEqual(context.state, "completed")
        self.assertEqual(context.result["label"], "厨房计时")
        self.assertEqual(group.notifications[-1], {"text": "关火", "priority": "high"})

        cancel_context = TaskContext(
            task_id="timer-002",
            input={"duration_seconds": 10, "label": "备用计时", "enable_background_timer": False},
            device_group=group,
            data={"label": "备用计时"},
        )
        task.on_cancel(cancel_context)
        self.assertEqual(cancel_context.state, "cancelled")
        self.assertEqual(group.notifications[-1], {"text": "备用计时已取消", "priority": "normal"})

    def test_timer_task_background_finish_notifies_user(self) -> None:
        """测试目标：计时器到点后应由后台倒计时推进完成并通知用户。

        测试方法：启动 1 秒计时器，等待后台 Timer 触发。
        预期结果：任务进入 completed，最后一条通知为自定义完成提示。
        """

        import time

        group = FakeDeviceGroup()
        context = TaskContext(
            task_id="timer-bg-001",
            input={"duration_seconds": 1, "label": "测试计时", "notify_text": "测试时间到了"},
            device_group=group,
        )

        TimerTask().on_start(context)
        time.sleep(1.2)

        self.assertEqual(context.state, "completed")
        self.assertEqual(context.result["finished"], True)
        self.assertEqual(group.notifications[-1], {"text": "测试时间到了", "priority": "high"})


class SearchCapabilityTests(unittest.TestCase):
    """搜索能力单元测试。"""

    def test_search_web_tool_calls_mcp(self) -> None:
        """测试目标：搜索 Tool 应通过 SDK MCP 入口执行搜索。

        测试方法：配置 fake `web.search` 返回结果后直接调用 Tool。
        预期结果：MCP 调用参数正确，Tool 输出搜索结果。
        """

        context = FakeToolContext(
            {
                "web.search": CapabilityResult.success(
                    data={
                        "query": "大模型是什么",
                        "results": [{"title": "大模型介绍", "url": "https://example.com"}],
                        "result_count": 1,
                    },
                    message="找到 1 条搜索结果",
                )
            }
        )

        result = SearchWebTool().run(context, {"query": "大模型是什么", "max_results": 3})

        self.assertTrue(result.ok)
        self.assertEqual(context.mcp_calls, [("web.search", {"query": "大模型是什么", "max_results": 3})])
        self.assertEqual(result.data["result_count"], 1)

    def test_web_search_adapter_parses_duckduckgo_html(self) -> None:
        """测试目标：搜索 adapter 能把 HTML 搜索结果解析成结构化列表。

        测试方法：传入最小 DuckDuckGo HTML 片段。
        预期结果：返回标题、摘要和链接。
        """

        body = """
        <div class="result__body">
          <a rel="nofollow" class="result__a" href="https://example.com">标题 &amp; 测试</a>
          <a class="result__snippet">这是摘要</a>
        </div></div>
        """

        results = WebSearchMcpAdapter._parse_duckduckgo_html(body, max_results=2)

        self.assertEqual(results, [{"title": "标题 & 测试", "snippet": "这是摘要", "url": "https://example.com"}])

    def test_web_search_adapter_parses_bocha_response(self) -> None:
        """测试目标：搜索 adapter 能把博查 AI Search 响应解析成统一结果。

        测试方法：传入最小博查 Web Search JSON 响应。
        预期结果：优先使用 summary，并保留来源和展示链接。
        """

        payload = {
            "code": 200,
            "data": {
                "webPages": {
                    "value": [
                        {
                            "name": "大模型是什么",
                            "url": "https://example.cn/llm",
                            "displayUrl": "example.cn/llm",
                            "summary": "大模型是一类参数规模较大的模型。",
                            "snippet": "旧摘要",
                            "siteName": "示例站点",
                        }
                    ]
                }
            },
        }

        results = WebSearchMcpAdapter._parse_bocha_response(payload, max_results=3)

        self.assertEqual(
            results,
            [
                {
                    "title": "大模型是什么",
                    "snippet": "大模型是一类参数规模较大的模型。",
                    "url": "https://example.cn/llm",
                    "source": "示例站点",
                    "display_url": "example.cn/llm",
                }
            ],
        )


class NavigationCapabilityTests(unittest.TestCase):
    """导航能力单元测试。"""

    def test_prepare_navigation_requires_destination(self) -> None:
        """测试目标：目的地为空时不调用 MCP。

        测试方法：传入空 destination。
        预期结果：返回 invalid_input 且无 MCP 调用。
        """

        context = FakeToolContext()
        result = PrepareNavigationTool().run(context, {"destination": "  "})

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code if result.error else "", "invalid_input")
        self.assertEqual(context.mcp_calls, [])

    def test_prepare_navigation_can_wait_for_poi_confirmation(self) -> None:
        """测试目标：需要用户确认时只返回候选，不创建导航任务。

        测试方法：配置 POI 搜索结果并设置 require_confirmation。
        预期结果：只调用 poi_search，结果包含 awaiting_confirmation。
        """

        context = FakeToolContext(
            {
                "amap.poi_search": CapabilityResult.success(
                    data={
                        "candidates": [
                            {"poi_id": "p1", "name": "桂林路地铁站"},
                            {"poi_id": "p2", "name": "桂林公园"},
                        ]
                    }
                )
            }
        )
        result = PrepareNavigationTool().run(
            context,
            {"destination": "桂林路", "require_confirmation": True},
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.data["awaiting_confirmation"])
        self.assertEqual(context.created_tasks, [])
        self.assertEqual([name for name, _ in context.mcp_calls], ["amap.poi_search"])

    def test_prepare_navigation_creates_task_after_route_ready(self) -> None:
        """测试目标：路线规划成功后创建 navigation_task。

        测试方法：依次返回 POI、地理编码和路线规划结果。
        预期结果：MCP 调用顺序正确，任务输入包含路线和选中 POI。
        """

        context = FakeToolContext(
            {
                "amap.poi_search": CapabilityResult.success(
                    data={
                        "candidates": [
                            {"poi_id": "p1", "name": "错误地点"},
                            {"poi_id": "p2", "name": "桂林路地铁站"},
                        ]
                    }
                ),
                "amap.geocode": CapabilityResult.success(
                    data={"poi_id": "p2", "name": "桂林路地铁站", "location": "121.412,31.169"}
                ),
                "amap.route_plan": CapabilityResult.success(
                    data={"summary": "步行 600 米到达桂林路地铁站", "steps": [{"instruction": "向前走"}]}
                ),
            }
        )

        result = PrepareNavigationTool().run(
            context,
            {"origin": "当前位置", "destination": "桂林路", "selected_poi_id": "p2"},
        )

        self.assertTrue(result.ok)
        self.assertEqual([name for name, _ in context.mcp_calls], ["amap.poi_search", "amap.geocode", "amap.route_plan"])
        self.assertEqual(result.data["task_type"], "navigation_task")
        self.assertEqual(len(context.created_tasks), 1)
        created = context.created_tasks[0]
        self.assertEqual(created.input_data["destination"], "桂林路地铁站")
        self.assertEqual(created.input_data["route"]["summary"], "步行 600 米到达桂林路地铁站")

    def test_navigation_task_deduplicates_repeated_visual_signal(self) -> None:
        """测试目标：导航任务对重复红绿灯信号不重复播报。

        测试方法：启动任务后连续发送两次 red，再发送 green。
        预期结果：red 只通知一次，green 变化后再次通知。
        """

        group = FakeDeviceGroup()
        context = TaskContext(
            task_id="nav-001",
            input={
                "destination": "桂林路地铁站",
                "route": {"summary": "步行 600 米到达桂林路地铁站"},
            },
            device_group=group,
        )
        task = NavigationTask()
        task.on_start(context)
        group.notifications.clear()

        task.on_event(context, TaskEvent(name="phone.vision.traffic_light.result", payload={"signal": "red"}))
        task.on_event(context, TaskEvent(name="phone.vision.traffic_light.result", payload={"signal": "red"}))
        task.on_event(context, TaskEvent(name="phone.vision.traffic_light.result", payload={"signal": "green"}))

        self.assertEqual(len(group.notifications), 2)
        self.assertEqual(group.notifications[0], {"text": "前方红灯，请停下等待", "priority": "critical"})
        self.assertEqual(group.notifications[1], {"text": "前方绿灯，可继续按导航前进", "priority": "high"})


class VisionCapabilityTests(unittest.TestCase):
    """找物体和红绿灯能力单元测试。"""

    def test_find_object_task_starts_and_completes_phone_video_flow(self) -> None:
        """测试目标：找物体任务通过 SDK 启停手机视频链路和手机任务。

        测试方法：启动任务后发送 found=true 的手机视觉结果。
        预期结果：任务完成，手机任务和视频链路均被释放。
        """

        group = FakeDeviceGroup()
        context = TaskContext(
            task_id="find-001",
            input={"target_object": "水杯", "frame_interval_ms": 300},
            device_group=group,
        )
        task = FindObjectTask()

        task.on_start(context)
        self.assertEqual(context.state, "running")
        self.assertEqual(group.video_link_starts[0]["reason"], "find_object")
        self.assertEqual(group.phone_task_starts[0]["task_type"], "find_object_phone_task")

        result_payload = {"found": True, "summary": "在正前方找到水杯", "direction": "front"}
        task.on_event(context, TaskEvent(name="phone.vision.find_object.result", payload=result_payload))

        self.assertEqual(context.state, "completed")
        self.assertEqual(context.result, result_payload)
        self.assertEqual(group.phone_task_stops[-1]["reason"], "task.completed")
        self.assertEqual(group.video_link_stops[-1]["reason"], "find_object_completed")
        self.assertEqual(group.notifications[-1], {"text": "在正前方找到水杯", "priority": "high"})

    def test_traffic_light_task_red_signal_completes_single_shot_flow(self) -> None:
        """测试目标：红绿灯任务收到红灯后提交 critical 通知并按策略结束。

        测试方法：启动单次识别任务，发送 red 识别结果。
        预期结果：任务完成，手机任务和视频链路均被释放。
        """

        group = FakeDeviceGroup()
        context = TaskContext(
            task_id="traffic-001",
            input={"crossing_name": "路口", "stop_after_first_signal": True},
            device_group=group,
        )
        task = TrafficLightTask()

        task.on_start(context)
        self.assertEqual(group.video_link_starts[0]["reason"], "traffic_light")
        self.assertEqual(group.phone_task_starts[0]["task_type"], "traffic_light_phone_task")

        payload = {"signal": "red", "summary": "红灯，请停下等待"}
        task.on_event(context, TaskEvent(name="phone.vision.traffic_light.result", payload=payload))

        self.assertEqual(context.state, "completed")
        self.assertEqual(context.result, payload)
        self.assertEqual(group.notifications[-1], {"text": "红灯，请停下等待", "priority": "critical"})
        self.assertEqual(group.phone_task_stops[-1]["reason"], "task.completed")
        self.assertEqual(group.video_link_stops[-1]["reason"], "traffic_light_completed")


if __name__ == "__main__":
    unittest.main()
