# dev-support 单元测试

本目录放 dev-support 端侧参考组件的单元测试和轻量组件测试。

| 目录 | 测试目标 |
| --- | --- |
| `browser/` | browser-glass 静态页面和浏览器端协议字段。 |
| `playback/` | Python playback 端点基础行为。 |
| `python_phone/` | Python phone 预览、YOLO、heartbeat 和 remote task reporter。 |
| `python_phone_mock/` | Python phone mock endpoint 和视觉任务边界。 |
| `python_playback_glass/` | Python playback glass smoke、协议 client、recorder 和静态边界。 |

回归命令：

```bash
uv run python -m pytest examples/dev-support/unit-tests -q
```
