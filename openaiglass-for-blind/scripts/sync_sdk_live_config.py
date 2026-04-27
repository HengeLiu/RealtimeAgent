"""同步业务真机联调配置到三端运行时。"""

from __future__ import annotations

import argparse
import plistlib
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
SERVER_CONFIG = APP_ROOT / "config/local_server.env"
SERVER_CONFIG_TEMPLATE = APP_ROOT / "config/local_server.env.example"
PHONE_BUSINESS_CONFIG = APP_ROOT / "host/phone/config/AppConfig.plist"
PHONE_BUSINESS_CONFIG_TEMPLATE = APP_ROOT / "host/phone/config/AppConfig.plist.example"
PHONE_RUNTIME_CONFIG = REPO_ROOT / "openaiglass-sdk/phone-ios/GlassesVideoReceiver/AppConfig.plist"
GLASS_LOCAL_CONFIG = APP_ROOT / "host/glass/config/local_build.env"
GLASS_LOCAL_CONFIG_TEMPLATE = APP_ROOT / "host/glass/config/local_build.env.example"


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="同步业务真机联调配置到 SDK 三端运行时")
    parser.add_argument(
        "--server-config",
        type=str,
        default=str(SERVER_CONFIG),
        help="业务服务端本地配置文件，默认 openaiglass-for-blind/config/local_server.env",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要同步的内容，不写文件",
    )
    return parser.parse_args()


def resolve_path(path_text: str) -> Path:
    """解析路径。"""

    path = Path(path_text)
    if not path.is_absolute():
        repo_candidate = REPO_ROOT / path
        path = repo_candidate if repo_candidate.exists() else APP_ROOT / path
    return path.resolve()


def read_env_file(path: Path) -> dict[str, str]:
    """读取简单 env 文件。"""

    values: dict[str, str] = {}
    if not path.exists():
        raise RuntimeError(f"配置文件不存在: {path}，请从 {SERVER_CONFIG_TEMPLATE} 复制后修改")
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


def parse_token_map(token_map: str) -> dict[str, str]:
    """解析配对令牌映射。"""

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


def parse_device_ids(values: dict[str, str], token_map: dict[str, str]) -> tuple[str, str]:
    """从服务端配置解析眼镜和手机设备编号。

    参数：
    1. `values`：服务端 env 配置。
    2. `token_map`：设备编号到配对令牌的映射。

    返回值：
    1. `(glass_device_id, phone_device_id)`。

    异常情况：
    1. 设备编号为空或无法从 `DEVICE_TOKEN_MAP` 推断时抛出 `RuntimeError`。
    """

    glass_device_id = str(values.get("GLASS_DEVICE_ID") or "").strip()
    phone_device_id = str(values.get("PHONE_DEVICE_ID") or "").strip()
    for device_id in token_map:
        if not glass_device_id and device_id.startswith("glass"):
            glass_device_id = device_id
        if not phone_device_id and device_id.startswith("phone"):
            phone_device_id = device_id
    if not glass_device_id:
        glass_device_id = "glass-001"
    if not phone_device_id:
        phone_device_id = "phone-001"
    if glass_device_id not in token_map or phone_device_id not in token_map:
        raise RuntimeError(f"DEVICE_TOKEN_MAP 必须包含 {glass_device_id} 和 {phone_device_id}")
    return glass_device_id, phone_device_id


def quote_env(value: str) -> str:
    """把值写成双引号 env 字符串。"""

    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def upsert_env_lines(existing_text: str, updates: dict[str, str]) -> str:
    """更新 env 文本中的指定键。"""

    lines = existing_text.splitlines()
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={quote_env(updates[key])}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={quote_env(value)}")
    return "\n".join(output) + "\n"


def _read_phone_business_config() -> dict[str, object]:
    """读取业务侧手机配置。

    返回值：
    1. 配置字典。业务配置不存在时读取 example，仍不存在时读取 SDK 运行时配置。
    """

    source = PHONE_BUSINESS_CONFIG
    if not source.exists() and PHONE_BUSINESS_CONFIG_TEMPLATE.exists():
        source = PHONE_BUSINESS_CONFIG_TEMPLATE
    if not source.exists():
        source = PHONE_RUNTIME_CONFIG
    with source.open("rb") as file:
        payload = plistlib.load(file)
    if not isinstance(payload, dict):
        raise RuntimeError("AppConfig.plist 根节点不是字典")
    return payload


def sync_phone_config(*, server_url: str, phone_device_id: str, phone_token: str, glass_device_id: str, dry_run: bool) -> None:
    """同步手机端业务配置和 SDK 运行时配置。"""

    payload = _read_phone_business_config()
    payload["serverBaseURLString"] = server_url
    payload["phoneDeviceID"] = phone_device_id
    payload["pairToken"] = phone_token
    payload["desiredGlassDeviceID"] = glass_device_id
    if not dry_run:
        PHONE_BUSINESS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        with PHONE_BUSINESS_CONFIG.open("wb") as file:
            plistlib.dump(payload, file, sort_keys=False)
        PHONE_RUNTIME_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        with PHONE_RUNTIME_CONFIG.open("wb") as file:
            plistlib.dump(payload, file, sort_keys=False)


def sync_glass_config(*, ws_uri: str, glass_device_id: str, glass_token: str, dry_run: bool) -> None:
    """同步眼镜端本地构建配置。"""

    if GLASS_LOCAL_CONFIG.exists():
        existing_text = GLASS_LOCAL_CONFIG.read_text(encoding="utf-8")
    elif GLASS_LOCAL_CONFIG_TEMPLATE.exists():
        existing_text = GLASS_LOCAL_CONFIG_TEMPLATE.read_text(encoding="utf-8")
    else:
        existing_text = ""
    updated = upsert_env_lines(
        existing_text,
        {
            "GLASS_SERVER_WS_URI": ws_uri,
            "GLASS_DEVICE_ID": glass_device_id,
            "GLASS_PAIR_TOKEN": glass_token,
        },
    )
    if not dry_run:
        GLASS_LOCAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        GLASS_LOCAL_CONFIG.write_text(updated, encoding="utf-8")


def main() -> int:
    """脚本主入口。"""

    args = parse_args()
    server_config_path = resolve_path(args.server_config)
    server_config = read_env_file(server_config_path)
    public_host = str(server_config.get("SERVER_PUBLIC_HOST") or "").strip()
    port = str(server_config.get("PORT") or "8765").strip()
    token_map = parse_token_map(str(server_config.get("DEVICE_TOKEN_MAP") or ""))

    if not public_host:
        raise RuntimeError("SERVER_PUBLIC_HOST 不能为空，否则无法同步手机和眼镜局域网地址")
    glass_device_id, phone_device_id = parse_device_ids(server_config, token_map)

    server_url = f"http://{public_host}:{port}"
    ws_uri = f"ws://{public_host}:{port}/ws/control"
    sync_phone_config(
        server_url=server_url,
        phone_device_id=phone_device_id,
        phone_token=token_map[phone_device_id],
        glass_device_id=glass_device_id,
        dry_run=bool(args.dry_run),
    )
    sync_glass_config(
        ws_uri=ws_uri,
        glass_device_id=glass_device_id,
        glass_token=token_map[glass_device_id],
        dry_run=bool(args.dry_run),
    )

    action = "将同步" if args.dry_run else "已同步"
    print(f"{action}业务手机配置: {PHONE_BUSINESS_CONFIG}")
    print(f"{action}SDK iOS 运行时配置: {PHONE_RUNTIME_CONFIG}")
    print(f"{action}眼镜本地配置: {GLASS_LOCAL_CONFIG}")
    print(f"server_url={server_url}")
    print(f"glass_ws_uri={ws_uri}")
    print(f"phone_device_id={phone_device_id}")
    print(f"glass_device_id={glass_device_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"同步 SDK 真机联调配置失败: {exc}", file=sys.stderr)
        raise SystemExit(1)
