"""盲人 AI 眼镜业务服务端装配模块。"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT_DIR.parent
for path in (REPO_ROOT / "openaiglass-sdk/server-python", ROOT_DIR, REPO_ROOT):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from capabilities.find_object.phone.processor import YoloFindObjectProcessor
from capabilities.find_object.phone.task import FindObjectPhoneTask
from capabilities.find_object.server.task import FindObjectTask
from capabilities.find_object.server.tool import StartFindObjectTool
from capabilities.navigation.mcp import MockAmapMcpAdapter
from capabilities.navigation.server.task import NavigationTask
from capabilities.navigation.server.tool import PrepareNavigationTool
from capabilities.timer.server.task import TimerTask
from capabilities.timer.server.tool import StartTimerTool
from capabilities.traffic_light.phone.processor import TrafficLightProcessor
from capabilities.traffic_light.phone.task import TrafficLightPhoneTask
from capabilities.traffic_light.server.task import TrafficLightTask
from capabilities.traffic_light.server.tool import StartTrafficLightTool
from host.server.debug_routes import install_business_debug_routes
from openaiglasses import OpenAIGlassesSDK, ServerSettings


def create_sdk(*, include_traffic_light: bool = False) -> OpenAIGlassesSDK:
    """创建并装配盲人场景业务 SDK。

    功能：
    1. 创建 SDK 主入口。
    2. 注册找物体 Tool、Task 和手机处理器。
    3. 按需注册红绿灯识别能力。
    4. 返回可启动的 SDK 对象。

    参数：
    1. `include_traffic_light`：是否装配红绿灯识别能力。默认保持旧 SDK 契约测试兼容。

    返回值：
    1. `OpenAIGlassesSDK`：已注册盲人业务能力的 SDK 对象。

    异常情况：
    1. 注册能力名称为空时由 SDK 抛出异常。
    """

    sdk = OpenAIGlassesSDK()
    sdk.register_tool(StartFindObjectTool())
    sdk.register_task(FindObjectTask())
    sdk.register_phone_processor(YoloFindObjectProcessor())
    sdk.register_phone_task(FindObjectPhoneTask())
    if include_traffic_light:
        register_traffic_light_capability(sdk)
    return sdk


def create_full_sdk() -> OpenAIGlassesSDK:
    """创建完整盲人业务 SDK。

    功能：
    1. 装配当前业务工程全部已启用能力。
    2. 给真实服务端、设备级回放和业务预检使用。

    参数：
    1. 无。

    返回值：
    1. `OpenAIGlassesSDK`：已注册全部业务能力的 SDK 对象。

    异常情况：
    1. 注册能力名称为空时由 SDK 抛出异常。
    """

    sdk = create_sdk(include_traffic_light=True)
    register_timer_capability(sdk)
    register_navigation_capability(sdk)
    return sdk


def register_timer_capability(sdk: OpenAIGlassesSDK) -> None:
    """向 SDK 注册计时器业务能力。"""

    sdk.register_tool(StartTimerTool())
    sdk.register_task(TimerTask())


def register_navigation_capability(sdk: OpenAIGlassesSDK) -> None:
    """向 SDK 注册导航准备业务能力。"""

    sdk.register_mcp_adapter(MockAmapMcpAdapter())
    sdk.register_tool(PrepareNavigationTool())
    sdk.register_task(NavigationTask())


def register_traffic_light_capability(sdk: OpenAIGlassesSDK) -> None:
    """向 SDK 注册红绿灯识别业务能力。"""

    sdk.register_tool(StartTrafficLightTool())
    sdk.register_task(TrafficLightTask())
    sdk.register_phone_processor(TrafficLightProcessor())
    sdk.register_phone_task(TrafficLightPhoneTask())


def create_server_handle(settings: ServerSettings):
    """创建基于盲人场景业务能力的真实服务端句柄。"""

    handle = create_full_sdk().build_server_handle(settings)
    install_business_debug_routes(handle)
    return handle
