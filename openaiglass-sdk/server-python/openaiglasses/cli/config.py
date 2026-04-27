"""配置同步通用命令。"""

from __future__ import annotations

import argparse
import json
import plistlib
import socket
import subprocess
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """构建配置命令参数解析器。

    返回值：
    1. `argparse.ArgumentParser` 实例。
    """

    parser = argparse.ArgumentParser(prog="openaiglass config", description="同步 OpenAI Glasses 三端本地联调配置")
    subparsers = parser.add_subparsers(dest="action")
    sync_parser = subparsers.add_parser("sync", help="同步服务端、手机端和眼镜端配置")
    sync_parser.add_argument("--app-root", default="openaiglass-for-blind", help="业务工程根目录")
    sync_parser.add_argument("--server-config", default="", help="服务端 env 配置文件")
    sync_parser.add_argument("--phone-config", default="", help="手机端 AppConfig.plist 配置文件")
    sync_parser.add_argument("--phone-mock-config", default="", help="phone-mock JSON 配置文件")
    sync_parser.add_argument("--glass-config", default="", help="眼镜端 local_build.env 配置文件")
    sync_parser.add_argument("--public-host", default="", help="手动指定本机局域网 IPv4")
    sync_parser.add_argument("--dry-run", action="store_true", help="只打印同步结果，不写文件")
    sync_parser.add_argument("--skip-phone", action="store_true", help="不写手机端配置")
    sync_parser.add_argument("--skip-phone-mock", action="store_true", help="不写 phone-mock 配置")
    sync_parser.add_argument("--skip-glass", action="store_true", help="不写眼镜端配置")
    return parser


def main(argv: list[str] | None = None) -> int:
    """配置命令主入口。

    参数：
    1. `argv`：命令行参数，不包含程序名。

    返回值：
    1. 进程退出码。
    """

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.action != "sync":
        parser.print_help()
        return 0
    return sync_config(args)


def sync_config(args: argparse.Namespace) -> int:
    """同步三端联调配置。

    参数：
    1. `args`：命令行参数。

    返回值：
    1. 进程退出码。
    """

    paths = resolve_paths(args)
    server_config = read_env_file(paths["server_config"], paths["server_template"])
    public_host, detected_hosts, public_host_source = resolve_public_host(
        str(server_config.get("SERVER_PUBLIC_HOST") or ""),
        str(args.public_host or ""),
    )
    port = str(server_config.get("PORT") or "8765").strip()
    token_map = parse_token_map(str(server_config.get("DEVICE_TOKEN_MAP") or ""))
    glass_device_id, phone_device_id = parse_device_ids(server_config, token_map)

    server_url = f"http://{public_host}:{port}"
    ws_uri = f"ws://{public_host}:{port}/ws/control"
    sync_server_public_host(paths["server_config"], public_host, bool(args.dry_run))
    if not args.skip_phone:
        sync_phone_config(
            phone_config=paths["phone_config"],
            phone_template=paths["phone_template"],
            server_url=server_url,
            phone_device_id=phone_device_id,
            phone_token=token_map[phone_device_id],
            glass_device_id=glass_device_id,
            dry_run=bool(args.dry_run),
        )
    if not args.skip_phone_mock:
        sync_phone_mock_config(
            phone_mock_config=paths["phone_mock_config"],
            ws_uri=ws_uri,
            public_host=public_host,
            phone_device_id=phone_device_id,
            phone_token=token_map[phone_device_id],
            dry_run=bool(args.dry_run),
        )
    if not args.skip_glass:
        sync_glass_config(
            glass_config=paths["glass_config"],
            glass_template=paths["glass_template"],
            ws_uri=ws_uri,
            glass_device_id=glass_device_id,
            glass_token=token_map[glass_device_id],
            dry_run=bool(args.dry_run),
        )

    action = "将同步" if args.dry_run else "已同步"
    print(f"{action}服务端本机地址: {paths['server_config']}")
    if not args.skip_phone:
        print(f"{action}业务手机配置: {paths['phone_config']}")
    if not args.skip_phone_mock:
        print(f"{action} phone-mock 配置: {paths['phone_mock_config']}")
    if not args.skip_glass:
        print(f"{action}眼镜本地配置: {paths['glass_config']}")
    print(f"public_host={public_host}")
    print(f"public_host_source={public_host_source}")
    if detected_hosts:
        print(f"detected_ipv4_candidates={','.join(detected_hosts)}")
    print(f"server_url={server_url}")
    print(f"glass_ws_uri={ws_uri}")
    print(f"phone_device_id={phone_device_id}")
    print(f"glass_device_id={glass_device_id}")
    return 0


def resolve_paths(args: argparse.Namespace) -> dict[str, Path]:
    """解析业务配置路径。

    参数：
    1. `args`：命令行参数。

    返回值：
    1. 各配置文件和模板文件路径。
    """

    app_root = Path(args.app_root).resolve()
    return {
        "app_root": app_root,
        "server_config": resolve_path(args.server_config, app_root, "config/local_server.env"),
        "server_template": app_root / "config/local_server.env.example",
        "phone_config": resolve_path(args.phone_config, app_root, "host/phone/config/AppConfig.plist"),
        "phone_template": app_root / "host/phone/config/AppConfig.plist.example",
        "phone_mock_config": resolve_path(args.phone_mock_config, app_root, "host/phone-mock/config/phone.mock.json"),
        "glass_config": resolve_path(args.glass_config, app_root, "host/glass/config/local_build.env"),
        "glass_template": app_root / "host/glass/config/local_build.env.example",
    }


def resolve_path(path_text: str, app_root: Path, default_relative: str) -> Path:
    """解析配置路径。

    参数：
    1. `path_text`：命令行传入路径。
    2. `app_root`：业务工程根目录。
    3. `default_relative`：默认相对业务工程路径。

    返回值：
    1. 绝对路径。
    """

    path = Path(path_text) if path_text else app_root / default_relative
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def read_env_file(path: Path, template: Path) -> dict[str, str]:
    """读取简单 env 配置文件。

    参数：
    1. `path`：配置文件路径。
    2. `template`：用于错误提示的模板路径。

    返回值：
    1. 配置键值字典。
    """

    if not path.exists():
        raise RuntimeError(f"配置文件不存在: {path}，请从 {template} 复制后修改")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def parse_token_map(token_map: str) -> dict[str, str]:
    """解析设备配对令牌映射。"""

    result: dict[str, str] = {}
    for item in token_map.split(","):
        text = item.strip()
        if not text or "=" not in text:
            continue
        device_id, token = text.split("=", 1)
        if device_id.strip():
            result[device_id.strip()] = token.strip()
    return result


def parse_device_ids(values: dict[str, str], token_map: dict[str, str]) -> tuple[str, str]:
    """从配置和令牌映射中解析眼镜、手机设备编号。"""

    glass_device_id = str(values.get("GLASS_DEVICE_ID") or "").strip()
    phone_device_id = str(values.get("PHONE_DEVICE_ID") or "").strip()
    for device_id in token_map:
        if not glass_device_id and device_id.startswith("glass"):
            glass_device_id = device_id
        if not phone_device_id and device_id.startswith("phone"):
            phone_device_id = device_id
    glass_device_id = glass_device_id or "glass-001"
    phone_device_id = phone_device_id or "phone-001"
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
        if not stripped or stripped.startswith("#") or "=" not in line:
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


def sync_server_public_host(server_config: Path, public_host: str, dry_run: bool) -> None:
    """把自动探测到的服务端地址写回服务端配置。"""

    if dry_run:
        return
    existing_text = server_config.read_text(encoding="utf-8")
    server_config.write_text(upsert_env_lines(existing_text, {"SERVER_PUBLIC_HOST": public_host}), encoding="utf-8")


def sync_phone_config(
    *,
    phone_config: Path,
    phone_template: Path,
    server_url: str,
    phone_device_id: str,
    phone_token: str,
    glass_device_id: str,
    dry_run: bool,
) -> None:
    """同步手机端业务配置。"""

    source = phone_config if phone_config.exists() else phone_template
    if not source.exists():
        raise RuntimeError(f"手机配置模板不存在: {phone_template}")
    with source.open("rb") as file:
        payload = plistlib.load(file)
    if not isinstance(payload, dict):
        raise RuntimeError("AppConfig.plist 根节点不是字典")
    payload["serverBaseURLString"] = server_url
    payload["phoneDeviceID"] = phone_device_id
    payload["pairToken"] = phone_token
    payload["desiredGlassDeviceID"] = glass_device_id
    if not dry_run:
        phone_config.parent.mkdir(parents=True, exist_ok=True)
        with phone_config.open("wb") as file:
            plistlib.dump(payload, file, sort_keys=False)


def sync_glass_config(
    *,
    glass_config: Path,
    glass_template: Path,
    ws_uri: str,
    glass_device_id: str,
    glass_token: str,
    dry_run: bool,
) -> None:
    """同步眼镜端本地构建配置。"""

    if glass_config.exists():
        existing_text = glass_config.read_text(encoding="utf-8")
    elif glass_template.exists():
        existing_text = glass_template.read_text(encoding="utf-8")
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
        glass_config.parent.mkdir(parents=True, exist_ok=True)
        glass_config.write_text(updated, encoding="utf-8")


def sync_phone_mock_config(
    *,
    phone_mock_config: Path,
    ws_uri: str,
    public_host: str,
    phone_device_id: str,
    phone_token: str,
    dry_run: bool,
) -> None:
    """同步 `phone-mock` 设备配置。"""

    if not phone_mock_config.exists():
        return
    payload = json.loads(phone_mock_config.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("phone-mock 配置根节点不是字典")
    payload["device_type"] = "phone"
    payload["device_id"] = phone_device_id
    payload["pair_token"] = phone_token
    payload["control_ws_url"] = ws_uri
    camera_sink = payload.get("camera_sink")
    if isinstance(camera_sink, dict):
        camera_sink["public_host"] = public_host
        if "save_dir" in camera_sink:
            camera_sink["save_dir"] = f"openaiglass-for-blind/runs/phone-mock/{phone_device_id}/camera"
    if not dry_run:
        phone_mock_config.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_public_host(configured_host: str, override_host: str) -> tuple[str, list[str], str]:
    """解析本机服务端局域网地址。"""

    if override_host.strip():
        host = override_host.strip()
        if not _is_usable_ipv4(host):
            raise RuntimeError(f"--public-host 不是可用 IPv4: {host}")
        return host, [host], "manual"
    candidates = _detect_local_ipv4_candidates()
    if not candidates:
        if _is_usable_ipv4(configured_host):
            return configured_host.strip(), [], "config-fallback"
        raise RuntimeError("无法自动探测本机局域网 IPv4，请检查网络连接，或用 --public-host 手动指定")
    return candidates[0], candidates, "auto"


def _is_usable_ipv4(value: str) -> bool:
    """判断 IPv4 是否适合给手机和眼镜访问。"""

    text = value.strip()
    if not text or text.startswith("127.") or text == "0.0.0.0":
        return False
    parts = text.split(".")
    if len(parts) != 4:
        return False
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return False
    return all(0 <= number <= 255 for number in numbers)


def _run_command(args: list[str]) -> str:
    """执行系统命令并返回标准输出。"""

    try:
        completed = subprocess.run(args, check=False, capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _append_candidate(candidates: list[str], value: str) -> None:
    """把可用 IPv4 追加到候选列表并去重。"""

    text = value.strip()
    if _is_usable_ipv4(text) and text not in candidates:
        candidates.append(text)


def _default_route_interface() -> str:
    """获取当前系统默认路由接口名。"""

    route_output = _run_command(["route", "-n", "get", "default"])
    for line in route_output.splitlines():
        stripped = line.strip()
        if stripped.startswith("interface:"):
            return stripped.split(":", 1)[1].strip()
    ip_output = _run_command(["ip", "route", "show", "default"])
    parts = ip_output.split()
    if "dev" in parts:
        index = parts.index("dev")
        if index + 1 < len(parts):
            return parts[index + 1].strip()
    return ""


def _detect_local_ipv4_candidates() -> list[str]:
    """探测当前机器可给同一局域网设备访问的 IPv4 候选。"""

    candidates: list[str] = []
    default_interface = _default_route_interface()
    if default_interface:
        _append_candidate(candidates, _run_command(["ipconfig", "getifaddr", default_interface]))
    for interface in ("en0", "en1", "en2", "bridge100"):
        _append_candidate(candidates, _run_command(["ipconfig", "getifaddr", interface]))
    ifconfig_output = _run_command(["ifconfig"])
    current_block: list[str] = []
    for line in ifconfig_output.splitlines() + [""]:
        if line and not line.startswith(("\t", " ")):
            if current_block:
                _append_ifconfig_block_candidate(candidates, current_block)
            current_block = [line]
        elif current_block:
            current_block.append(line)
    hostname_output = _run_command(["hostname", "-I"])
    for item in hostname_output.split():
        _append_candidate(candidates, item)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
            udp_socket.connect(("8.8.8.8", 80))
            _append_candidate(candidates, udp_socket.getsockname()[0])
    except OSError:
        pass
    try:
        for item in socket.gethostbyname_ex(socket.gethostname())[2]:
            _append_candidate(candidates, item)
    except OSError:
        pass
    return candidates


def _append_ifconfig_block_candidate(candidates: list[str], block: list[str]) -> None:
    """从 ifconfig 单个网卡信息块中提取可用 IPv4。"""

    header = block[0]
    interface_name = header.split(":", 1)[0].strip()
    if interface_name.startswith(("lo", "utun", "awdl", "llw")):
        return
    text = "\n".join(block)
    if "status: active" not in text and "RUNNING" not in header:
        return
    for line in block:
        stripped = line.strip()
        if not stripped.startswith("inet "):
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            _append_candidate(candidates, parts[1])
