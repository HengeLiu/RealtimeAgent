# python-playback-glass

`python-playback-glass` 是 dev-support 下的眼镜回放端侧。它通过真实 `/ws/control` 和 `/ws/stream` 与 server 对话，对 server 来说只是一台普通设备。

## 运行 Case

```bash
uv run python -m realtime_agent_python_playback_glass run \
  --server-url http://127.0.0.1:8765 \
  --case examples/dev-support/devices/python-playback-glass/cases/smoke/who_are_you.yaml \
  --report runs/python-playback-glass/who_are_you/report.json
```

运行 suite：

```bash
uv run python -m realtime_agent_python_playback_glass run \
  --server-url http://127.0.0.1:8765 \
  --suite examples/dev-support/devices/python-playback-glass/suites/smoke.yaml \
  --report runs/python-playback-glass/smoke/report.json
```

## 生成 Case 草稿

先用 `browser-glass` 手动跑一次，再从 runs 产物生成 YAML：

```bash
uv run python -m realtime_agent_python_playback_glass record \
  --runs-root examples/device_app_demo/agent-server/runs \
  --user-id user-browser-glass-001 \
  --device-id dev-browser-glass-001 \
  --audio testdata/audio-sample/看一下我前面有什么.wav \
  --image sensor.rgb=testdata/image-sample/刚子看电脑.jpeg \
  --out examples/dev-support/devices/python-playback-glass/cases/draft/look_front.yaml
```

录制器只归纳稳定断言，不保存 `event_id`、`timestamp_ms`、`stream_id`、`asset_id` 等动态字段。
