# WebRTC 全双工迁移测试

这个目录用于隔离验证“浏览器 WebRTC AEC + Omni Realtime”这套全双工语音链路。它不是业务功能代码，也不是 SDK 正式运行时；目标是把已经验证可行的方案收拢成一个可重复测试的迁移样板。

## 验证目标

- 浏览器负责采集麦克风，并启用系统级 `echoCancellation`、`noiseSuppression`、`autoGainControl`。
- 同一个浏览器页面播放 Omni 返回的音频，让浏览器 AEC 可以拿到真实远端参考信号。
- 播放过程中用户开口时，页面本地停止播放，并通过 relay 向 Omni 发送 `response.cancel`，验证全双工打断。
- relay 只负责协议转发，不做本地 Python/Swift 自研 AEC。

## 启动方式

```bash
uv run --with websockets --with dashscope \
  python openaiglass-sdk/server-python/devtools/webrtc_full_duplex_migration/relay.py
```

如果没有在环境变量里配置 DashScope Key：

```bash
uv run --with websockets --with dashscope \
  python openaiglass-sdk/server-python/devtools/webrtc_full_duplex_migration/relay.py \
  --api-key "$DASHSCOPE_API_KEY"
```

启动后打开：

```text
http://127.0.0.1:9877/
```

## 观察点

- 页面日志中应看到 `mic settings`，其中浏览器实际启用的 AEC/NS/AGC 配置会被打印出来。
- 正常说话后，应看到 Omni 的转写和回复事件。
- Omni 播放过程中直接插话，页面应出现 `barge-in triggered ...`，同时 relay 应打印 `response.cancel forwarded`。
- 如果出现模型把自己刚播放的内容当作用户输入，说明当前浏览器/设备组合没有形成有效 AEC 参考链路。

## 本地测试

这些测试不连接真实 Omni，只验证迁移代码的本地契约：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/server-python/devtools/webrtc_full_duplex_migration/tests -q
```

也可以做启动入口冒烟测试：

```bash
uv run --with websockets --with dashscope \
  python openaiglass-sdk/server-python/devtools/webrtc_full_duplex_migration/relay.py \
  --api-key test-key --http-port 19877 --ws-port 19876
```
