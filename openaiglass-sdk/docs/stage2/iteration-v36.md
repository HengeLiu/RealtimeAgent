# iteration-v36：SDK v37 glass-playback 状态日志格式统一

## 本轮目标

让 `glass-playback` 命令行状态日志能和服务端日志按时间直接对齐，同时去掉固定 `[glass-playback]` 前缀，减少多设备联调时的人工整理成本。

本轮对应对外 SDK 版本：`sdk-v37`。

## 主要改动

1. `glass-playback` 的 `_print_status(...)` 统一输出 UTC ISO 时间戳。
2. 状态日志格式调整为 `时间-INFO-glass.playback---消息 key=value`。
3. 继续保持“只打印收到的控制消息，不打印自身发送的控制消息正文”的设备侧日志边界。

## 当前边界

1. 本轮只调整 `glass-playback` 命令行状态日志，不改变事件 JSONL 和执行器 JSONL 的结构。
2. 本轮不改变回放协议、设备注册、绑定等待、音频上传或执行器处理逻辑。

## 验证

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback:openaiglass-for-blind \
uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_playback_config.py -q
```
