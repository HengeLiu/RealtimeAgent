from __future__ import annotations

from mcp.base import McpAdapter, McpRequest, McpResult


class AMapAdapter(McpAdapter):
    name = "amap_adapter"
    description = "AMap MCP adapter (stub)."
    request_schema = {"type": "object"}
    response_schema = {"type": "object"}

    def invoke(self, request: McpRequest) -> McpResult:
        if request.action != "route_plan":
            return McpResult(status="failed", error=f"Unsupported action: {request.action}")
        destination = request.params.get("destination", "")
        return McpResult(
            status="completed",
            data={
                "destination": destination,
                "route_summary": "walk 500m then turn right",
                "eta_minutes": 8,
            },
        )
