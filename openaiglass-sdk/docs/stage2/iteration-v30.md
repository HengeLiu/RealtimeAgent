# iteration-v30：SDK v31 服务端前台运行生命周期

## 本轮目标

让 `openaiglass.server.run` 成为真正的前台运行命令。开发者用 Ctrl+C 结束命令或关闭当前终端时，本地服务端应随命令一起退出，不再需要额外执行 `openaiglass.server.stop`。

本轮对应对外 SDK 版本：`sdk-v31`。

## 主要改动

1. `server local all` 改为直接调用前台运行逻辑，而不是后台 `start` 后再 `tail -F` 日志。
2. 新增 `run_local_foreground(...)`，以前台子进程启动 `openaiglasses.cli.server_runtime`。
3. 前台运行不写 PID 文件、不重定向 stdout/stderr，也不使用 `start_new_session`。
4. Ctrl+C 时 CLI 会终止子进程；如果子进程未能及时退出，会升级为 kill。
5. `openaiglass.server.start/stop/logs` 仍保留原有后台管理语义。

## 使用边界

1. 日常联调推荐 `openaiglass.server.run`。
2. 需要跨终端保留服务端时，使用 `openaiglass.server.start`，然后用 `openaiglass.server.logs` 看日志，用 `openaiglass.server.stop` 停止。
3. 如果已经有后台 PID 文件指向正在运行的服务端，前台 `run` 会拒绝启动并提示先 stop 或 logs。

## 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_server_cli.py -q
```
