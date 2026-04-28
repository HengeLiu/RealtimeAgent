"""基于真实 iPhone 和 glass-playback 的设备级验收入口。

测试目标：
1. 使用 SDK 的 glass-playback 运行时模拟真实眼镜设备。
2. 连接已经启动的真实服务端和真实 iPhone，不启动 phone-mock。
3. 用真实触发音频、真实摄像头帧或视频资产验证三端注册、绑定、语音触发和可选视频链路。

测试方法：
1. 读取 host/glass-playback/config 下的回放配置。
2. 在回放前查询服务端健康状态和运行态，确认指定 phone 设备在线。
3. 运行 PlaybackGlassDevice，并检查 event_log / actuator_log 中是否出现预期事件。

预期结果：
1. 基础验收至少出现 device.registered、voice.session.opened、device.binding.ready 和 trigger audio 事件。
2. 找物体或红绿灯验收可额外要求 sensor.camera.stream.started、actuator.audio.play 等事件。
3. 缺少真机、真实数据、绑定或 SDK 视频链路装配时，脚本应以非零退出并输出明确原因。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (
    REPO_ROOT / "openaiglass-sdk" / "server-python",
    REPO_ROOT / "openaiglass-sdk" / "glass-playback",
    REPO_ROOT / "openaiglass-for-blind",
    REPO_ROOT,
):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from openaiglass_glass_playback.config import PlaybackConfig
from openaiglass_glass_playback.glass_device import PlaybackGlassDevice


DEFAULT_EXPECT_EVENTS = [
    "device.registered",
    "voice.session.opened",
    "device.binding.ready",
    "voice.trigger_audio.started",
    "voice.trigger_audio.finished",
]


def main(argv: list[str] | None = None) -> int:
    """执行 glass-playback 真机验收。

    参数：
    1. `argv`：命令行参数，测试时可传入；为空时使用系统命令行。

    返回值：
    1. `0` 表示验收通过。
    2. `1` 表示环境、数据或链路检查失败。

    异常情况：
    1. 所有可预期异常都会转成中文错误信息和非零退出码。
    """

    args = _parse_args(argv)
    try:
        config = PlaybackConfig.load(args.config, repo_root=args.repo_root)
        phone_device_id = _resolve_phone_device_id(args, config)
        _assert_real_data_config(config)
        _assert_server_ready(config)
        runtime_snapshot = _fetch_runtime_snapshot(config)
        _assert_phone_ready(runtime_snapshot, phone_device_id)
        if args.check_only:
            _print_json(
                {
                    "ok": True,
                    "mode": "check_only",
                    "device_id": config.device_id,
                    "phone_device_id": phone_device_id,
                    "message": "真机回放前置检查通过",
                }
            )
            return 0

        _reset_output_logs(config)
        device = PlaybackGlassDevice(
            config,
            timeout_seconds=float(args.timeout_seconds),
            max_runtime_seconds=float(args.max_runtime_seconds),
        )
        result = device.run()
        events = _read_jsonl(config.outputs.event_log if config.outputs else None)
        actuators = _read_jsonl(config.outputs.actuator_log if config.outputs else None)
        missing_events = _missing_names(events, field="type", expected=args.expect_events)
        missing_actuators = _missing_names(actuators, field="name", expected=args.expect_actuators)
        if missing_events or missing_actuators:
            _print_json(
                {
                    "ok": False,
                    "device_id": config.device_id,
                    "phone_device_id": phone_device_id,
                    "missing_events": missing_events,
                    "missing_actuators": missing_actuators,
                    "event_types": sorted({str(item.get("type") or "") for item in events}),
                    "actuator_names": sorted({str(item.get("name") or "") for item in actuators}),
                }
            )
            return 1
        _print_json(
            {
                "ok": bool(result.ok),
                "device_id": config.device_id,
                "phone_device_id": phone_device_id,
                "event_count": result.event_count,
                "actuator_count": result.actuator_count,
                "event_log": str(config.outputs.event_log) if config.outputs else "",
                "actuator_log": str(config.outputs.actuator_log) if config.outputs else "",
            }
        )
        return 0 if result.ok else 1
    except Exception as exc:
        print(f"glass-playback 真机验收失败：{exc}", file=sys.stderr)
        return 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """解析命令行参数。

    参数：
    1. `argv`：命令行参数列表；为空时由 argparse 读取系统参数。

    返回值：
    1. 包含配置路径、设备编号、超时和期望事件的参数对象。

    异常情况：
    1. 参数缺失或格式错误时由 argparse 退出。
    """

    parser = argparse.ArgumentParser(description="真实 iPhone + glass-playback 设备级验收")
    parser.add_argument("--config", required=True, help="glass-playback JSON 配置路径")
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="仓库根目录，默认自动推断")
    parser.add_argument("--phone-device-id", default="", help="必须在线的真实 iPhone 设备编号")
    parser.add_argument("--timeout-seconds", type=float, default=20.0, help="单次网络等待超时")
    parser.add_argument("--max-runtime-seconds", type=float, default=30.0, help="触发音频结束后的控制消息等待时长")
    parser.add_argument(
        "--expect-event",
        dest="expect_events",
        action="append",
        default=None,
        help="要求 event_log 出现的事件名，可重复传入；不传时使用基础验收事件",
    )
    parser.add_argument(
        "--expect-actuator",
        dest="expect_actuators",
        action="append",
        default=None,
        help="要求 actuator_log 出现的执行器事件名，可重复传入",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="只检查配置、真实数据、服务端和真实 phone 在线状态，不启动回放",
    )
    args = parser.parse_args(argv)
    args.expect_events = list(args.expect_events or DEFAULT_EXPECT_EVENTS)
    args.expect_actuators = list(args.expect_actuators or [])
    return args


def _resolve_phone_device_id(args: argparse.Namespace, config: PlaybackConfig) -> str:
    """从命令行或配置中解析真实 phone 设备编号。

    参数：
    1. `args`：命令行参数。
    2. `config`：glass-playback 配置。

    返回值：
    1. 真实 iPhone 设备编号。

    异常情况：
    1. 未配置设备编号或命令行与配置不一致时抛出 `ValueError`。
    """

    phone_device_id = str(args.phone_device_id or config.desired_phone_device_id or "").strip()
    if not phone_device_id:
        raise ValueError("必须通过 --phone-device-id 或 desired_phone_device_id 指定真实 iPhone 设备编号")
    if config.desired_phone_device_id and config.desired_phone_device_id != phone_device_id:
        raise ValueError(
            "配置 desired_phone_device_id 与 --phone-device-id 不一致："
            f"{config.desired_phone_device_id} != {phone_device_id}"
        )
    return phone_device_id


def _assert_real_data_config(config: PlaybackConfig) -> None:
    """校验回放配置已经指向真实数据资产。

    参数：
    1. `config`：glass-playback 配置。

    返回值：
    1. 无。

    异常情况：
    1. 缺少真实音频或摄像头输入配置时抛出异常。
    """

    if not config.trigger_audio.path.exists():
        raise FileNotFoundError(f"触发音频不存在：{config.trigger_audio.path}")
    camera_stream = config.sensors.get("camera_stream")
    if not isinstance(camera_stream, dict):
        raise ValueError("真机场景验收应配置 sensors.camera_stream，使用真实图片帧或 MP4 视频作为眼镜摄像头输入")
    if not camera_stream.get("path") and not camera_stream.get("frames"):
        raise ValueError("sensors.camera_stream 必须配置 path 或 frames")


def _assert_server_ready(config: PlaybackConfig) -> None:
    """检查服务端健康接口。

    参数：
    1. `config`：glass-playback 配置。

    返回值：
    1. 无。

    异常情况：
    1. 服务端不可访问或健康检查失败时抛出异常。
    """

    url = f"{_http_base_url(config.control_ws_url)}/api/health"
    payload = _fetch_json(url)
    ok = bool(payload.get("ok", True)) if isinstance(payload, dict) else False
    if not ok:
        raise RuntimeError(f"服务端健康检查未通过：{payload}")


def _fetch_runtime_snapshot(config: PlaybackConfig) -> dict[str, Any]:
    """读取服务端运行态快照。

    参数：
    1. `config`：glass-playback 配置。

    返回值：
    1. 服务端 `/api/runtime/devices` 返回的 JSON object。

    异常情况：
    1. 接口不可访问或返回非对象时抛出异常。
    """

    url = f"{_http_base_url(config.control_ws_url)}/api/runtime/devices"
    payload = _fetch_json(url)
    if not isinstance(payload, dict):
        raise RuntimeError(f"运行态接口返回不是 JSON object：{payload!r}")
    return payload


def _assert_phone_ready(snapshot: dict[str, Any], phone_device_id: str) -> None:
    """确认指定 phone 在线且不是明显的 phone-mock。

    参数：
    1. `snapshot`：服务端运行态快照。
    2. `phone_device_id`：真实 iPhone 设备编号。

    返回值：
    1. 无。

    异常情况：
    1. 找不到设备、设备离线或运行态带有 phone-mock 特征时抛出异常。
    """

    entries = _collect_device_entries(snapshot, phone_device_id)
    if not entries:
        raise RuntimeError(f"服务端运行态中找不到 phone 设备：{phone_device_id}")
    if not any(_entry_online(entry) for entry in entries):
        raise RuntimeError(f"phone 设备存在但不在线：{phone_device_id}")
    entry_text = json.dumps(entries, ensure_ascii=False).lower()
    for forbidden in ("phone-mock", "phone_mock", "mock_phone", "mock-phone"):
        if forbidden in entry_text:
            raise RuntimeError(f"检测到 phone-mock 特征，本验收要求使用真实 iPhone：{forbidden}")


def _collect_device_entries(node: Any, device_id: str) -> list[dict[str, Any]]:
    """递归收集运行态中匹配设备编号的对象。

    参数：
    1. `node`：任意 JSON 节点。
    2. `device_id`：要查找的设备编号。

    返回值：
    1. 运行态中匹配该设备编号的对象列表。

    异常情况：
    1. 本函数不主动抛出异常。
    """

    result: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if str(node.get("device_id") or node.get("id") or "") == device_id:
            result.append(dict(node))
        for key, value in node.items():
            if str(key) == device_id and isinstance(value, dict):
                result.append({"device_id": device_id, **value})
            result.extend(_collect_device_entries(value, device_id))
    elif isinstance(node, list):
        for item in node:
            result.extend(_collect_device_entries(item, device_id))
    return result


def _entry_online(entry: dict[str, Any]) -> bool:
    """判断运行态设备对象是否在线。

    参数：
    1. `entry`：运行态中的设备对象。

    返回值：
    1. 未明确离线时返回 `True`，明确离线时返回 `False`。

    异常情况：
    1. 本函数不主动抛出异常。
    """

    if entry.get("online") is False:
        return False
    state = str(entry.get("state") or entry.get("status") or "").lower()
    if state in {"offline", "disconnected"}:
        return False
    return True


def _reset_output_logs(config: PlaybackConfig) -> None:
    """清理本次回放输出，避免历史日志影响断言。

    参数：
    1. `config`：glass-playback 配置。

    返回值：
    1. 无。

    异常情况：
    1. 输出文件不可删除时由文件系统异常向外抛出。
    """

    if not config.outputs:
        return
    for path in (config.outputs.event_log, config.outputs.actuator_log):
        if path.exists():
            path.unlink()


def _read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    """读取 JSONL 日志。

    参数：
    1. `path`：JSONL 日志路径；为空或不存在时返回空列表。

    返回值：
    1. 逐行解析后的 JSON object 列表。

    异常情况：
    1. 文件内容不是合法 JSON 行时抛出 `JSONDecodeError`。
    """

    if path is None or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _missing_names(rows: list[dict[str, Any]], *, field: str, expected: list[str]) -> list[str]:
    """计算日志中缺失的事件名。

    参数：
    1. `rows`：事件或执行器日志行。
    2. `field`：用于取名称的字段。
    3. `expected`：必须出现的名称列表。

    返回值：
    1. 未在日志中出现的名称列表。

    异常情况：
    1. 本函数不主动抛出异常。
    """

    present = {str(item.get(field) or "") for item in rows}
    return [name for name in expected if name not in present]


def _fetch_json(url: str) -> Any:
    """通过 HTTP 读取 JSON。

    参数：
    1. `url`：目标 HTTP 地址。

    返回值：
    1. 解析后的 JSON 数据。

    异常情况：
    1. 网络访问失败时抛出 `RuntimeError`。
    2. 返回体不是 JSON 时抛出 `JSONDecodeError`。
    """

    try:
        with urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except URLError as exc:
        raise RuntimeError(f"无法访问 {url}：{exc}") from exc


def _http_base_url(ws_url: str) -> str:
    """把 WebSocket 地址转换为同源 HTTP 地址。

    参数：
    1. `ws_url`：WebSocket 地址。

    返回值：
    1. 同 host 和 port 的 HTTP/HTTPS base URL。

    异常情况：
    1. 本函数不主动校验地址合法性。
    """

    parsed = urlsplit(ws_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    return urlunsplit((scheme, parsed.netloc, "", "", ""))


def _print_json(payload: dict[str, Any]) -> None:
    """输出结构化验收结果。

    参数：
    1. `payload`：验收结果对象。

    返回值：
    1. 无。

    异常情况：
    1. 载荷包含不可 JSON 序列化对象时抛出 `TypeError`。
    """

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
