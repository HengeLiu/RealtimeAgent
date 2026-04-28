# iteration-v29：SDK v30 顶层 Python 安装入口

## 本轮目标

让本地开发者可以使用更自然的安装命令：

```bash
uv pip install -e openaiglass-sdk
```

此前需要安装 `openaiglass-sdk/server-python`，暴露了 SDK 内部目录结构。

本轮对应对外 SDK 版本：`sdk-v30`。

## 主要改动

1. 在 `openaiglass-sdk/` 顶层新增 `pyproject.toml`。
2. 顶层项目名仍为 `openaiglasses-sdk`，CLI 入口仍为 `openaiglass.*`。
3. `setuptools.package-dir` 指向 `server-python`，包发现范围为 `server-python` 下的 `openaiglasses`、`agent_core`、`protocol`、`runtime` 等模块。
4. 保留 `server-python/pyproject.toml`，便于内部包源码目录继续独立构建和调试。
5. 更新开发指南和 SDK README，把本地 editable 安装命令改为顶层入口。

## 当前边界

1. 顶层 `pyproject.toml` 是 Python SDK 的聚合安装入口，不代表 iOS 和 ESP32 已经变成 Python 包。
2. `server-python` 仍是实际 Python 源码目录，端侧源码仍分别位于 `phone-ios` 和 `glass-esp32`。
3. 后续若做正式多包 workspace 发布，可以再把顶层入口扩展为统一版本治理和构建入口。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_package_check.py -q
```

也可以在临时虚拟环境中验证：

```bash
uv venv /tmp/openaiglass-sdk-install-check
/tmp/openaiglass-sdk-install-check/bin/python -m pip install -e openaiglass-sdk
/tmp/openaiglass-sdk-install-check/bin/python -c "import openaiglasses; print(openaiglasses.__name__)"
```
