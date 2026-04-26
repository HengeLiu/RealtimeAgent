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
- `contracts/`：SDK 公共契约金样
- `compat/`：盲人业务兼容性回归套件

当前阶段已经提供找物体最小闭环的样例：

1. `text/find_object_frames_water_cup.json`
2. `scenario/find_object_with_testdata.json`
3. `task_event/find_object_cancel_timeline.json`
4. `scenario/find_object_cancelled.json`
5. `scenario/find_object_missing_phone.json`
6. `scenario/find_object_video_link_start_failed.json`
7. `sensor/find_object_heading.json`
8. `scenario/find_object_with_heading_sensor.json`

后续新增场景时，优先复用已有资产，避免把所有输入都内联写入单个 scenario 文件。

推荐执行方式：

```bash
uv run python scripts/run_sdk_scenario.py --validate-scenarios testdata/scenario --pretty
uv run python ../openaiglass-sdk/scripts/run_sdk_contract_tests.py --pretty
uv run python ../openaiglass-sdk/scripts/run_sdk_compatibility_tests.py --pretty
uv run python scripts/run_sdk_scenario.py --scenario testdata/scenario/find_object_with_testdata.json --pretty
uv run python scripts/run_sdk_scenario.py --scenario testdata/scenario/find_object_cancelled.json --pretty
uv run python scripts/run_sdk_scenario.py --scenario-dir testdata/scenario --pretty
uv run python scripts/run_sdk_preflight.py --report ../logs/sdk-preflight.json
```

说明：

1. 第一条命令用于先校验全部 manifest 和资产引用是否完整。
2. 第二条命令用于执行 SDK 公共契约测试。
3. 第三条命令用于执行盲人业务兼容性回归。
4. 第四条命令用于验证找物体成功闭环。
5. 第五条命令用于验证取消路径、链路停止和回放断言。
6. 第六条命令用于批量执行全部场景。
7. 第七条命令用于执行联调前预检，并生成 JSON 报告。

当前已覆盖的找物体场景类型：

1. 成功闭环
2. 取消路径
3. 缺少在线手机
4. 视频链路启动失败
5. 结合 heading 传感器的手机任务结果增强

当前建议的维护顺序：

1. 先新增 `text/`、`sensor/`、`task_event/` 等可复用资产。
2. 再新增 `scenario/*.json`，只组合资产，不重复内联大段输入。
3. 先运行 `--validate-scenarios`，确认场景字段和资产引用正确。
4. 再运行 `--scenario-dir`，确认真实回放行为符合 `expected` 断言。

当前回放链路说明：

1. 服务端业务任务通过通用 `DeviceGroupContext` 负责启动视频链路和调用 `start_phone_task()` / `stop_phone_task()`。
2. 手机业务任务通过通用事件名和结构化 `payload` 回传结果。
3. `ScenarioRunner` 会同时记录 `glass_commands`、`phone_commands` 和 `phone_results`，用于核对“系统控制命令”和“业务结果事件”是否已经分层。
