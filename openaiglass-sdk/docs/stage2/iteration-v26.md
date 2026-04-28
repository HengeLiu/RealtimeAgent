# iteration-v26：SDK v27 服务端默认配置单一来源

## 本轮目标

修复 `settings.py` 和服务端 CLI 同时维护大量相同配置默认值的问题，避免模型、音色、心跳等默认值在两处漂移。

本轮对应对外 SDK 版本：`sdk-v27`。

## 主要改动

1. `ServerSettings` 继续作为服务端运行时配置的唯一默认值、读取和校验入口。
2. `openaiglasses.cli.server.SERVER_DEFAULTS` 改为通过 `ServerSettings()` 派生，不再手写第二份模型配置。
3. CLI 仍保留 `HOST` / `PORT` 作为 `local_server.env` 的用户友好别名，并在启动时转换为运行时实际读取的 `SERVER_HOST` / `SERVER_PORT`。
4. 新增单元测试，验证 CLI 默认值与 `ServerSettings` 默认值一致。

## 为什么以前会重复

`settings.py` 面向服务端进程运行时，负责类型化配置和校验；`server.py` 面向启动器，负责合并 `local_server.env`、命令行参数和默认环境变量。历史上为了让启动器在配置文件缺项时仍能启动，CLI 复制了一份默认值，但这会造成配置漂移。

## 当前边界

1. `LOG_FILE` 仍由 CLI 特殊处理：后台启动时 stdout/stderr 已经重定向到启动器日志文件，所以子进程 `LOG_FILE` 会被置空，避免重复写日志。
2. `SERVER_PUBLIC_HOST` 是业务侧同步配置，不属于 `ServerSettings` 运行时监听配置。
3. `VOICE_RUNS_ROOT` 在 CLI 中仍可按 repo root 派生默认路径，运行时继续通过 `ServerSettings` 读取。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_server_cli.py \
  openaiglass-sdk/tests/unit/test_settings.py -q
```
