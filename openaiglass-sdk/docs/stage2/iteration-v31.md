# iteration-v31：SDK v32 服务端 Secret 环境合并

## 本轮目标

修复开发者已经在外部环境变量中配置 `DASHSCOPE_API_KEY`，但服务端仍提示缺少 key 的问题。

本轮对应对外 SDK 版本：`sdk-v32`。

## 问题原因

`openaiglass.server.run/start` 启动器此前按以下顺序合并环境：

1. 继承当前 shell 环境变量。
2. 读取 `config/local_server.env` 并覆盖同名变量。
3. 补齐 SDK 默认值。

如果 `local_server.env` 保留模板占位：

```env
DASHSCOPE_API_KEY=""
```

它会把 shell 或 CI 中已经注入的真实 key 覆盖为空，导致运行时 `ServerSettings.dashscope_api_key` 为空。

## 主要改动

1. `merged_env(...)` 对 `DASHSCOPE_API_KEY` 增加 secret 合并规则。
2. 当配置文件中的 `DASHSCOPE_API_KEY` 为空，且外部环境已有非空 key 时，保留外部 key。
3. 普通配置仍然由 `local_server.env` 覆盖外部环境，避免改变现有模型、端口和设备配置语义。
4. 新增单元测试覆盖空本地占位符不覆盖外部 key。

## 当前边界

1. 如果外部环境没有 key，且 `local_server.env` 仍为空，服务端仍会按预期报 `缺少 DASHSCOPE_API_KEY`。
2. 如果 `local_server.env` 中写了非空 key，它仍会覆盖外部环境。
3. 修改 key 后必须重启服务端，已运行进程不会自动刷新环境变量。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_server_cli.py \
  openaiglass-sdk/tests/unit/test_settings.py -q
```
