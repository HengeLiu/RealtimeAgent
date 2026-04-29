# Python SDK 打包与发布流程

## 1. 文档目标

本文说明 `openaiglasses-sdk` 从源码到 pip 安装包的发布流程。

目标是保证外部开发者可以通过：

```bash
pip install openaiglasses-sdk
```

安装 SDK，并只依赖 `openaiglasses` 公开 API 完成能力扩展开发。

## 2. 包结构

Python SDK 包位于：

```text
openaiglass-sdk/server-python/
  pyproject.toml
  README.md
  openaiglasses/
  agent_core/
  backend_task_core/
  api/
  runtime/
  protocol/
  infra/
```

其中：

1. `openaiglasses` 是开发者公开导入入口。
2. `agent_core / backend_task_core / api / runtime / protocol / infra` 是 SDK 内部运行时包，随 wheel 一起发布。
3. 外部业务项目不应直接依赖 `openaiglass-sdk/server-python` 或仓库根路径。

## 3. 本地安装验证

在仓库根目录执行：

```bash
pip install ./openaiglass-sdk/server-python
```

或使用当前仓库验证脚本：

```bash
python script/run_sdk_package_check.py
```

预期结果：

1. 成功构建 `openaiglasses_sdk-<version>-py3-none-any.whl`。
2. 成功把 wheel 安装到临时目录。
3. 安装后可以导入 `OpenAIGlassesSDK / ServerSettings`。
4. 安装后可以导入 SDK 内部运行时模块。

## 4. 发布前验收

发布前必须执行：

```bash
python script/run_sdk_preflight.py --report logs/sdk-preflight-release.json
```

预期结果：

1. `ok` 为 `true`。
2. `sdk_package` 通过。
3. `sdk_boundary` 无违规项。
4. 场景回放、契约测试、兼容性测试、核心 pytest、服务健康检查全部通过。

## 5. 构建发布产物

当前仓库的包构建检查使用 `setuptools.build_meta`，正式发布时建议使用标准构建工具：

```bash
python -m pip install build twine
python -m build openaiglass-sdk/server-python
```

产物位于：

```text
openaiglass-sdk/server-python/dist/
  openaiglasses_sdk-<version>-py3-none-any.whl
  openaiglasses_sdk-<version>.tar.gz
```

`dist/` 属于构建产物，不应提交到 git。

## 6. TestPyPI 预发布

首次发布或版本结构变化时，先发布到 TestPyPI：

```bash
python -m twine upload --repository testpypi openaiglass-sdk/server-python/dist/*
```

在干净环境验证：

```bash
python -m venv /tmp/openaiglasses-sdk-test
/tmp/openaiglasses-sdk-test/bin/python -m pip install --index-url https://test.pypi.org/simple --extra-index-url https://pypi.org/simple openaiglasses-sdk
/tmp/openaiglasses-sdk-test/bin/python -c "from openaiglasses import OpenAIGlassesSDK, ServerSettings; print(OpenAIGlassesSDK, ServerSettings)"
```

## 7. PyPI 正式发布

TestPyPI 验证通过后发布到 PyPI：

```bash
python -m twine upload openaiglass-sdk/server-python/dist/*
```

正式发布后验证：

```bash
python -m venv /tmp/openaiglasses-sdk-prod
/tmp/openaiglasses-sdk-prod/bin/python -m pip install openaiglasses-sdk
/tmp/openaiglasses-sdk-prod/bin/python -c "from openaiglasses import OpenAIGlassesSDK, ServerSettings; print(OpenAIGlassesSDK, ServerSettings)"
```

## 8. 版本管理规则

1. 每次发布前更新 [openaiglass-sdk/server-python/pyproject.toml](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/openaiglass-sdk/server-python/pyproject.toml) 中的 `version`。
2. 修改公开 API 时，同步更新 [SDK开发者快速开始.md](./SDK开发者快速开始.md)。
3. 修改系统协议或运行时契约时，同步更新 `testdata/contracts` 和契约测试。
4. 发布前保留 `logs/sdk-preflight-release.json` 作为验收记录，但 `logs/` 不提交到 git。
