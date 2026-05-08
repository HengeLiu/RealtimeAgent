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

真实 iPhone + `glass-playback` 验收默认需要以下真实采集资产：

1. `audio/find_object_real_trigger.wav`：真实语音触发音频，16-bit PCM WAV，默认 16000 Hz、单声道。
2. `image/find_object_real_frame_001.jpg` 或同等真实场景帧：眼镜摄像头输入帧。

这些资产暂不提交到仓库；执行验收前由实测人员放入本目录。不要用 `phone-mock` 代替真实 iPhone。

`glass-playback` 是设备级组件，配置文件不放在 `testdata` 下，统一放在 `host/glass-playback/config`。后续新增回放用例时，优先复用已有资产，避免把所有输入都内联写入单个设备配置文件。

推荐执行方式：

```bash
uv run openaiglass.sdk.contract-tests --pretty
uv run openaiglass.sdk.preflight --app-root openaiglass-for-blind --report logs/sdk-preflight.json
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
4. 通过 `tests/glass_playback_acceptance.py` 或 `openaiglass.glass.start --runtime playback --config <config>` 启动 `glass-playback`，根据事件日志、执行器输出、服务端日志和手机端日志判断结果。

当前设备级回放链路说明：

1. 服务端业务任务通过通用 `DeviceGroupContext` 负责启动视频链路和调用 `start_phone_task()` / `stop_phone_task()`。
2. 手机业务任务通过通用事件名和结构化 `payload` 回传结果。
3. `glass-playback` 像真实眼镜一样注册、心跳、发送触发音频，并记录执行器输出。
