# OpenAI Glasses SDK 官方示例

本目录是第二期 SDK 产品化的唯一官方案例。它模拟一个外部开发者项目：服务端通过 Python SDK 装配运行时和业务能力，手机端与眼镜端保留各自平台工程，通过协议接入设备组。

推荐结合以下文档一起阅读：

1. [SDK开发者快速开始.md](</Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/sdk-design/SDK开发者快速开始.md>)
2. [SDK真机联调前检查与联调步骤.md](</Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/sdk-design/SDK真机联调前检查与联调步骤.md>)

## 目录说明

```text
example/
  server/                 服务端 Python 示例入口
  phone/                  手机端工程与启动脚本
  glass/                  眼镜端工程与启动脚本
  capabilities/           实际业务能力实现
  scenario/               可回放测试场景
```

配套复用测试资产位于：

```text
testdata/
  text/                   可复用帧文本样例
  scenario/               资产化场景 manifest
```

## 启动方式

1. 服务端：`python example/server/main.py`
2. 手机端：`bash example/phone/run.sh`
3. 眼镜端：`bash example/glass/run.sh`

当前阶段服务端入口已经走真实 SDK 主入口装配，手机端和眼镜端脚本仍先委托现有 `phone/ios` 与 `glass/src` 工程，后续再逐步迁移到 `example/phone` 和 `example/glass`。

## 推荐先做离线回放验证

在进入真机联调前，建议先执行：

```bash
uv run python script/run_sdk_scenario.py --scenario testdata/scenario/find_object_with_testdata.json --pretty
uv run python script/run_sdk_scenario.py --scenario testdata/scenario/find_object_cancelled.json --pretty
uv run python script/run_sdk_scenario.py --scenario-dir testdata/scenario --pretty
uv run python script/run_sdk_preflight.py --report logs/sdk-preflight.json
uv run python script/sync_sdk_live_config.py
uv run python script/run_sdk_live_check.py --report logs/sdk-live-check.json
```

这样可以先验证：

1. 正向闭环是否成立。
2. 取消路径与视频链路停止是否成立。
3. 当前 `expected` 断言是否通过。
4. 缺设备与链路异常等失败场景是否按预期返回结构化错误。
5. 服务端最小健康检查是否通过。
6. 服务端、手机端和眼镜端的设备编号、配对令牌、局域网地址是否一致。
