"""容器级找物场景运行支持。"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable

from nextgen.integration.container_sim.runtime_probe import wait_for_runtime_probe_snapshots
from nextgen.integration.container_sim.ws_client import WebSocketRpcClient, wait_for_ws_ready
from nextgen.integration.smoke.testdata_loader import StandardTestDataLoader


def run_containerized_find_object_case(
    case_id: str,
    status_dir: Path,
    glass_ws_url: str,
    server_ws_url: str,
    required_runtimes: Iterable[str] = ("glass", "phone", "server"),
    require_fresh_probes: bool = True,
) -> Dict[str, Any]:
    """运行容器级找物标准场景。

    主要逻辑：
    - 等待三端容器探针文件就绪
    - 通过眼镜 WebSocket 长连接注入语音输入
    - 通过服务器 WebSocket 长连接注入单帧分析输入
    - 通过服务器 WebSocket 长连接读取最终状态与日志
    """

    wait_started_at = datetime.now().astimezone().isoformat()
    probes = wait_for_runtime_probe_snapshots(
        status_dir=status_dir,
        runtimes=required_runtimes,
        updated_after=wait_started_at if require_fresh_probes else None,
    )
    loader = StandardTestDataLoader()
    case = loader.build_find_object_case(case_id)
    wait_for_ws_ready(glass_ws_url)
    wait_for_ws_ready(server_ws_url)

    glass_client = WebSocketRpcClient(glass_ws_url)
    server_client = WebSocketRpcClient(server_ws_url)
    try:
        voice_response = glass_client.request(
            "/voice-input",
            {
                "text": case["voice_text"],
                "audio_ref": case["audio_ref"],
                "vad_confidence": case["vad_confidence"],
            },
        )
        session_id = voice_response["server_response"]["session_id"]
        frame_response = server_client.request(
            "/frame-analysis",
            {
                "session_id": session_id,
                "target_name": case["target_name"],
                "candidates": [item.to_dict() for item in case["candidates"]],
                "hand_observation": case["hand_observation"].to_dict() if case["hand_observation"] else None,
                "mark_completed": case["expected_final_status"] == "completed",
            },
        )
        server_report = server_client.request(f"/sessions/{session_id}", {})
    finally:
        glass_client.close()
        server_client.close()
    report = {
        "case_id": case["case_id"],
        "voice_case_id": case["voice_case_id"],
        "expected_hint_contains": case["expected_hint_contains"],
        "expected_final_status": case["expected_final_status"],
        "session_id": session_id,
        "voice_response": voice_response,
        "frame_response": frame_response,
        "server_report": server_report,
        "container_probes": {runtime: snapshot.to_dict() for runtime, snapshot in probes.items()},
    }
    return report


def write_containerized_find_object_report(output_path: Path, report: Dict[str, Any]) -> None:
    """将容器级找物运行结果写入文件。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
