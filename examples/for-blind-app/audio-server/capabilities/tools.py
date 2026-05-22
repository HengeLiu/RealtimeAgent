from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request

from pydantic import BaseModel, Field

from realtime_agent import AssetRef, BaseTool, ToolContext, ToolError, ToolResult, ToolSpec, VisualAssetRef
from realtime_agent.errors import ErrorCode


CAPTURE_PHOTO_DEFAULT_TIMEOUT_SECONDS = 15
VISION_MODEL_DEFAULT = "qwen3.6-plus"
VISION_BASE_URL_DEFAULT = "https://dashscope.aliyuncs.com/compatible-mode/v1"
VISION_IMAGE_BASE64_MAX_BYTES = 7_500_000
BOCHA_SEARCH_API_URL_DEFAULT = "https://api.bochaai.com/v1/web-search"


class CapturePhotoInput(BaseModel):
    """抓拍 Tool 输入参数。"""

    timeout_seconds: float = Field(
        default=CAPTURE_PHOTO_DEFAULT_TIMEOUT_SECONDS,
        gt=0,
        description="等待图片返回的超时时间，单位秒。",
    )


class CapturePhotoOutput(BaseModel):
    """抓拍 Tool 输出结构。"""

    captured: bool = Field(description="是否收到图片资产。")
    asset_id: str | None = Field(default=None, description="图片资产 ID。")
    stream_type: str | None = Field(default=None, description="资产来源类型。")
    uri: str | None = Field(default=None, description="资产 URI。")
    mime_type: str | None = Field(default=None, description="资产 MIME 类型。")


class CapturePhotoTool(BaseTool):
    """当前画面抓拍 Tool。

    主要功能：通过 `context.devices.sensors.rgb.one()` 获取一张 RGB 图片资产。
    该工具属于 for-blind-app 业务能力，不是 SDK 内置 Tool。
    """

    spec = ToolSpec(
        name="capture_photo",
        description="当用户需要了解当前画面、障碍物、文字或路况时，采集一张当前 RGB 图片。",
        input_model=CapturePhotoInput,
        output_model=CapturePhotoOutput,
        progress_message=("我先拍张照片看看。", "稍等，我看一下当前画面。"),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行当前画面抓拍。

        主要逻辑：只使用 Context 设备 API 请求 `sensor.rgb` 单帧资产；图片字节
        由端侧通过 stream 上传，Tool 只返回资产引用。
        参数：`context` 为 SDK 注入上下文，`input_data` 包含 timeout_seconds。
        返回值：成功时返回 `AssetRef`。
        异常情况：设备不可用或超时时由底层 Context API 抛出。
        """

        asset = await context.devices.sensors.rgb.one(
            params={"format": "jpeg"},
            timeout_seconds=float(input_data.get("timeout_seconds") or CAPTURE_PHOTO_DEFAULT_TIMEOUT_SECONDS),
        )
        return ToolResult.success(
            data={
                "captured": True,
                "asset_id": asset.asset_id,
                "stream_type": asset.stream_type,
                "uri": asset.uri,
                "mime_type": asset.mime_type,
            },
            assets=[asset],
            visual_assets=[
                VisualAssetRef(
                    asset=asset,
                    visibility="append_to_agent",
                    consumer="agent_inline",
                    text_context="这是刚拍摄的当前画面，请结合用户问题回答。",
                )
            ],
            message="已获取当前画面。",
        )


class InterpretImageInput(BaseModel):
    """图片解读 Tool 输入参数。"""

    query: str = Field(default="请描述当前画面。", description="用户想基于图片了解的问题。")
    image_asset_id: str = Field(description="已经上传到本次会话的图片资产 ID。")
    timeout_seconds: float = Field(default=20, gt=0, description="等待视觉模型返回的超时时间，单位秒。")


class InterpretImageOutput(BaseModel):
    """图片解读 Tool 输出结构。"""

    interpreted: bool = Field(description="是否完成图片解读。")
    interpretation: str = Field(description="适合直接语音播报的中文解读结果。")
    model: str = Field(description="实际使用的视觉模型。")
    asset_id: str = Field(description="被解读的图片资产 ID。")


class InterpretImageTool(BaseTool):
    """图片解读 Tool。

    主要功能：读取已经抓拍得到的图片资产，调用支持图片输入的 Chat Completions
    兼容模型生成中文解读结果。
    """

    spec = ToolSpec(
        name="interpret_image",
        description="当已经有图片资产 ID，并需要理解图片内容、障碍物、文字或路况时调用。",
        input_model=InterpretImageInput,
        output_model=InterpretImageOutput,
        progress_message=("我看一下这张照片。", "稍等，我分析一下图片。"),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行图片解读。

        主要逻辑：先通过 `context.assets.get()` 读取资产引用，再把图片编码为
        OpenAI-compatible 的 `image_url` data URL，最后调用视觉模型生成短中文结果。
        参数：`context` 为 SDK 注入上下文，`input_data` 包含 query、image_asset_id。
        返回值：包含 interpretation 的结构化 ToolResult。
        异常情况：资产不存在、图片不可读、模型不可用时返回失败 ToolResult。
        """

        asset_id = str(input_data.get("image_asset_id") or "").strip()
        if not asset_id:
            return ToolResult.failed(ToolError("image_asset_id 不能为空", code=ErrorCode.INVALID_ARGUMENT))
        asset = context.assets.get(asset_id)
        if asset is None:
            return ToolResult.failed(
                ToolError(f"找不到图片资产：{asset_id}", code=ErrorCode.NOT_FOUND, details={"asset_id": asset_id})
            )
        return _interpret_asset_with_vision_model(
            asset=asset,
            query=str(input_data.get("query") or "请描述当前画面。"),
            timeout_seconds=float(input_data.get("timeout_seconds") or 20),
        )


class InterpretCurrentViewInput(BaseModel):
    """拍照并解读 Tool 输入参数。"""

    query: str = Field(default="请描述当前画面。", description="用户想基于当前画面了解的问题。")
    timeout_seconds: float = Field(
        default=CAPTURE_PHOTO_DEFAULT_TIMEOUT_SECONDS,
        gt=0,
        description="等待端侧拍照上传的超时时间，单位秒。",
    )
    vision_timeout_seconds: float = Field(default=20, gt=0, description="等待视觉模型返回的超时时间，单位秒。")


class InterpretCurrentViewOutput(BaseModel):
    """拍照并解读 Tool 输出结构。"""

    captured: bool = Field(description="是否收到当前画面图片。")
    interpreted: bool = Field(description="是否完成图片解读。")
    interpretation: str = Field(description="适合直接语音播报的中文解读结果。")
    asset_id: str | None = Field(default=None, description="图片资产 ID。")
    stream_type: str | None = Field(default=None, description="资产来源类型。")
    uri: str | None = Field(default=None, description="资产 URI。")
    mime_type: str | None = Field(default=None, description="资产 MIME 类型。")
    model: str | None = Field(default=None, description="实际使用的视觉模型。")


class InterpretCurrentViewTool(BaseTool):
    """拍照并解读当前画面 Tool。

    主要功能：把“抓拍当前 RGB 图片”和“调用视觉模型解读图片”封装成一个业务
    Tool，避免 AgentCore 写死 `capture_photo -> 图片消息 -> 主模型` 的编排。
    """

    spec = ToolSpec(
        name="interpret_current_view",
        description=(
            "Vision 链路中，当用户询问前方、眼前、当前画面、障碍物、文字或路况时调用；"
            "本工具会先拍照，再用视觉模型解读，并直接返回可播报的中文结果。"
        ),
        input_model=InterpretCurrentViewInput,
        output_model=InterpretCurrentViewOutput,
        progress_message=("我先拍张照片看看。", "稍等，我看一下当前画面。"),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行当前画面抓拍和解读。

        主要逻辑：先通过 `sensor.rgb.one()` 请求端侧上传一帧图片，再复用图片解读
        helper 调视觉模型。这样 VisionRealtimeAgentCore 只需要执行 Tool，不需要知道视觉链路内部编排。
        参数：`context` 为 SDK 注入上下文，`input_data` 包含 query 和超时设置。
        返回值：成功时包含图片资产和 interpretation。
        异常情况：拍照失败由底层 Context API 转成 ToolResult；解读失败返回失败结果。
        """

        query = str(input_data.get("query") or "请描述当前画面。")
        asset = await context.devices.sensors.rgb.one(
            params={"format": "jpeg"},
            timeout_seconds=float(input_data.get("timeout_seconds") or CAPTURE_PHOTO_DEFAULT_TIMEOUT_SECONDS),
        )
        result = _interpret_asset_with_vision_model(
            asset=asset,
            query=query,
            timeout_seconds=float(input_data.get("vision_timeout_seconds") or 20),
        )
        if not result.ok:
            return result
        data = dict(result.data or {})
        data.update(
            {
                "captured": True,
                "asset_id": asset.asset_id,
                "stream_type": asset.stream_type,
                "uri": asset.uri,
                "mime_type": asset.mime_type,
            }
        )
        return ToolResult.success(
            data=data,
            assets=[asset],
            visual_assets=[
                VisualAssetRef(
                    asset=asset,
                    visibility="internal_only",
                    consumer="tool_internal",
                    text_context="图片已经由 interpret_current_view 内部视觉模型分析，主模型只读取文本结果。",
                )
            ],
            message=str(data.get("interpretation") or result.message),
        )


def _interpret_asset_with_vision_model(*, asset: AssetRef, query: str, timeout_seconds: float) -> ToolResult:
    """调用视觉模型解读图片资产。

    主要逻辑：本地测试可通过 `REALTIME_AGENT_VISION_MODEL_PROVIDER=mock` 使用确定性结果；
    真实运行时使用 DashScope OpenAI-compatible Chat Completions，模型默认 qwen3.6-plus。
    参数：`asset` 为图片资产引用；`query` 为用户问题；`timeout_seconds` 为模型超时。
    返回值：包含 `interpretation` 的 ToolResult。
    异常情况：图片路径不可读、缺少 API Key 或 provider 报错时返回失败 ToolResult。
    """

    model = os.getenv("REALTIME_AGENT_VISION_MODEL") or os.getenv("DASHSCOPE_VISION_MODEL") or VISION_MODEL_DEFAULT
    if os.getenv("REALTIME_AGENT_VISION_MODEL_PROVIDER") == "mock":
        text = "我已经根据刚拍到的照片完成识别。"
        return ToolResult.success(
            data={"interpreted": True, "interpretation": text, "model": "mock-vision", "asset_id": asset.asset_id},
            message=text,
            assets=[asset],
            visual_assets=[
                VisualAssetRef(
                    asset=asset,
                    visibility="internal_only",
                    consumer="tool_internal",
                    text_context="图片已经由 interpret_image 内部视觉模型分析，主模型只读取文本结果。",
                )
            ],
        )
    image_path = _asset_local_path(asset)
    if image_path is None:
        return ToolResult.failed(
            ToolError(
                "图片资产没有可读取的本地路径",
                code=ErrorCode.NOT_FOUND,
                details={"asset_id": asset.asset_id, "uri": asset.uri},
            )
        )
    try:
        image_bytes = image_path.read_bytes()
    except OSError as exc:
        return ToolResult.failed(
            ToolError(
                f"读取图片资产失败：{exc}",
                code=ErrorCode.UNKNOWN,
                details={"asset_id": asset.asset_id, "path": str(image_path)},
            )
        )
    if len(image_bytes) > VISION_IMAGE_BASE64_MAX_BYTES:
        return ToolResult.failed(
            ToolError(
                "图片过大，无法通过 base64 方式提交给视觉模型",
                code=ErrorCode.INVALID_ARGUMENT,
                details={"asset_id": asset.asset_id, "size_bytes": len(image_bytes)},
            )
        )
    api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return ToolResult.failed(
            ToolError("DASHSCOPE_API_KEY 未配置，无法调用图片解读模型", code=ErrorCode.PROVIDER_UNAVAILABLE)
        )
    try:
        from openai import OpenAI
    except ImportError as exc:
        return ToolResult.failed(ToolError(f"openai 包未安装：{exc}", code=ErrorCode.PROVIDER_UNAVAILABLE))
    base_url = os.getenv("OPENAI_BASE_URL") or VISION_BASE_URL_DEFAULT
    mime_type = asset.mime_type or "image/jpeg"
    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds, max_retries=1)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是盲人眼镜的视觉解读助手。只基于图片回答，中文，简短口语化。"
                        "优先说明主要物体、障碍、危险、文字和行动建议；无法判断距离时不要编造具体距离。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"用户问题：{query}。请直接给出适合语音播报的回答。"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            stream=False,
        )
    except Exception as exc:
        return ToolResult.failed(
            ToolError(
                f"图片解读模型调用失败：{exc}",
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                retryable=True,
                details={"asset_id": asset.asset_id, "model": model},
            )
        )
    choices = getattr(response, "choices", None) or []
    message = getattr(choices[0], "message", None) if choices else None
    text = str(getattr(message, "content", "") or "").strip()
    if not text:
        return ToolResult.failed(
            ToolError(
                "图片解读模型没有返回文本",
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"asset_id": asset.asset_id},
            )
        )
    return ToolResult.success(
        data={"interpreted": True, "interpretation": text, "model": model, "asset_id": asset.asset_id},
        message=text,
        assets=[asset],
        visual_assets=[
            VisualAssetRef(
                asset=asset,
                visibility="internal_only",
                consumer="tool_internal",
                text_context="图片已经由 interpret_image 内部视觉模型分析，主模型只读取文本结果。",
            )
        ],
    )


def _asset_local_path(asset: AssetRef) -> Path | None:
    """解析图片资产的本地文件路径。"""

    if not asset.uri:
        return None
    path = Path(asset.uri).expanduser()
    if path.is_file():
        return path.resolve()
    if not path.is_absolute():
        candidate = Path.cwd() / path
        if candidate.is_file():
            return candidate.resolve()
    return None


def _looks_like_lnglat(value: str) -> bool:
    """判断字符串是否已经是高德需要的 `经度,纬度` 坐标。

    主要逻辑：只接受两个可解析数字，避免把中文地址误传给路线规划。
    参数：`value` 为用户或上游工具给出的地点字符串。
    返回值：形如 `121.4,31.1` 时返回 True。
    异常情况：解析失败时返回 False。
    """

    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        return False
    try:
        longitude = float(parts[0])
        latitude = float(parts[1])
    except ValueError:
        return False
    return -180 <= longitude <= 180 and -90 <= latitude <= 90


def _mcp_text_json(call_result: dict) -> dict:
    """从 MCP `content[text]` 响应里解析 JSON。

    主要逻辑：高德 MCP 返回的 JSON 放在 `result.content[].text` 字符串中，
    本函数取第一个可解析的 JSON 文本。
    参数：`call_result` 为 `McpGateway.call()` 的包装结果。
    返回值：解析后的 JSON 对象。
    异常情况：远端标记 `isError`、无文本或 JSON 非对象时抛出 `ValueError`。
    """

    result = dict(call_result.get("result") or {})
    if result.get("isError"):
        text = _mcp_first_text(result)
        raise ValueError(text or "mcp tool returned error")
    for item in result.get("content") or []:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("mcp text json is not an object")
        return parsed
    raise ValueError("mcp response has no json text content")


def _mcp_first_text(result: dict) -> str:
    """读取 MCP result 中第一段文本，主要用于错误说明。"""

    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            return str(item.get("text") or "").strip()
    return ""


def _extract_geo_location(call_result: dict, label: str) -> tuple[str, dict]:
    """从 `maps_geo` 结果中提取第一个坐标。

    主要逻辑：解析 MCP 文本 JSON，读取 `return[0].location`。
    参数：`call_result` 为 `amap.geo` 调用结果，`label` 用于错误说明。
    返回值：坐标字符串和对应地理编码条目。
    异常情况：无可用坐标时抛出 `ValueError`。
    """

    parsed = _mcp_text_json(call_result)
    candidates = parsed.get("return") or []
    if not isinstance(candidates, list):
        raise ValueError(f"{label} 地理编码结果格式不正确")
    for item in candidates:
        if not isinstance(item, dict):
            continue
        location = str(item.get("location") or "").strip()
        if _looks_like_lnglat(location):
            return location, item
    raise ValueError(f"{label} 未找到可用坐标")


def _route_point_for_mcp(mcp, *, value: str, label: str, timeout_seconds: float) -> tuple[str, dict | None]:
    """把地址或坐标转换为路线规划可用坐标。

    主要逻辑：如果输入已经是坐标则直接返回；否则调用 `amap.geo`
    转换中文地址。
    参数：`mcp` 为 MCP 网关，`value` 为地点，`label` 用于错误说明，
    `timeout_seconds` 为单次 MCP 调用超时。
    返回值：坐标字符串和可选地理编码条目。
    异常情况：MCP 不可用或地址无法编码时向上抛出异常。
    """

    if _looks_like_lnglat(value):
        return value, None
    geo_result = mcp.call(tool_name="amap.geo", arguments={"address": value}, timeout_seconds=timeout_seconds)
    return _extract_geo_location(geo_result, label)


class QueryRoutePlanInput(BaseModel):
    """路线规划查询 Tool 输入参数。"""

    destination: str = Field(default="盲人服务中心", description="用户想去的目的地名称或地址。")
    origin: str = Field(default="当前位置", description="导航起点；通常使用当前位置。")
    timeout_seconds: float = Field(default=5, gt=0, description="等待路线结果的超时时间，单位秒。")


class QueryRoutePlanOutput(BaseModel):
    """路线规划查询 Tool 输出结构。"""

    route_ready: bool = Field(description="是否准备好可用路线。")
    provider: str | None = Field(default=None, description="路线来源。")
    destination: str | None = Field(default=None, description="导航目的地。")
    route: dict | None = Field(default=None, description="路线结构化结果。")
    error: str | None = Field(default=None, description="fallback 错误说明。")


class QueryRoutePlanTool(BaseTool):
    """路线规划查询 Tool。

    主要功能：调用配置好的 Amap MCP 路线规划工具；没有 MCP 时返回明确 fallback，不启动后台导航任务。
    """

    spec = ToolSpec(
        name="query_route_plan",
        description="当用户想去某个地点、询问怎么走或需要路线时调用。目的地不明确时，先向用户确认。",
        input_model=QueryRoutePlanInput,
        output_model=QueryRoutePlanOutput,
        progress_message=("我先规划一下路线。", "稍等，我查一下怎么走。"),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行路线规划查询。

        主要逻辑：调用 `amap.route_plan` MCP 工具；不可用时返回明确 fallback。
        参数：`input_data` 可包含 `destination`、`origin`。
        返回值：路线规划摘要。
        异常情况：MCP 未启用或失败时返回 fallback，不伪装真实地图成功。
        """

        destination = str(input_data["destination"]).strip()
        origin = str(input_data["origin"]).strip()
        mcp = getattr(context, "mcp", None)
        if mcp is None:
            return ToolResult.success(
                data={"provider": "fallback", "destination": destination, "route_ready": False},
                message="路线服务未配置，无法规划真实路线",
            )
        try:
            origin_location, origin_geo = _route_point_for_mcp(
                mcp,
                value=origin,
                label="起点",
                timeout_seconds=float(input_data["timeout_seconds"]),
            )
            destination_location, destination_geo = _route_point_for_mcp(
                mcp,
                value=destination,
                label="终点",
                timeout_seconds=float(input_data["timeout_seconds"]),
            )
            route = mcp.call(
                tool_name="amap.route_plan",
                arguments={"origin": origin_location, "destination": destination_location},
                timeout_seconds=float(input_data["timeout_seconds"]),
            )
        except Exception as exc:
            return ToolResult.success(
                data={"provider": "fallback", "destination": destination, "route_ready": False, "error": str(exc)},
                message="路线规划进入 fallback",
            )
        return ToolResult.success(
            data={
                "route_ready": True,
                "provider": "amap_mcp",
                "destination": destination,
                "origin": origin,
                "origin_location": origin_location,
                "destination_location": destination_location,
                "origin_geo": origin_geo,
                "destination_geo": destination_geo,
                "route": route,
            },
            message="路线已准备",
        )


class SearchWebInput(BaseModel):
    """搜索 Tool 输入参数。"""

    query: str = Field(default="盲人导航安全提示", description="要搜索的问题或关键词。")
    limit: int = Field(default=3, ge=1, le=10, description="最多返回的搜索结果数量。")
    freshness: str = Field(default="noLimit", description="搜索时间范围，例如 noLimit、oneDay、oneWeek、oneMonth、oneYear。")
    summary: bool = Field(default=True, description="是否让 Bocha 返回摘要内容。")
    timeout_seconds: float = Field(default=5, gt=0, description="等待搜索结果的超时时间，单位秒。")


class SearchWebOutput(BaseModel):
    """搜索 Tool 输出结构。"""

    provider: str | None = Field(default=None, description="搜索结果来源。")
    fallback: bool | None = Field(default=None, description="是否使用 fallback。")
    query: str = Field(description="实际搜索词。")
    items: list[dict] | None = Field(default=None, description="搜索结果列表。")
    search: dict | None = Field(default=None, description="搜索返回的原始结构化结果。")
    error: str | None = Field(default=None, description="fallback 错误说明。")


class SearchWebTool(BaseTool):
    """联网搜索 Tool。"""

    spec = ToolSpec(
        name="search_web",
        description="当用户明确要求搜索、查询资料、查最新公开信息，或问题需要外部资料时调用。",
        input_model=SearchWebInput,
        output_model=SearchWebOutput,
        progress_message=("我查一下资料。", "稍等，我搜索一下。"),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行搜索。

        主要逻辑：调用 Bocha Web Search API；没有 API Key 时返回明确 fallback 来源。
        参数：`input_data` 包含 `query`。
        返回值：搜索摘要和引用列表。
        异常情况：Bocha 未配置或调用失败时返回 fallback 结果，不把大正文塞进控制事件。
        """

        query = str(input_data["query"]).strip()
        api_key = os.getenv("BOCHA_SEARCH_API_KEY") or os.getenv("BOCHA_API_KEY")
        if not api_key:
            return ToolResult.success(
                data={"provider": "fallback", "fallback": True, "query": query, "items": []},
                message="搜索服务未配置 BOCHA_SEARCH_API_KEY，暂时没有搜索结果",
            )
        try:
            search = await asyncio.to_thread(
                _call_bocha_web_search,
                api_key=api_key,
                query=query,
                count=int(input_data["limit"]),
                freshness=str(input_data.get("freshness") or "noLimit"),
                summary=bool(input_data.get("summary", True)),
                timeout_seconds=float(input_data["timeout_seconds"]),
            )
        except Exception as exc:
            return ToolResult.success(
                data={"provider": "fallback", "fallback": True, "query": query, "error": str(exc), "items": []},
                message="搜索服务暂时不可用",
            )
        return ToolResult.success(
            data={"provider": "bocha", "fallback": False, "query": query, "items": search["items"], "search": search},
            message="搜索完成",
        )


def _call_bocha_web_search(
    *,
    api_key: str,
    query: str,
    count: int,
    freshness: str,
    summary: bool,
    timeout_seconds: float,
) -> dict:
    """调用 Bocha Web Search API 并归一化结果。

    主要逻辑：向 `BOCHA_SEARCH_API_URL` 指定的接口发送 JSON 请求，默认使用
    `https://api.bochaai.com/v1/web-search`；响应中只保留标题、链接、摘要、站点和发布时间。
    参数：`api_key` 为 Bocha API Key，`query` 为搜索词，`count` 为返回条数。
    返回值：包含 provider、raw 和 items 的结构化字典。
    异常情况：HTTP 非 2xx、JSON 无法解析或响应结构异常时抛出 RuntimeError。
    """

    api_url = os.getenv("BOCHA_SEARCH_API_URL") or BOCHA_SEARCH_API_URL_DEFAULT
    payload = {
        "query": query,
        "freshness": freshness,
        "summary": summary,
        "count": count,
    }
    request = urllib_request.Request(
        api_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Bocha 搜索 HTTP {exc.code}: {detail}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"Bocha 搜索网络失败：{exc.reason}") from exc
    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Bocha 搜索返回非 JSON：{exc}") from exc
    pages = (((raw.get("data") or {}).get("webPages") or {}).get("value") or [])
    if not isinstance(pages, list):
        raise RuntimeError("Bocha 搜索返回结构缺少 data.webPages.value[]")
    items = [_normalize_bocha_page(page) for page in pages[:count] if isinstance(page, dict)]
    return {"provider": "bocha", "api_url": api_url, "raw": raw, "items": items}


def _normalize_bocha_page(page: dict) -> dict:
    """把 Bocha 单条网页结果归一成模型和端侧更容易消费的结构。"""

    return {
        "title": str(page.get("name") or page.get("title") or "").strip(),
        "url": str(page.get("url") or "").strip(),
        "snippet": str(page.get("snippet") or "").strip(),
        "summary": str(page.get("summary") or page.get("content") or "").strip(),
        "site_name": str(page.get("siteName") or "").strip(),
        "date_published": str(page.get("datePublished") or "").strip(),
    }
