# iteration-v35：SDK v36 glass-playback 安装包入口收敛

## 本轮目标

让功能开发者在只安装 Python SDK 包的情况下启动 `glass-playback`，不再依赖 SDK 源码目录，也不需要在命令中手动传 `--sdk-root`。

本轮对应对外 SDK 版本：`sdk-v36`。

## 问题原因

旧命令在 `openaiglass.glass.start --runtime playback` 分支中固定按 `<sdk-root>/glass-playback` 查找运行时代码。这个设计适合 SDK 仓库源码开发，但会把 SDK 内部目录结构泄露给业务能力开发者。业务项目只安装 SDK 包时，开发者未必拥有 `openaiglass-sdk` 源码目录，`--sdk-root` 也不应该成为设备级回放的必填知识。

## 主要改动

1. `openaiglass.glass.start --runtime playback` 优先直接导入已安装包中的 `openaiglass_glass_playback`。
2. 仅在安装包中找不到 playback 运行时时，才回退到源码开发态的 `<sdk-root>/glass-playback`。
3. SDK 顶层 `pyproject.toml` 将 `glass-playback/openaiglass_glass_playback` 纳入 Python 包发现范围。
4. 开发指南中的 `glass-playback` 启动命令去掉 `--sdk-root`，并说明该参数只用于 SDK 源码或固件开发态。

## 当前边界

1. `--sdk-root` 仍保留给 ESP32 固件源码构建、烧录、监看等 SDK 开发场景。
2. `glass-playback` 的配置和测试资产仍由业务工程提供，例如 `host/glass-playback/config/*.json` 和 `testdata/*`。
3. 当前只处理 Python SDK 包形态；iOS XCFramework 和 ESP32 component registry 发布仍是后续工作。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_playback_config.py -q

PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m unittest discover \
  -s openaiglass-sdk/tests/unit -p 'test_*.py' -v
```
