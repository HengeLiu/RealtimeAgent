"""执行 SDK 真机联调前配置检查。"""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import perf_counter
from urllib.error import URLError
from urllib.request import urlopen


APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
DEFAULT_SERVER_CONFIG = APP_ROOT / "config/local_server.env"
DEFAULT_PREFLIGHT_REPORT = REPO_ROOT / "logs/sdk-preflight-current.json"
PHONE_APP_CONFIG = APP_ROOT / "host/phone/config/AppConfig.plist"
PHONE_APP_CONFIG_TEMPLATE = APP_ROOT / "host/phone/config/AppConfig.plist.example"
PHONE_RUNTIME_CONFIG = REPO_ROOT / "openaiglass-sdk/phone-ios/GlassesVideoReceiver/AppConfig.plist"
PHONE_PROJECT = REPO_ROOT / "openaiglass-sdk/phone-ios/GlassesVideoReceiver.xcodeproj"
GLASS_PROJECT = REPO_ROOT / "openaiglass-sdk/glass-esp32"
GLASS_KCONFIG = REPO_ROOT / "openaiglass-sdk/glass-esp32/main/Kconfig.projbuild"
GLASS_LOCAL_CONFIG = APP_ROOT / "host/glass/config/local_build.env"


@dataclass(slots=True)
class LiveCheckResult:
    """单项联调检查结果。

    主要功能：
    1. 表示某个真机联调前置检查是否通过。
    2. 保留检查细节，便于写入 JSON 报告。

    主要属性：
    1. `name`：检查名称。
    2. `ok`：是否通过。
    3. `level`：失败级别，`error` 会影响总结果，`warning` 只提示。
    4. `duration_ms`：检查耗时。
    5. `details`：结构化检查细节。
    """

    name: str
    ok: bool
    level: str = "error"
    duration_ms: int = 0
    details: dict[str, object] = field(default_factory=dict)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    返回值：
    1. `argparse.Namespace`：解析后的参数对象。
    """

    parser = argparse.ArgumentParser(description="执行 SDK 真机联调前配置检查")
    parser.add_argument(
        "--server-config",
        type=str,
        default=str(DEFAULT_SERVER_CONFIG),
        help="业务服务端配置文件路径，默认 openaiglass-for-blind/config/local_server.env",
    )
    parser.add_argument(
        "--preflight-report",
        type=str,
        default=str(DEFAULT_PREFLIGHT_REPORT),
        help="SDK 预检报告路径，默认 logs/sdk-preflight-current.json",
    )
    parser.add_argument(
        "--report",
        type=str,
        default="",
        help="可选 JSON 报告输出路径",
    )
    parser.add_argument(
        "--require-server",
        action="store_true",
        help="要求服务端已经启动并通过 /api/health 检查",
    )
    return parser.parse_args()


def _duration_ms(start: float) -> int:
    """计算耗时毫秒数。"""

    return int((perf_counter() - start) * 1000)


def _resolve_path(path_text: str) -> Path:
    """解析相对或绝对路径。

    参数：
    1. `path_text`：命令行传入路径。

    返回值：
    1. `Path`：绝对路径。
    """

    path = Path(path_text)
    if not path.is_absolute():
        repo_candidate = REPO_ROOT / path
        path = repo_candidate if repo_candidate.exists() else APP_ROOT / path
    return path.resolve()


def _read_env_file(path: Path) -> dict[str, str]:
    """读取简单 shell env 文件。

    主要逻辑：
    1. 支持 `KEY=value` 与 `KEY="value"`。
    2. 忽略注释和空行。
    3. 不执行 shell 代码，避免读取配置时产生副作用。

    参数：
    1. `path`：配置文件路径。

    返回值：
    1. 配置键值字典。
    """

    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[key] = value
    return values


def _list_local_ipv4_addresses() -> list[str]:
    """列出当前机器可用的非回环 IPv4 地址。

    返回值：
    1. IPv4 地址列表。
    """

    completed = subprocess.run(
        ["ifconfig"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    addresses: list[str] = []
    for match in re.finditer(r"\binet\s+(\d+\.\d+\.\d+\.\d+)\b", completed.stdout):
        address = match.group(1)
        if address != "127.0.0.1":
            addresses.append(address)
    return sorted(set(addresses))


def _parse_token_map(token_map: str) -> dict[str, str]:
    """解析设备配对令牌映射。

    参数：
    1. `token_map`：形如 `glass-001=a,phone-001=b` 的字符串。

    返回值：
    1. 设备编号到令牌的映射。
    """

    result: dict[str, str] = {}
    for item in token_map.split(","):
        text = item.strip()
        if not text or "=" not in text:
            continue
        device_id, token = text.split("=", 1)
        device_id = device_id.strip()
        token = token.strip()
        if device_id:
            result[device_id] = token
    return result


def _read_phone_config() -> dict[str, object]:
    """读取业务侧 iOS 手机端 AppConfig.plist。"""

    source = PHONE_APP_CONFIG
    if not source.exists() and PHONE_APP_CONFIG_TEMPLATE.exists():
        source = PHONE_APP_CONFIG_TEMPLATE
    if not source.exists():
        source = PHONE_RUNTIME_CONFIG
    with source.open("rb") as file:
        payload = plistlib.load(file)
    if not isinstance(payload, dict):
        raise RuntimeError("AppConfig.plist 根节点不是字典")
    return {str(key): value for key, value in payload.items()}


def _read_glass_kconfig_defaults() -> dict[str, str]:
    """读取眼镜端 Kconfig 默认值。

    返回值：
    1. 配置名到默认值的映射。
    """

    text = GLASS_KCONFIG.read_text(encoding="utf-8")
    defaults: dict[str, str] = {}
    current_key = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("config "):
            current_key = line.split(maxsplit=1)[1].strip()
            continue
        if current_key and line.startswith("default "):
            value = line.removeprefix("default ").strip()
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            defaults[current_key] = value
    return defaults


def check_paths() -> LiveCheckResult:
    """检查三端关键入口文件是否存在。"""

    start = perf_counter()
    paths = {
        "server_script": APP_ROOT / "scripts/run_server.sh",
        "phone_script": APP_ROOT / "scripts/run_phone.sh",
        "sdk_preflight_script": APP_ROOT / "scripts/run_sdk_preflight.py",
        "sdk_live_check_script": APP_ROOT / "scripts/run_sdk_live_check.py",
        "phone_project": PHONE_PROJECT,
        "phone_business_config": PHONE_APP_CONFIG,
        "phone_business_config_template": PHONE_APP_CONFIG_TEMPLATE,
        "phone_runtime_config": PHONE_RUNTIME_CONFIG,
        "glass_project": GLASS_PROJECT,
        "glass_kconfig": GLASS_KCONFIG,
        "glass_local_config": GLASS_LOCAL_CONFIG,
    }
    details = {
        key: {
            "path": str(path),
            "exists": path.exists(),
        }
        for key, path in paths.items()
    }
    ok = all(bool(item["exists"]) for item in details.values())
    return LiveCheckResult(
        name="entry_paths",
        ok=ok,
        duration_ms=_duration_ms(start),
        details=details,
    )


def check_preflight_report(report_path: Path) -> LiveCheckResult:
    """检查 SDK 离线预检报告。

    参数：
    1. `report_path`：预检报告路径。

    返回值：
    1. 单项检查结果。
    """

    start = perf_counter()
    if not report_path.exists():
        return LiveCheckResult(
            name="preflight_report",
            ok=False,
            duration_ms=_duration_ms(start),
            details={
                "path": str(report_path),
                "message": "未找到预检报告，请先执行 openaiglass-for-blind/scripts/run_sdk_preflight.py",
            },
        )
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return LiveCheckResult(
            name="preflight_report",
            ok=False,
            duration_ms=_duration_ms(start),
            details={"path": str(report_path), "error": str(exc)},
        )
    return LiveCheckResult(
        name="preflight_report",
        ok=bool(payload.get("ok")),
        duration_ms=_duration_ms(start),
        details={
            "path": str(report_path),
            "ok": bool(payload.get("ok")),
            "check_count": payload.get("check_count"),
            "passed_count": payload.get("passed_count"),
            "failed_count": payload.get("failed_count"),
        },
    )


def check_config_alignment(server_config_path: Path) -> LiveCheckResult:
    """检查服务端、手机端和眼镜端的联调配置是否一致。

    参数：
    1. `server_config_path`：本地服务端 env 配置路径。

    返回值：
    1. 单项检查结果。
    """

    start = perf_counter()
    server_config = _read_env_file(server_config_path)
    phone_config = _read_phone_config()
    glass_local_config = _read_env_file(GLASS_LOCAL_CONFIG)
    glass_defaults = _read_glass_kconfig_defaults()

    port = str(server_config.get("PORT") or "8765")
    public_host = str(server_config.get("SERVER_PUBLIC_HOST") or "").strip()
    token_map = _parse_token_map(str(server_config.get("DEVICE_TOKEN_MAP") or ""))
    phone_id = str(phone_config.get("phoneDeviceID") or "").strip()
    phone_token = str(phone_config.get("pairToken") or "").strip()
    desired_glass_id = str(phone_config.get("desiredGlassDeviceID") or "").strip()
    phone_server_url = str(phone_config.get("serverBaseURLString") or "").strip()
    glass_id = str(glass_local_config.get("GLASS_DEVICE_ID") or glass_defaults.get("GLASS_DEVICE_ID") or "").strip()
    glass_token = str(glass_local_config.get("GLASS_PAIR_TOKEN") or glass_defaults.get("GLASS_PAIR_TOKEN") or "").strip()
    glass_server_ws_uri = str(
        glass_local_config.get("GLASS_SERVER_WS_URI") or glass_defaults.get("GLASS_SERVER_WS_URI") or ""
    ).strip()

    expected_phone_url = f"http://{public_host}:{port}" if public_host else ""
    expected_glass_ws = f"ws://{public_host}:{port}/ws/control" if public_host else ""
    local_ipv4_addresses = _list_local_ipv4_addresses()

    failures: list[str] = []
    warnings: list[str] = []
    if not server_config_path.exists():
        failures.append("缺少 openaiglass-for-blind/config/local_server.env")
    if not PHONE_APP_CONFIG.exists():
        failures.append("缺少 openaiglass-for-blind/host/phone/config/AppConfig.plist")
    if not token_map:
        failures.append("DEVICE_TOKEN_MAP 为空或格式非法")
    if public_host and public_host not in local_ipv4_addresses:
        failures.append("SERVER_PUBLIC_HOST 不是当前 Mac 的可用局域网 IPv4 地址")
    if phone_id not in token_map:
        failures.append(f"DEVICE_TOKEN_MAP 缺少手机设备: {phone_id}")
    elif token_map[phone_id] != phone_token:
        failures.append("业务手机 AppConfig.plist pairToken 与 DEVICE_TOKEN_MAP 不一致")
    if glass_id not in token_map:
        failures.append(f"DEVICE_TOKEN_MAP 缺少眼镜设备: {glass_id}")
    elif token_map[glass_id] != glass_token:
        failures.append("眼镜 GLASS_PAIR_TOKEN 默认值与 DEVICE_TOKEN_MAP 不一致")
    if desired_glass_id and desired_glass_id != glass_id:
        failures.append("手机 desiredGlassDeviceID 与眼镜 GLASS_DEVICE_ID 默认值不一致")
    if expected_phone_url and phone_server_url != expected_phone_url:
        failures.append("业务手机 AppConfig.plist serverBaseURLString 与 SERVER_PUBLIC_HOST/PORT 不一致")
    if expected_glass_ws and glass_server_ws_uri != expected_glass_ws:
        failures.append("眼镜 openaiglass-for-blind/host/glass/config/local_build.env 中 GLASS_SERVER_WS_URI 与 SERVER_PUBLIC_HOST/PORT 不一致")

    return LiveCheckResult(
        name="config_alignment",
        ok=not failures,
        duration_ms=_duration_ms(start),
        details={
            "server_config": str(server_config_path),
            "server_public_host": public_host,
            "local_ipv4_addresses": local_ipv4_addresses,
            "server_port": port,
            "expected_phone_server_url": expected_phone_url,
            "actual_phone_server_url": phone_server_url,
            "expected_glass_ws_uri": expected_glass_ws,
            "actual_glass_ws_uri": glass_server_ws_uri,
            "glass_local_config": str(GLASS_LOCAL_CONFIG),
            "token_map_device_ids": sorted(token_map.keys()),
            "phone_device_id": phone_id,
            "desired_glass_device_id": desired_glass_id,
            "glass_device_id_default": glass_id,
            "failures": failures,
            "warnings": warnings,
        },
    )


def check_server_health(server_config_path: Path, *, required: bool) -> LiveCheckResult:
    """检查服务端健康状态。

    参数：
    1. `server_config_path`：本地服务端 env 配置路径。
    2. `required`：是否要求服务端必须在线。

    返回值：
    1. 单项检查结果。
    """

    start = perf_counter()
    server_config = _read_env_file(server_config_path)
    port = str(server_config.get("PORT") or "8765")
    public_host = str(server_config.get("SERVER_PUBLIC_HOST") or "").strip()
    urls = [f"http://127.0.0.1:{port}/api/health"]
    if public_host:
        urls.append(f"http://{public_host}:{port}/api/health")
    responses: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    ok = True
    for url in urls:
        try:
            with urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            url_ok = payload.get("status") == "ok" and payload.get("service") == "server-api"
            responses.append({"url": url, "ok": url_ok, "payload": payload})
            ok = ok and url_ok
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            errors.append({"url": url, "error": str(exc)})
            ok = False
    details: dict[str, object] = {
        "urls": urls,
        "responses": responses,
        "errors": errors,
    }
    if errors:
        details["message"] = "服务端未启动，或本机局域网地址不可达"
    return LiveCheckResult(
        name="server_health",
        ok=ok or not required,
        level="error" if required else "warning",
        duration_ms=_duration_ms(start),
        details=details,
    )


def build_summary(results: list[LiveCheckResult]) -> dict[str, object]:
    """构建总报告。

    参数：
    1. `results`：所有检查结果。

    返回值：
    1. JSON 可序列化报告。
    """

    blocking_failures = [item for item in results if item.level == "error" and not item.ok]
    return {
        "ok": not blocking_failures,
        "check_count": len(results),
        "passed_count": sum(1 for item in results if item.ok),
        "failed_count": sum(1 for item in results if not item.ok),
        "blocking_failed_count": len(blocking_failures),
        "checks": [asdict(item) for item in results],
    }


def main() -> int:
    """脚本主入口。"""

    args = parse_args()
    server_config_path = _resolve_path(args.server_config)
    preflight_report_path = _resolve_path(args.preflight_report)

    results = [
        check_paths(),
        check_preflight_report(preflight_report_path),
        check_config_alignment(server_config_path),
        check_server_health(server_config_path, required=bool(args.require_server)),
    ]
    report = build_summary(results)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.report:
        report_path = _resolve_path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入真机联调检查报告：{report_path}", file=sys.stderr)

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
