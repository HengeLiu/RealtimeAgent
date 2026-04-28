# iteration-v11：回放测试断言能力

对应 SDK 版本：sdk-v12

## 背景

旧回放工具能执行音频样例并保存 `result.json`，但功能团队仍需要人工判断回复是否正确、是否调用了期望工具、模型请求是否包含关键上下文。本轮先给音频样例批量回归增加声明式断言。

## 本轮改动

1. `audio_sample_batch_runner` 增加 `--expectations`。
2. 支持 `defaults` 和 `cases.<sample_name>` 两级配置。
3. 支持回复文本包含/不包含断言。
4. 支持能力调用轨迹断言。
5. 支持模型请求文本片段断言。
6. 单条结果输出 `assertions_ok`、`assertion_failures`、`expectations`。
7. 断言失败计入批量失败数。
8. 更新开发指南、SDK 支持情况说明和结构设计文档。

## 验收

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_audio_sample_batch_runner.py -q
```

覆盖点：

1. 正常样例可合并默认断言和单样例断言。
2. 断言失败会让样例失败，即使客户端返回码为 0。
3. `load_expectations` 能读取 defaults/cases。
4. 断言函数能检查回复、能力轨迹和模型请求。

## 后续边界

下一步应把断言扩展到 `glass-playback` 配置内，包括控制事件顺序、执行器命令、相机流事件、真实视频帧和 phone 侧任务事件。
