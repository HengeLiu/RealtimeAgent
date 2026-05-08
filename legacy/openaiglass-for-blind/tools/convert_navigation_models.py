"""把本地下载的导航视觉模型转换为 iOS 可打包的 CoreML 资源。

本脚本只处理本地开发机上的模型产物，不把真实权重提交到仓库。默认把
ModelScope 下载目录中的 `trafficlight.pt` 转成业务 iOS App 可直接复制的
`TrafficLightYOLO.mlmodelc`。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


DEFAULT_MODEL_ROOT = Path.home() / ".cache/modelscope/hub/models/archifancy/AIGlasses_for_navigation"
DEFAULT_APP_MODEL_DIR = Path("openaiglass-for-blind/host/phone/models")


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    主要逻辑：
    1. 默认读取用户本机 ModelScope 缓存目录。
    2. 默认输出到业务 iOS 宿主的本地模型目录。
    3. 支持调用方显式覆盖输入权重和输出目录，便于后续替换模型。

    参数：无，直接从命令行读取。
    返回值：包含路径和导出参数的命令行配置。
    异常情况：参数格式不合法时由 `argparse` 输出错误并退出。
    """

    parser = argparse.ArgumentParser(description="转换导航视觉模型为 CoreML 本地资源")
    parser.add_argument(
        "--trafficlight-pt",
        type=Path,
        default=DEFAULT_MODEL_ROOT / "trafficlight.pt",
        help="trafficlight.pt 权重路径",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_APP_MODEL_DIR,
        help="输出 .mlpackage 和 .mlmodelc 的目录",
    )
    parser.add_argument("--imgsz", type=int, default=640, help="YOLO 导出输入尺寸")
    return parser.parse_args()


def export_trafficlight_model(source: Path, output_dir: Path, imgsz: int) -> Path:
    """导出红绿灯 YOLO CoreML 模型。

    主要逻辑：
    1. 用 Ultralytics 读取 `trafficlight.pt`。
    2. 使用 `nms=False` 保留 7 类模型的原始 `[1, 11, 8400]` 输出，避免
       CoreML Pipeline 后处理把类别维度改成通用 80 类。
    3. 调用 `xcrun coremlcompiler compile` 生成 iOS App 可直接放入 Bundle 的
       `TrafficLightYOLO.mlmodelc`。

    参数：
    1. `source`：本地 PyTorch 权重路径。
    2. `output_dir`：本地 CoreML 输出目录。
    3. `imgsz`：导出时使用的方形输入尺寸。

    返回值：生成的 `TrafficLightYOLO.mlmodelc` 目录路径。
    异常情况：
    1. 权重不存在时抛出 `FileNotFoundError`。
    2. 未安装 `ultralytics` 或 `xcrun` 编译失败时抛出对应异常。
    """

    if not source.exists():
        raise FileNotFoundError(f"未找到红绿灯模型权重：{source}")

    from ultralytics import YOLO

    output_dir.mkdir(parents=True, exist_ok=True)
    package_path = output_dir / "TrafficLightYOLO.mlpackage"
    compiled_path = output_dir / "TrafficLightYOLO.mlmodelc"
    if package_path.exists():
        shutil.rmtree(package_path)
    if compiled_path.exists():
        shutil.rmtree(compiled_path)

    model = YOLO(str(source))
    exported_path = Path(model.export(format="coreml", nms=False, imgsz=imgsz, simplify=True))
    shutil.move(str(exported_path), str(package_path))

    subprocess.run(
        ["xcrun", "coremlcompiler", "compile", str(package_path), str(output_dir)],
        check=True,
    )
    if not compiled_path.exists():
        raise RuntimeError(f"CoreML 编译完成但未找到输出目录：{compiled_path}")
    return compiled_path


def main() -> None:
    """执行模型转换入口。

    主要逻辑：
    1. 解析参数。
    2. 转换并编译红绿灯模型。
    3. 打印生成路径，供真机打包和排障使用。

    参数：无。
    返回值：无。
    异常情况：转换失败时异常直接向上抛出，使命令返回非零退出码。
    """

    args = parse_args()
    compiled = export_trafficlight_model(
        source=args.trafficlight_pt.expanduser().resolve(),
        output_dir=args.output_dir.resolve(),
        imgsz=args.imgsz,
    )
    print(f"generated: {compiled}")


if __name__ == "__main__":
    main()
