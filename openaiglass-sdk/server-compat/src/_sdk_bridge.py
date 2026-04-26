"""旧 `server/src` 导入路径到 `openaiglass-sdk/python` 的包桥接工具。"""

from __future__ import annotations

from pathlib import Path


def bridge_package(module_globals: dict, package_name: str) -> None:
    """把当前包桥接到 `openaiglass-sdk/python/<package_name>`。

    主要逻辑：
    1. 计算 SDK 中对应包目录。
    2. 把 SDK 包目录追加到当前包的 `__path__`。
    3. 执行 SDK 包中的 `__init__.py`，让旧导入路径继续可用。

    参数：
    1. `module_globals`：当前模块 `globals()`。
    2. `package_name`：目标包名。

    异常情况：
    1. SDK 中缺少目标包时抛出 `RuntimeError`。
    """

    current_file = Path(str(module_globals["__file__"])).resolve()
    repo_root = current_file.parents[4]
    sdk_package_dir = repo_root / "openaiglass-sdk/python" / package_name
    sdk_init_file = sdk_package_dir / "__init__.py"
    if not sdk_init_file.exists():
        raise RuntimeError(f"SDK 中缺少桥接目标包: {package_name}")

    package_path = module_globals.setdefault("__path__", [])
    sdk_path = str(sdk_package_dir)
    if sdk_path not in package_path:
        package_path.append(sdk_path)

    module_globals["__file__"] = str(sdk_init_file)
    exec(compile(sdk_init_file.read_text(encoding="utf-8"), str(sdk_init_file), "exec"), module_globals)
