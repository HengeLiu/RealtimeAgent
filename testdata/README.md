# testdata 目录说明

当前目录用于存放 SDK 回放测试可复用的样例资产。

推荐分类如下：

- `audio/`：音频样例
- `image/`：图片样例
- `video/`：视频样例
- `sensor/`：传感器时间序列
- `map/`：地图或路线 mock 数据
- `text/`：文本、帧标签、模型 mock 输出
- `task_event/`：结构化任务事件样例
- `scenario/`：场景 manifest

当前阶段已经提供找物体最小闭环的样例：

1. `text/find_object_frames_water_cup.json`
2. `scenario/find_object_with_testdata.json`
3. `task_event/find_object_cancel_timeline.json`
4. `scenario/find_object_cancelled.json`
5. `scenario/find_object_missing_phone.json`
6. `scenario/find_object_video_link_start_failed.json`

后续新增场景时，优先复用已有资产，避免把所有输入都内联写入单个 scenario 文件。

推荐执行方式：

```bash
uv run python script/run_sdk_scenario.py --scenario testdata/scenario/find_object_with_testdata.json --pretty
uv run python script/run_sdk_scenario.py --scenario testdata/scenario/find_object_cancelled.json --pretty
uv run python script/run_sdk_scenario.py --scenario-dir testdata/scenario --pretty
uv run python script/run_sdk_preflight.py --report logs/sdk-preflight.json
```

说明：

1. 第一条命令用于验证找物体成功闭环。
2. 第二条命令用于验证取消路径、链路停止和回放断言。
3. 第三条命令用于批量执行全部场景。
4. 第四条命令用于执行联调前预检，并生成 JSON 报告。
