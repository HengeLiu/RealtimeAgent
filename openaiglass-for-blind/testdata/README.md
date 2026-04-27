# testdata 目录说明

当前目录用于存放 SDK 设备级回放测试可复用的样例资产。

推荐分类如下：

- `audio/`：音频样例
- `image/`：图片样例
- `video/`：视频样例
- `sensor/`：传感器时间序列
- `map/`：地图或路线 mock 数据
- `text/`：文本或模型 mock 输出
- `contracts/`：SDK 公共契约金样

当前阶段历史样例资产包括：

1. `sensor/find_object_heading.json`

`glass-playback` 是设备级组件，配置文件不放在 `testdata` 下，统一放在 `host/glass-playback/config`。后续新增回放用例时，优先复用已有资产，避免把所有输入都内联写入单个设备配置文件。

推荐执行方式：

```bash
uv run python ../openaiglass-sdk/scripts/run_sdk_contract_tests.py --pretty
uv run python scripts/run_sdk_preflight.py --report ../logs/sdk-preflight.json
```

说明：

1. 第一条命令用于执行 SDK 公共契约测试。
2. 第二条命令用于执行联调前预检，并生成 JSON 报告。

设备级回放配置应覆盖的找物体用例类型：

1. 成功闭环
2. 取消路径
3. 缺少在线手机
4. 视频链路启动失败
5. 结合 heading 传感器的手机任务结果增强

当前建议的维护顺序：

1. 先新增 `audio/`、`video/`、`image/`、`sensor/` 等可复用资产。
2. 再在 `host/glass-playback/config` 新增设备配置，只组合资产，不重复内联大段输入。
3. 启动真实业务服务端和真实 iOS 手机端。
4. 通过 `openaiglass.glass.start --runtime playback --config <config>` 启动 `glass-playback`，根据执行器输出、服务端日志和手机端日志人工判断结果。

当前设备级回放链路说明：

1. 服务端业务任务通过通用 `DeviceGroupContext` 负责启动视频链路和调用 `start_phone_task()` / `stop_phone_task()`。
2. 手机业务任务通过通用事件名和结构化 `payload` 回传结果。
3. `glass-playback` 像真实眼镜一样注册、心跳、发送触发音频，并记录执行器输出。
