# Stage 2 迭代记录汇总（v1-v100）

更新时间：2026-05-05

本文合并 `openaiglass-sdk/docs/stage2` 下 `iteration-v1.md` 到 `iteration-v100.md` 范围内实际存在的迭代记录，便于减少零散文档数量并保留历史事实。

- 合并文件数：91
- 缺失编号：v41, v56, v58, v59, v60, v61, v62, v63, v83
- 保留范围外文件：`iteration-v101.md` 及后续迭代记录仍按单文件维护。

## 目录

- [iteration-v1：SDK v1 迭代记录](#iteration-v1)
- [iteration-v2：SDK v2 迭代记录](#iteration-v2)
- [iteration-v3：SDK v4 迭代记录](#iteration-v3)
- [iteration-v4：SDK v5 迭代记录](#iteration-v4)
- [iteration-v5：SDK v6 迭代记录](#iteration-v5)
- [iteration-v6：SDK v7 迭代记录](#iteration-v6)
- [iteration-v7：SDK v8 迭代记录](#iteration-v7)
- [iteration-v8：账号级设备组织](#iteration-v8)
- [iteration-v9：最小 Skill Runtime](#iteration-v9)
- [iteration-v10：任务持久化生产化](#iteration-v10)
- [iteration-v11：回放测试断言能力](#iteration-v11)
- [iteration-v12：端侧 SDK 打包形态](#iteration-v12)
- [iteration-v13：真 iOS 手机视觉资源管理](#iteration-v13)
- [iteration-v14：统一播放仲裁和用户打断](#iteration-v14)
- [iteration-v15：账号治理和远程配置 Provider](#iteration-v15)
- [iteration-v16：SQLite 任务持久化](#iteration-v16)
- [iteration-v17：全双工实时语音第一版](#iteration-v17)
- [iteration-v18：语音会话模式启动配置](#iteration-v18)
- [iteration-v19：SDK v20 阻塞点收口](#iteration-v19)
- [iteration-v20：SDK v21 glass-playback 控制循环修复](#iteration-v20)
- [iteration-v21：SDK v22 日志观测增强](#iteration-v21)
- [iteration-v22：SDK v23 服务端配置收口](#iteration-v22)
- [iteration-v23：SDK v24 Agent 模型兼容性与可观测性](#iteration-v23)
- [iteration-v24：SDK v25 撤销模型硬编码黑名单](#iteration-v24)
- [iteration-v25：SDK v26 绑定等待诊断](#iteration-v25)
- [iteration-v26：SDK v27 服务端默认配置单一来源](#iteration-v26)
- [iteration-v27：SDK v28 Agent 运行热路径拆薄](#iteration-v27)
- [iteration-v28：SDK v29 真实眼镜实时语音打开兼容](#iteration-v28)
- [iteration-v29：SDK v30 顶层 Python 安装入口](#iteration-v29)
- [iteration-v30：SDK v31 服务端前台运行生命周期](#iteration-v30)
- [iteration-v31：SDK v32 服务端 Secret 环境合并](#iteration-v31)
- [iteration-v32：SDK v33 视觉拍照播报去重](#iteration-v32)
- [iteration-v33：SDK v34 语音结束自动照片](#iteration-v33)
- [iteration-v34：SDK v35 实时 ASR 热路径](#iteration-v34)
- [iteration-v35：SDK v36 glass-playback 安装包入口收敛](#iteration-v35)
- [iteration-v36：SDK v37 glass-playback 状态日志格式统一](#iteration-v36)
- [iteration-v37：SDK v38 glass-playback 下行语音直接播放](#iteration-v37)
- [iteration-v38：SDK v39 实时 ASR 延迟指标口径修正](#iteration-v38)
- [iteration-v39：SDK v40 实时 ASR 切换官方 Recognition 接口](#iteration-v39)
- [iteration-v40：SDK v41 实时 ASR 分段延迟诊断与 VAD 阈值](#iteration-v40)
- [iteration-v42：SDK v42 迭代记录](#iteration-v42)
- [iteration-v43：SDK v43 迭代记录](#iteration-v43)
- [iteration-v44：SDK v44 迭代记录](#iteration-v44)
- [iteration-v45：SDK v45 迭代记录](#iteration-v45)
- [iteration-v46：SDK v46 迭代记录](#iteration-v46)
- [iteration-v47：SDK v47 迭代记录](#iteration-v47)
- [iteration-v48：SDK v48 迭代记录](#iteration-v48)
- [iteration-v49：SDK v49 迭代记录](#iteration-v49)
- [iteration-v50：SDK v50 迭代记录](#iteration-v50)
- [iteration-v51：SDK 迭代记录：语音输入模式与下行音频日志口径](#iteration-v51)
- [iteration-v52：SDK 迭代记录：Omni 默认链路与说话期间预推音频](#iteration-v52)
- [iteration-v53：SDK 迭代记录：glass-playback 本机麦克风输入](#iteration-v53)
- [iteration-v54：SDK 迭代记录：Omni 语义实时连续对话接线](#iteration-v54)
- [iteration-v55：SDK 迭代记录：Omni semantic_vad 默认连续对话](#iteration-v55)
- [iteration-v57：SDK 迭代记录：ESP32 首次唤醒轻提示音](#iteration-v57)
- [iteration-v64：SDK 迭代记录：回退播放中自然插话试验](#iteration-v64)
- [iteration-v65：SDK 迭代记录：Agent 长期记忆自然语言更新删除增强](#iteration-v65)
- [iteration-v66：SDK 迭代记录：Agent 长期记忆维护语义收敛](#iteration-v66)
- [iteration-v67：SDK 迭代记录：长期记忆分类描述统一](#iteration-v67)
- [iteration-v68：SDK 迭代记录：主 Agent 主动记忆提示补强](#iteration-v68)
- [iteration-v69：SDK 迭代记录：工具调用前置播报](#iteration-v69)
- [iteration-v70：SDK 迭代记录：工具前置播报静态音频缓存](#iteration-v70)
- [iteration-v71：SDK 迭代记录：工具前置播报随机候选](#iteration-v71)
- [iteration-v72：SDK 迭代记录：ESP32 连续对话门控收紧](#iteration-v72)
- [iteration-v73：SDK 迭代记录：ESP32 播放任务创建可靠性](#iteration-v73)
- [iteration-v74：SDK 迭代记录：音频原生链路流式返回](#iteration-v74)
- [iteration-v75：SDK 迭代记录：Omni 音频直出支持工具调用](#iteration-v75)
- [iteration-v76：SDK 迭代记录：Omni Realtime 上行字节流透传](#iteration-v76)
- [iteration-v77：SDK 迭代记录：Omni 音频直出旁路 ASR 转写](#iteration-v77)
- [iteration-v78：SDK 迭代记录：Omni 最终回复播放流延迟注册](#iteration-v78)
- [iteration-v79：SDK 迭代记录：工具前置播报缓存指纹校验](#iteration-v79)
- [iteration-v80：SDK 迭代记录：工具前置播报改为首输出自动判定](#iteration-v80)
- [iteration-v81：SDK 迭代记录：工具前置播报音频来源可配置](#iteration-v81)
- [iteration-v82：sdk-v82 配置分层与 YAML 化](#iteration-v82)
- [iteration-v84：sdk-v84 外部 MCP、Task 调度与通知链路修复](#iteration-v84)
- [iteration-v85：sdk-v85 真实眼镜连续 VAD 自循环修复](#iteration-v85)
- [iteration-v86：sdk-v86 受限连续对话和唤醒词打断修复](#iteration-v86)
- [iteration-v87：sdk-v87 语音轮次意图裁决](#iteration-v87)
- [iteration-v88：sdk-v88 连续 VAD 空段收口修复](#iteration-v88)
- [iteration-v89：sdk-v89 Omni semantic_vad 主链路恢复](#iteration-v89)
- [iteration-v90：sdk-v90 Omni 音频完成事件收口修复](#iteration-v90)
- [iteration-v91：sdk-v91 Omni 事件排障与非阻塞关闭](#iteration-v91)
- [iteration-v92：sdk-v92 Omni Realtime 长连接连续对话](#iteration-v92)
- [iteration-v93：sdk-v93 模型工具 reason 参数收敛](#iteration-v93)
- [iteration-v94：sdk-v94 ESP32 WakeNet SR 任务栈稳定性修复](#iteration-v94)
- [iteration-v95：sdk-v95 模型自决视觉拍照链路](#iteration-v95)
- [iteration-v96：sdk-v96 Omni Realtime 事件日志收敛](#iteration-v96)
- [iteration-v97：sdk-v97 语音模型服务边界抽象](#iteration-v97)
- [iteration-v98：sdk-v98 Omni/Text Server 适配器落地](#iteration-v98)
- [iteration-v99：sdk-v99 语音运行时代码物理拆分](#iteration-v99)
- [iteration-v100：sdk-v100 共享状态与音频工具拆分](#iteration-v100)

---

<a id="iteration-v1"></a>
## iteration-v1：SDK v1 迭代记录

来源：`iteration-v1.md`


本文记录 SDK 团队根据业务能力开发反馈进行的第一轮优化。

### 1. 输入反馈

业务团队在 `openaiglass-for-blind/docs/stage1/develop/架构阻塞点说明与改进建议.md` 中反馈：

1. iOS 通用运行时已有 `PhoneTaskCapabilityRuntime` 和 `PhoneCapabilityBootstrap`，但只能注册单个 runtime。
2. 多个 iOS 业务插件同时加入 App target 时，后注册的能力会覆盖先注册的能力。
3. 业务侧如果自行写组合 Runtime，会把 SDK 应负责的多能力分发逻辑扩散到业务工程。
4. iOS 插件接入 target 的方式需要在文档中明确。

### 2. 本轮 SDK 改动

#### 2.1 iOS 多能力注册表

新增 `PhoneTaskCapabilityRegistry`：

```swift
PhoneTaskCapabilityRegistry.register(taskType: "demo_phone_task") {
    DemoPhoneCapabilityRuntime()
}
```

它按 `taskType` 保存业务能力运行时工厂。`CameraStreamStore` 仍只持有一个 `PhoneTaskCapabilityRuntime`，但默认工厂会优先创建组合运行时，由组合运行时完成多能力分发。

#### 2.2 组合运行时

新增 `RegisteredPhoneTaskCapabilityRuntime`，负责：

1. `startTask` 时按 `taskType` 创建业务运行时。
2. 记录 `taskID -> runtime`，确保 `stopTask` 回到同一个实例。
3. 将视频帧投递给当前活跃任务对应的业务运行时。
4. 未知 `taskType` 使用 `NoopPhoneTaskCapabilityRuntime` 兜底。

#### 2.3 旧接口兼容

保留 `PhoneCapabilityRuntimeFactory.register { ... }`，但它只作为旧式单能力入口。新业务插件必须使用 `PhoneTaskCapabilityRegistry.register(taskType:runtimeBuilder:)`，否则无法稳定表达多个能力的分发关系。

### 3. 本轮不进入 SDK 的内容

Swift Package、XCFramework 和独立插件包发布形态暂不在本轮实现。

原因：

1. 当前 iOS SDK 仍以可直接打开的 Xcode 工程交付。
2. 业务团队下一轮迭代只需要稳定把多个 Swift 插件加入同一个 App target。
3. 包发布会引入版本号、资源拷贝、target 依赖和二进制兼容问题，应在 SDK 形态进一步稳定后单独处理。

本轮文档给出的临时约定是：业务 Swift 插件继续放在 `openaiglass-for-blind/capabilities/<capability>/phone/ios/`，由手机宿主 App target 显式加入 Compile Sources，并在启动入口集中调用各插件 `install()`。

### 4. 文档同步

已同步更新：

1. `openaiglass-for-blind/SDK安装与能力开发指南.md`
2. `openaiglass-sdk/phone-ios/README.md`
3. `openaiglass-sdk/docs/structure-design/手机SDK运行时设计.md`

### 5. 验证范围

本轮新增 iOS 测试覆盖：

1. 多个 `taskType` 同时注册后不会互相覆盖。
2. 停止某个 `taskID` 时只停止对应业务运行时。
3. 视频帧只投递给当前活跃手机任务。

真机联调仍需业务团队在下一轮把插件注册方式切换到 `PhoneTaskCapabilityRegistry` 后验证。

---

<a id="iteration-v2"></a>
## iteration-v2：SDK v2 迭代记录

来源：`iteration-v2.md`


本文记录 SDK 团队根据业务能力开发反馈进行的第二轮优化。业务侧版本记录更新为 `sdk-v3`，原因是第一轮文档名沿用 `SDK v1`，而对业务团队可见的能力版本已经进入第三个可用迭代。

### 1. 输入反馈

业务团队在 `openaiglass-for-blind/docs/stage1/develop/架构阻塞点说明与改进建议.md` 中反馈：

1. 业务 `BaseTool.run(context, input_data)` 拿到的 `DeviceGroupContext` 缺少统一 MCP 调用入口。
2. 导航准备能力如果要调用地图 MCP，只能绕过 SDK 直接 import adapter，或自行拼装 `McpRegistry / McpGateway / AgentToolContext`。
3. 完整 SDK 预检的 `sdk_boundary` 命中了 iOS SDK 测试夹具中的 `find_object_phone_task`，导致业务侧无法在不越界修改 SDK 的情况下修复预检失败。

### 2. 本轮 SDK 改动

#### 2.1 `DeviceGroupContext.mcp(...)`

新增业务可见 MCP 调用入口：

```python
route = context.mcp(
    "amap.route_plan",
    {
        "origin": input_data["origin"],
        "destination": input_data["destination"],
        "strategy": input_data.get("strategy", "walking"),
    },
)
```

该入口复用 SDK 内部 `McpGateway`，并把 agent-core 的能力结果转换成业务侧统一 `CapabilityResult`。业务调用失败时返回结构化失败结果，包含 `method_name`、输入摘要和统一错误码。

#### 2.2 MCP 网关绑定

`OpenAIGlassesSDK` 现在维护统一 `McpRegistry / McpGateway`，`register_mcp_adapter(...)` 会同步注册到该网关。`build_agent_facade_from_sdk(...)` 和真实 `ControlRuntime` 会把同一个 MCP 网关绑定到 `DeviceGroupRuntime`，保证：

1. 模型可见 MCP Tool 与业务 `context.mcp(...)` 走同一套注册表。
2. 离线回放、SDK Tool 调用和真实服务端运行时都能使用同一 MCP 调用入口。
3. MCP 调用轨迹可通过 `DeviceGroupRuntime.list_mcp_traces()` 观察；真实服务端中还会同步写入 agent session trace。

#### 2.3 iOS SDK 测试夹具通用化

将 `openaiglass-sdk/phone-ios/GlassesVideoReceiverTests` 中的历史业务 task type 改为 `demo_phone_task`。SDK 自测仍然覆盖多能力注册、按 `taskType` 分发和当前活跃任务帧路由，但不再携带 `find_object` 业务关键词。

### 3. 本轮不进入 SDK 的内容

本轮没有在 SDK 内置真实 AMap adapter。

原因：

1. 地图供应商、鉴权、限流和路线策略属于外部能力适配，不应内建到通用 SDK 根运行时。
2. SDK 已提供 `BaseMcpAdapter`、`register_mcp_adapter(...)` 和 `context.mcp(...)`，业务或宿主项目可以把具体 adapter 作为插件注册。
3. 导航业务的路线解释、缺槽追问和产品策略仍应放在业务能力层，而不是 SDK 系统层。

### 4. 文档同步

已同步更新：

1. `openaiglass-for-blind/SDK安装与能力开发指南.md`
2. `openaiglass-for-blind/sdk-version`
3. `openaiglass-sdk/docs/structure-design/SDK公共契约设计.md`
4. `openaiglass-sdk/docs/sdk-design/SDK开发者快速开始.md`

### 5. 验证范围

本轮新增 Python 单元测试覆盖：

1. mock MCP adapter 通过 `sdk.register_mcp_adapter(...)` 注册。
2. 业务上下文通过 `DeviceGroupContext.mcp(...)` 调用 MCP 方法。
3. 调用结果以业务侧 `CapabilityResult` 返回。
4. MCP 调用轨迹写入 `DeviceGroupRuntime.list_mcp_traces()`。

本轮预检重点：

1. `sdk_boundary` 不再命中 iOS SDK 测试夹具中的 `find_object`。
2. 完整预检不需要业务团队使用 `--skip-boundary`。

---

<a id="iteration-v3"></a>
## iteration-v3：SDK v4 迭代记录

来源：`iteration-v3.md`


本文记录 SDK 团队根据 `SDK对功能开发支持情况的说明.md` 继续进行的第三轮优化。业务侧版本记录更新为 `sdk-v4`。

### 1. 输入反馈

第 8 项后台任务管理和第 10 项大模型创建手机与眼镜直连后台任务已经能完成最小演示，但还缺少产品化任务语义：

1. `phone_video_link_task` 只能触发 `sensor.camera.stream.start/stop`，不能接收 peer-link 或 camera stream 事件。
2. 手机端准备失败、链路断开、视频开始、视频停止等状态不能回流到任务运行态。
3. 错误手机上报任务事件时缺少统一校验和结构化错误。
4. 业务团队难以通过任务查询判断视频链路当前处于准备、已就绪、推流中、失败或结束。

本轮明确暂缓实时语音打断、全双工语音、真实公网/NAT 穿透、iOS/ESP32 包化和多视觉任务并发调度。

### 2. 本轮 SDK 改动

#### 2.1 系统任务事件派发

`HybridTaskGateway.dispatch_event(...)` 现在可同时路由 SDK 业务任务和 SDK 系统任务。`phone_video_link_task` 不再只能创建和取消，也可以通过统一事件入口接收端侧上报。

#### 2.2 `phone_video_link_task` 生命周期

系统任务上下文新增标准阶段：

1. `peer_link_preparing`
2. `peer_link_ready`
3. `streaming`
4. `stopping`
5. `completed`
6. `cancelled`
7. `failed`
8. `timeout`

任务上下文会保留 `stream_id`、`phone_device_id`、`target_ws_uri`、`link_mode`、`frame_interval_ms`、最近 peer-link 事件、最近 camera stream 事件和最近结构化错误。

#### 2.3 标准端侧事件

SDK 固化以下最小事件名：

| 事件名 | 任务变化 |
| --- | --- |
| `peer_link.ready` | 阶段进入 `peer_link_ready`。 |
| `camera.stream.started` | 阶段进入 `streaming`。 |
| `peer_link.failed` | 状态进入 `failed`，记录 `peer_link_failed`。 |
| `peer_link.broken` | 状态进入 `failed`，记录 `peer_link_broken`。 |
| `peer_link.closed` | 状态进入 `completed`。 |
| `camera.stream.stopped` | 活动任务进入 `completed`；已取消任务保持 `cancelled`。 |

`cancel_task()` 继续保持兼容，取消活动视频任务时发布 `task.cancelled`，由 `ControlRuntime` 下发 `sensor.camera.stream.stop`。重复取消终态任务保持幂等返回。

#### 2.4 ControlRuntime 集成

`ControlRuntime.report_task_event(...)` 现在可用于 `phone_video_link_task`。服务端会先查询任务绑定的 `phone_device_id`，上报手机不匹配时返回结构化 `INVALID_MESSAGE`，避免非绑定手机污染任务状态。

`ControlRuntime` 也会在任务完成或失败后清理活动视频任务映射，避免调试停止接口长期指向已结束任务。

### 3. 本轮不进入 SDK 的内容

1. 实时语音打断、电话式实时对话和用户播放期插话。
2. 真实公网/NAT 穿透、TURN/STUN、跨网络重试和链路健康检查。
3. AMap 真实 adapter、手机端 YOLO 执行框架和多视觉任务并发调度。
4. iOS SDK / ESP32 SDK 的正式包化与发布兼容策略。

这些内容仍属于后续 SDK 系统层迭代，不应由业务能力目录自行补齐。

### 4. 文档同步

已同步更新：

1. `SDK对功能开发支持情况的说明.md`
2. `openaiglass-for-blind/SDK安装与能力开发指南.md`
3. `openaiglass-for-blind/sdk-version`

### 5. 验证范围

新增和调整测试覆盖：

1. 创建 `phone_video_link_task` 后检查初始 `state/context/event`。
2. 派发 `peer_link.ready`、`camera.stream.started` 后检查任务进入 `running/streaming`。
3. 派发 `peer_link.failed` 后检查任务进入 `failed`，并保留结构化错误。
4. 取消任务后检查 `task.cancelled`，重复取消保持幂等。
5. 通过 `/api/tasks/report-event` 验证手机上报事件可推进任务阶段。
6. 验证错误手机上报事件会被服务端拒绝。

---

<a id="iteration-v4"></a>
## iteration-v4：SDK v5 迭代记录

来源：`iteration-v4.md`


本文记录 SDK 团队在 `sdk-v4` 视频直连任务语义之后继续补齐的运行时能力。业务侧版本记录更新为 `sdk-v5`。

### 1. 输入反馈

`SDK对功能开发支持情况的说明.md` 中仍有几类非体验类 SDK 缺口：

1. SDK 业务 Task 虽然能创建、查询、取消和接收事件，但缺少可持久化的事件日志和恢复入口。
2. 后台任务缺少统一超时治理，业务团队难以用 SDK 层能力覆盖长任务等待超时。
3. 手机侧视频帧更接近单任务消费模型，缺少多视觉任务共享同一路帧的通用分发能力。

本轮仍不处理电话式实时语音对话、用户打断、全双工语音、公网/NAT 穿透、真实地图策略和端侧 SDK 包化。

### 2. 本轮 SDK 改动

#### 2.1 SDK 业务 Task 事件日志

`TaskRuntimeSnapshot` 新增：

1. `created_at_ms`
2. `updated_at_ms`
3. `started_at_ms`
4. `completed_at_ms`
5. `timeout_ms`
6. `deadline_at_ms`
7. `events`

SDK 运行时会记录 `task.created`、`task.started`、外部事件、`task.completed`、`task.failed`、`task.cancelled`、`task.timeout` 和 `task.restored`。

#### 2.2 超时治理

创建 SDK 业务 Task 时可在 `input_data` 中传入 `timeout_ms`。当查询、取消或派发事件时发现任务已经超过 `deadline_at_ms`，运行时会把任务推进到 `timeout`，写入结构化错误并追加 `task.timeout` 事件。

#### 2.3 快照导出与恢复

`TaskRuntimeManager` 新增：

1. `export_snapshots()`
2. `restore_snapshots(...)`
3. `save_snapshots(path)`
4. `load_snapshots(path)`

这组接口先提供 JSON 兼容快照，宿主可以保存到文件；后续切数据库或对象存储时不需要业务 Task 改接口。

#### 2.4 手机侧多任务帧分发

`PhoneRuntime.process_frame(...)` 支持把同一帧按 `stream_id` 和 `task_types` 分发给多个活跃手机任务。`PhoneTaskSnapshot` 新增 `frames_processed`，用于回放测试和真机联调观察。

终态任务不会再收到后续帧，避免已停止或已失败的任务继续消耗模型资源。

### 3. 本轮不进入 SDK 的内容

1. 多模型资源加载、YOLO 真实执行框架和性能保护。
2. 手机视觉任务优先级抢占、帧率降级和功耗治理。
3. 跨进程或跨服务的数据库级任务恢复。
4. 实时语音打断、全双工语音和公网/NAT 穿透。

这些仍属于后续 SDK 系统层迭代。

### 4. 文档同步

已同步更新：

1. `SDK对功能开发支持情况的说明.md`
2. `openaiglass-for-blind/SDK安装与能力开发指南.md`
3. `openaiglass-for-blind/sdk-version`

### 5. 验证范围

新增和调整测试覆盖：

1. SDK 业务 Task 事件日志与查询触发超时。
2. SDK 业务 Task 快照导出、恢复和继续派发事件。
3. SDK 业务 Task 快照保存到 JSON 文件并从文件恢复。
4. 手机侧同一路视频帧分发给多个活跃任务。
5. 手机侧按任务类型过滤分发，并跳过已停止任务。

---

<a id="iteration-v5"></a>
## iteration-v5：SDK v6 迭代记录

来源：`iteration-v5.md`


本文记录 SDK 团队在 `sdk-v5` 之后，按欠缺能力优先级推进的第一轮能力补全。业务侧版本记录更新为 `sdk-v6`。

### 1. 输入反馈

本轮优先处理“手机视觉执行框架的资源管理”。功能团队后续会迁移更多手机视觉能力，如果 SDK 只负责把视频帧广播给任务，而不提供帧率限制、过载记录和可观察快照，业务层很容易重复实现帧队列、限流和资源保护。

本轮只做手机视觉资源管理的最小 SDK 能力，不处理以下事项：

1. 普通文本流式和 TTS 首包延迟。
2. 通知、抢播和用户打断策略。
3. 多设备组织和账号级管理。
4. Skill Runtime。
5. 任务持久化生产化。
6. 回放测试断言能力。
7. iOS 和 ESP32 SDK 打包形态。
8. 真实手机端模型资源池、YOLO 执行框架和多模型内存治理。

### 2. 本轮 SDK 改动

#### 2.1 手机视觉任务资源策略

`PhoneRuntime` 新增 `VisionTaskPolicy`，用于从手机任务参数中读取视觉资源策略：

1. `min_frame_interval_ms`
2. `max_frames`
3. `priority`
4. `emit_overload_events`

策略优先从 `params["vision_policy"]` 读取。为了兼容早期写法，也会读取顶层 `frame_interval_ms`、`min_frame_interval_ms`、`max_frames` 和 `priority`。

#### 2.2 资源策略调度

`PhoneRuntime.process_frame(...)` 和 `PhoneRuntime.process_task_frame(...)` 现在都会在调用业务任务 `on_frame(...)` 前执行资源策略检查。

当前支持两类过载原因：

1. `frame_rate_limited`：当前帧距离上一帧实际处理时间不足。
2. `max_frames_reached`：当前任务已达到最大处理帧数。

被 SDK 丢弃的帧不会进入业务任务，避免业务层自行处理资源限制。

#### 2.3 手机任务快照增强

`PhoneTaskSnapshot` 新增：

1. `frames_dropped`
2. `resource_events`
3. `vision_policy`

其中 `resource_events` 会记录 `vision.task.overloaded`，供回放测试、`phone-mock` 和联调日志定位资源问题。

### 3. 开发者使用方式

功能开发者在启动手机任务时，可以通过任务参数声明策略：

```python
sdk.phone_runtime.start_task(
    task_type="demo_phone_task",
    params={
        "stream_id": "stream_cam_001",
        "vision_policy": {
            "min_frame_interval_ms": 1000,
            "max_frames": 30,
            "priority": 10,
            "emit_overload_events": True,
        },
    },
)
```

业务 `BasePhoneTask.on_frame(...)` 只会收到 SDK 允许处理的帧。被限流或丢弃的帧进入 `PhoneTaskSnapshot.resource_events`，不进入业务结果列表。

### 4. 本轮不进入 SDK 的内容

1. iOS 真机运行时还没有内置统一资源策略，真实 Swift 插件暂时需要读取同名 `vision_policy` 参数并保持一致语义。
2. `priority` 当前只进入策略和快照，后续再用于多任务抢占和降级。
3. 当前没有模型加载池、GPU/CPU 资源池、功耗治理和异步背压。
4. 当前没有把手机过载事件自动回流成服务端 TaskEvent；这会在通知和任务事件治理中继续补齐。

### 5. 文档同步

已同步更新：

1. `openaiglass-sdk/docs/structure-design/手机视觉资源管理设计.md`
2. `openaiglass-for-blind/SDK安装与能力开发指南.md`
3. `openaiglass-for-blind/sdk-version`

### 6. 验证范围

新增测试覆盖：

1. 手机视觉任务按 `min_frame_interval_ms` 限制处理频率。
2. 达到 `max_frames` 后，后续帧不会进入业务任务。
3. 被 SDK 丢弃的帧会记录 `vision.task.overloaded`。
4. 快照中可以观察 `frames_processed`、`frames_dropped`、`resource_events` 和 `vision_policy`。

验证命令：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q
python -m compileall -q openaiglass-sdk/server-python/openaiglasses/phone
```

---

<a id="iteration-v6"></a>
## iteration-v6：SDK v7 迭代记录

来源：`iteration-v6.md`


本文记录 SDK 团队在 `sdk-v6` 之后，按欠缺能力优先级推进的第二轮能力补全。业务侧版本记录更新为 `sdk-v7`。

### 1. 输入反馈

本轮处理“普通文本流式和 TTS 首包延迟”。此前代码虽然调用了 `Runner.run_streamed(...)`，但普通文本回复的 `response.output_text.delta` 没有继续透传给 `reply_text_delta_callback`。因此普通问答仍容易等完整回复出来后才进入 TTS。

本轮只处理普通文本 delta 到 TTS 调度层的透传和首包观测，不处理：

1. 全双工实时语音。
2. 用户语音打断。
3. 通知抢播和播放仲裁。
4. TTS 服务商底层 WebSocket 性能优化。

### 2. 本轮 SDK 改动

#### 2.1 普通文本 delta 透传

`OpenAIAgentLoopRunner._run_streamed_turn(...)` 现在会处理 Agents SDK 的 `raw_response_event`：

```text
event.type = raw_response_event
event.data.type = response.output_text.delta
event.data.delta = 文本增量
```

提取到的普通文本增量会：

1. 追加到当前轮 `reply_text_parts`。
2. 立即调用 `reply_text_delta_callback(text_delta)`。
3. 最终优先由流式增量拼接出 `AgentTurnResult.reply_text`。

这样普通问答和图片解读主链路都能走同一个上层流式 TTS 回调。

#### 2.2 首包延迟观测

`PlaybackStreamContext` 新增：

1. `first_text_delta_at_ms`
2. `first_audio_chunk_at_ms`
3. `first_play_request_at_ms`

`VoiceRuntime.build_runtime_snapshot()` 新增：

1. `reply_first_text_delta_at_ms`
2. `reply_first_audio_chunk_at_ms`
3. `reply_first_play_request_at_ms`
4. `reply_text_to_first_audio_ms`
5. `reply_audio_to_play_request_ms`

这些字段用于判断普通回复是否真的进入流式 TTS，以及首音频延迟发生在 Agent 文本、TTS 合成还是播放请求阶段。

### 3. 本轮不进入 SDK 的内容

1. 如果当前环境回退到 `BufferedStreamingTtsSession`，TTS 仍会在 `finish()` 后生成音频；此时快照中的 `reply_text_to_first_audio_ms` 会暴露延迟。
2. 本轮没有修改眼镜端播放协议。
3. 本轮没有修改通知和打断策略，播放期间是否允许用户语音打断仍由后续专项处理。

### 4. 文档同步

已同步更新：

1. `openaiglass-sdk/docs/structure-design/普通文本流式与TTS首包延迟优化设计.md`
2. `openaiglass-for-blind/SDK安装与能力开发指南.md`
3. `openaiglass-for-blind/sdk-version`

### 5. 验证范围

新增测试覆盖：

1. 普通 `raw_response_event` 文本增量会进入 `reply_text_delta_callback`。
2. 最终回复文本优先使用流式文本增量拼接结果。
3. `VoiceRuntime` 运行态快照会记录首文本、首音频、首播放请求和延迟字段。

验证命令：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_agent_core.py openaiglass-sdk/tests/unit/test_voice_runtime.py -q
python -m compileall -q openaiglass-sdk/server-python/agent_core/runtime openaiglass-sdk/server-python/runtime
```

---

<a id="iteration-v7"></a>
## iteration-v7：SDK v8 迭代记录

来源：`iteration-v7.md`


本文记录 SDK 团队在 `sdk-v7` 之后，按欠缺能力优先级推进的第三轮能力补全。业务侧版本记录更新为 `sdk-v8`。

### 1. 输入反馈

本轮处理“通知、抢播和用户打断策略”。此前 SDK 已有 `NotificationCoordinator`，但策略主要依赖 `allow_interrupt` 布尔值，运行态也缺少“为什么这条通知被播报、排队、抢播或去重”的解释。

本轮先补通知仲裁最小闭环，不处理完整实时语音用户打断。

### 2. 本轮 SDK 改动

#### 2.1 显式通知策略

`NotificationRequest` 新增：

1. `interrupt_policy`
2. `resume_policy`

兼容旧字段：

1. `allow_interrupt=true` 且未设置 `interrupt_policy` 时，默认 `higher_priority`。
2. `allow_interrupt=false` 且未设置 `interrupt_policy` 时，默认 `never`。

当前支持策略：

1. `never`
2. `higher_priority`
3. `critical_only`
4. `always`

#### 2.2 仲裁结果和决策快照

`NotificationSubmitResult` 新增：

1. `reason`
2. `active_request_id`
3. `queued_position`

新增 `NotificationDecision`，记录直发、排队、抢播和去重原因。

`NotificationCoordinator.build_snapshot()` 输出：

1. `active_requests`
2. `pending_requests`
3. `recent_decisions`

#### 2.3 VoiceRuntime 运行态聚合

`VoiceRuntime.build_runtime_snapshot()` 新增：

1. `active_notification`
2. `pending_notifications`
3. `recent_notification_decisions`

抢播旧通知时，`actuator.audio.interrupt` 的 payload 会包含 `resume_policy`，为后续恢复播放策略预留接口。

### 3. 本轮不进入 SDK 的内容

1. 不实现完整实时语音用户打断。
2. 不把普通 Agent 回复、任务通知和视觉告警全部收敛到统一播放仲裁器。
3. 不实现被中断内容的恢复播放，目前默认 `drop_interrupted`。
4. 不修改眼镜端音频播放协议。

### 4. 文档同步

已同步更新：

1. `openaiglass-sdk/docs/structure-design/通知抢播与用户打断策略设计.md`
2. `openaiglass-for-blind/SDK安装与能力开发指南.md`
3. `openaiglass-for-blind/sdk-version`
4. `SDK对功能开发支持情况的说明.md`

### 5. 验证范围

新增和调整测试覆盖：

1. 通知去重结果会带原因。
2. `critical_only` 策略下，high 通知只排队，critical 通知抢播。
3. 通知协调器快照能导出活动通知、待播队列和最近决策。
4. `VoiceRuntime` 运行态快照能聚合通知仲裁状态。
5. 旧通知被抢播时继续下发 `actuator.audio.interrupt`。

验证命令：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_task_event_runtime.py -q
python -m compileall -q openaiglass-sdk/server-python/runtime
```

---

<a id="iteration-v8"></a>
## iteration-v8：账号级设备组织

来源：`iteration-v8.md`


对应 SDK 版本：sdk-v9

### 背景

功能开发计划中后续会出现多副眼镜、多台手机和多用户并存场景。旧 SDK 只维护设备组和一对一绑定，能够支撑单账号联调，但缺少账号级索引和跨账号隔离。

### 本轮改动

1. 新增 `DeviceAccount` 公共模型。
2. `DeviceGroupRuntime.register_device()` 支持 `account_id/user_id`。
3. `DeviceGroupRuntime.bind_devices()` 增加跨账号绑定拒绝。
4. `DeviceGroupRuntime.build_snapshot()` 增加 `accounts` 快照。
5. `ControlRuntime` 从 `device.register` 读取账号字段，写入连接快照、设备 metadata 和注册响应。
6. 自动绑定兜底策略增加账号一致性判断。
7. 更新功能开发指南、SDK 支持情况说明和 `sdk-version`。

### 验收

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/integration/test_control_register_flow.py -q
```

覆盖点：

1. 同账号眼镜和手机绑定后出现在同一账号快照。
2. 跨账号绑定被拒绝。
3. 控制面注册响应和运行态快照包含账号字段。
4. 旧的无账号单眼镜单手机自动绑定仍保持兼容。

### 后续边界

sdk-v9 不是完整权限系统。授权、审计、组织管理后台、远程配置中心和多实例设备目录仍需后续专项处理。

---

<a id="iteration-v9"></a>
## iteration-v9：最小 Skill Runtime

来源：`iteration-v9.md`


对应 SDK 版本：sdk-v10

### 背景

前几轮 SDK 已经提供 Tool、Task、MCP、设备组、通知和多设备组织能力。复合业务仍缺少一个“告诉模型如何组合这些能力”的正式扩展面，因此本轮补齐最小 Skill Runtime。

### 本轮改动

1. `SkillManifest` 增加 `allowed_tools` 和 `allowed_mcp_methods`。
2. 新增 `SkillRuntime`，维护 Skill 注册、会话 active Skill、prompt 片段和运行态快照。
3. 新增内置 `read_skill` Tool，读取 Skill 正文后激活当前会话 Skill。
4. `ToolRegistry` 支持 Skill Runtime 注入和按会话过滤模型可见工具。
5. `ToolGateway` 在执行前校验当前会话 Skill 工具白名单。
6. `OpenAIAgentLoopRunner` 注入 Skill 摘要或 active Skill 正文，并在 `model_request` 中记录 active Skill 和工具白名单。
7. `OpenAIGlassesSDK` 增加 `register_skill` 和 `register_skill_manifest`。
8. 控制运行态快照增加 `skills` 节点。
9. 更新开发指南、支持情况说明和 Skill Runtime 设计文档。

### 验收

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_agent_core.py openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q
```

覆盖点：

1. `read_skill` 可读取 Skill 并激活当前会话。
2. active Skill 正文进入 system prompt。
3. active Skill 工具白名单会过滤模型可见工具。
4. `ToolGateway` 会拒绝白名单外工具调用。
5. SDK 注册的 Skill 可注入基于 SDK 构建的 `AgentFacade`。

### 后续边界

本轮不是远程 Skill 平台。目录扫描、审批、风险等级、远程注册、复杂会话恢复和多 Skill 冲突策略后续再做。

---

<a id="iteration-v10"></a>
## iteration-v10：任务持久化生产化

来源：`iteration-v10.md`


对应 SDK 版本：sdk-v11

### 背景

旧版 SDK 任务运行时已经能导出、保存和恢复 JSON 快照，但保存动作需要宿主主动调用，也缺少原子写入、事件幂等和终态任务清理。功能开发进入长任务和回放阶段后，需要更可靠的单机持久化能力。

### 本轮改动

1. 新增 `FileTaskPersistenceStore`。
2. `TaskRuntimeManager` 支持可选持久化存储。
3. 新增 `enable_persistence(path, restore=True)`。
4. 创建、取消、事件派发、恢复和清理后自动保存。
5. 文件保存使用临时文件加原子替换。
6. `dispatch_event` 支持 `event_id`，并识别 payload 中的 `event_id/idempotency_key`。
7. 新增 `prune_tasks(retain_terminal_ms=...)`。
8. 更新开发指南、支持情况说明和结构设计文档。

### 验收

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q
```

覆盖点：

1. 自动持久化文件包含版本、保存时间和任务列表。
2. 相同 `event_id` 的外部事件只处理一次。
3. 终态任务清理会同步更新持久化文件。

### 后续边界

sdk-v11 是单机生产化，不是数据库级分布式任务平台。多实例抢占、分布式锁、数据库事务和事件游标后续专项处理。

---

<a id="iteration-v11"></a>
## iteration-v11：回放测试断言能力

来源：`iteration-v11.md`


对应 SDK 版本：sdk-v12

### 背景

旧回放工具能执行音频样例并保存 `result.json`，但功能团队仍需要人工判断回复是否正确、是否调用了期望工具、模型请求是否包含关键上下文。本轮先给音频样例批量回归增加声明式断言。

### 本轮改动

1. `audio_sample_batch_runner` 增加 `--expectations`。
2. 支持 `defaults` 和 `cases.<sample_name>` 两级配置。
3. 支持回复文本包含/不包含断言。
4. 支持能力调用轨迹断言。
5. 支持模型请求文本片段断言。
6. 单条结果输出 `assertions_ok`、`assertion_failures`、`expectations`。
7. 断言失败计入批量失败数。
8. 更新开发指南、SDK 支持情况说明和结构设计文档。

### 验收

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_audio_sample_batch_runner.py -q
```

覆盖点：

1. 正常样例可合并默认断言和单样例断言。
2. 断言失败会让样例失败，即使客户端返回码为 0。
3. `load_expectations` 能读取 defaults/cases。
4. 断言函数能检查回复、能力轨迹和模型请求。

### 后续边界

下一步应把断言扩展到 `glass-playback` 配置内，包括控制事件顺序、执行器命令、相机流事件、真实视频帧和 phone 侧任务事件。

---

<a id="iteration-v12"></a>
## iteration-v12：端侧 SDK 打包形态

来源：`iteration-v12.md`


### 本轮目标

补齐 iOS 和 ESP32 SDK 的源码包形态，使 SDK 开发者和功能开发者可以通过统一 package-check 判断三端 SDK 发布输入是否齐全。

### 主要改动

1. 为 iOS 运行时新增 `phone-ios/package-manifest.json`，声明包名、版本、包形态、最低 iOS/Swift 版本、Xcode 工程、运行时代码、测试代码、资源文件和公开能力。
2. 为 ESP32 运行时新增 `glass-esp32/component-manifest.json`，声明包名、版本、包形态、ESP-IDF 目标、最低 ESP-IDF 版本、工程文件、组件文件、托管依赖和公开能力。
3. 扩展 `openaiglass.sdk.package-check`，在 Python wheel 构建和导入检查之外，继续校验 iOS 与 ESP32 清单和文件完整性。
4. 增加单元测试覆盖清单完整性和缺字段错误。
5. 更新 SDK 使用指南，说明 `sdk-v13` 的端侧源码包边界和仍未覆盖的二进制发布能力。

### 当前边界

本轮不把 iOS 运行时改造成 Swift Package 或 XCFramework，也不把 ESP32 工程发布成 ESP-IDF component registry 组件。当前目标是提供稳定的源码集成清单和自动检查入口，避免业务团队复制 SDK 运行时代码或误用构建产物。

---

<a id="iteration-v13"></a>
## iteration-v13：真 iOS 手机视觉资源管理

来源：`iteration-v13.md`


### 本轮目标

补齐真 iOS 手机视觉执行框架的第一版资源管理能力，让 Swift 业务插件通过 `vision_policy` 声明资源需求，由 SDK 通用运行时统一处理帧率、最大帧数、独占模型资源、抢占、功耗降级和资源事件回流。

### 主要改动

1. 新增 iOS `VisionResourceCoordinator`，支持 `VisionTaskPolicy`、`VisionTaskLease`、`VisionResourceEvent` 和帧投递决策。
2. `RegisteredPhoneTaskCapabilityRuntime` 在启动任务时申请视觉资源租约，资源不足时拒绝任务，高优先级任务可抢占低优先级任务。
3. 视频帧进入业务插件前先经过资源协调器，支持 `min_frame_interval_ms`、`max_frames` 和过载事件。
4. `CameraStreamStore` 记录视觉资源事件，并在有 `phoneDeviceID` 时通过 `PhoneTaskEventReportAPI` 异步回流服务端任务事件。
5. iOS 包清单加入 `VisionResourceCoordinator.swift`，package-check 可校验新增运行时代码。

### 当前边界

1. 本轮不内置具体视觉模型，也不实现 YOLO、盲道、红绿灯或找物算法。
2. 功耗治理第一版读取任务参数中的 `power_mode`，尚未自动接入真实电量和热状态采样。
3. SQLite 任务持久化、完整播放仲裁、账号权限和远程配置中心仍是后续优先项。

### 验证结果

已通过：

```bash
xcodebuild test -project openaiglass-sdk/phone-ios/GlassesVideoReceiver.xcodeproj -scheme GlassesVideoReceiver -destination 'platform=iOS Simulator,name=iPhone 17'
```

第一次使用计划文档中的 `iPhone 16` 目的地执行失败，原因是本机没有该模拟器；改用本机可用的 `iPhone 17` 后测试通过。

---

<a id="iteration-v14"></a>
## iteration-v14：统一播放仲裁和用户打断

来源：`iteration-v14.md`


### 本轮目标

补齐服务端播放通道的统一仲裁能力，让普通 Agent 回复、任务通知、视觉告警和用户主动打断都进入 SDK 中央播放策略，不再由业务层或单一路径直接控制播放器。

### 主要改动

1. 新增 `runtime.playback_arbiter.PlaybackArbiter`，统一维护活动播放意图、待播队列和最近决策。
2. `VoiceRuntime` 创建播放流时会生成 `PlaybackIntent`，支持 `play_now`、`queue`、`interrupt` 和 `user_interrupt` 决策。
3. 高优先级视觉告警或任务通知可以按 `interrupt_policy` 抢占普通 Agent 回复，旧播放流会被标记为 `interrupted` 并下发 `actuator.audio.interrupt`。
4. 新增 `user.voice.interrupt` 控制消息入口，支持停止当前播报并按 `clear_queue` 清理待播队列。
5. 运行态快照新增 `active_playback_intent`、`pending_playback_intents`、`recent_playback_decisions`，用于解释播放、排队、抢播和用户打断原因。

### 当前边界

1. 本轮完成的是半双工用户主动打断，不是全双工实时语音。
2. `resume_policy` 第一版以 `drop_interrupted` 为主，尚未实现断点恢复或摘要补偿。
3. `NotificationCoordinator` 仍保留通知去重和通知级队列职责，播放层统一收敛到 `PlaybackArbiter`。
4. 账号权限、组织管理、远程配置中心和 SQLite 任务持久化仍是后续优先项。

### 验证结果

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_playback_arbiter.py openaiglass-sdk/tests/unit/test_voice_runtime.py openaiglass-sdk/tests/unit/test_task_event_runtime.py -q
python -m compileall -q openaiglass-sdk/server-python
```

---

<a id="iteration-v15"></a>
## iteration-v15：账号治理和远程配置 Provider

来源：`iteration-v15.md`


### 本轮目标

补齐账号权限、组织管理、审计事件和远程配置中心第一版，让 SDK 在已有账号级设备索引之外，具备可授权、可审计、可配置的基础设施。

### 主要改动

1. 新增 `AccountGovernanceRuntime`，统一承载组织树、角色绑定、权限策略、审计事件和配置 Provider。
2. 新增 `OrganizationNode`、`RoleBinding`、`PermissionPolicy`、`AuditEvent` 等账号治理模型。
3. 新增 `MemoryAuditSink`、`FileAuditSink`，支持内存和 JSONL 文件审计输出。
4. 新增 `MemoryConfigProvider`、`FileConfigProvider`，支持 global、account、group、device 四级配置读取。
5. `DeviceGroupRuntime` 接入治理运行时：注册和绑定会记录审计，跨账号绑定 deny 也会进入审计事件。
6. `DeviceGroupContext` 新增 `get_config(...)` 和 `require_permission(...)`，业务代码可以通过 SDK 入口读取策略配置和执行权限检查。

### 当前边界

1. 本轮不实现商业后台 UI、外部用户中心、SSO、OAuth 或云端配置服务。
2. 默认权限检查以显式 `authorize(...)` / `require_permission(...)` 为主，后续再逐步接入更多 Tool、Task 自动检查点。
3. 文件配置 Provider 是单机本地文件形态，不是多实例配置推送系统。
4. SQLite 任务持久化仍是下一项优先工作。

### 验证结果

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q
python -m compileall -q openaiglass-sdk/server-python
```

---

<a id="iteration-v16"></a>
## iteration-v16：SQLite 任务持久化

来源：`iteration-v16.md`


### 本轮目标

把 SDK 托管任务从 JSON 文件快照升级到 SQLite 轻量数据库形态，支持任务快照恢复、事件幂等和单机多进程任务租约。

### 主要改动

1. 新增 `SQLiteTaskPersistenceStore`，使用 Python 标准库 `sqlite3`，保持与文件存储一致的 `save/load` 契约。
2. SQLite schema 包含 `schema_migrations`、`tasks`、`task_events`、`task_leases` 四张表。
3. 文件型 SQLite 默认启用 WAL，任务快照和事件写入使用 `BEGIN IMMEDIATE` 事务。
4. `task_events` 使用 `(task_id, event_id)` 主键做事件幂等。
5. `TaskRuntimeManager` 新增 `enable_sqlite_persistence(...)`，支持从 SQLite 恢复任务。
6. `SQLiteTaskPersistenceStore.acquire_lease(...)` / `release_lease(...)` 提供单机多进程租约能力。

### 当前边界

1. SQLite 第一版只保证单机 SQLite 文件内的多进程协调，不保证跨机器强一致。
2. 当前 manager 通过快照保存契约接入 SQLite，后续可进一步细化为增量事件写入。
3. 多服务器部署仍需要外部数据库、恢复协调器和事件消费游标。

### 验证结果

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q
python -m compileall -q openaiglass-sdk/server-python
```

---

<a id="iteration-v17"></a>
## iteration-v17：全双工实时语音第一版

来源：`iteration-v17.md`


### 本轮目标

把全双工实时语音从设计文档推进到 SDK 第一版运行时能力，先覆盖协议事件、实时会话状态机、播放仲裁贯通、回声候选观测、迟到输出丢弃和回放级单测。

本轮对应对外 SDK 版本：`sdk-v18`。

### 主要改动

1. 新增 `RealtimeVoiceRuntime`，管理 `full_duplex_realtime` 会话、输入流、输出流、最近事件和延迟指标。
2. 新增 `RealtimeModelAdapter` 抽象，并提供 `LoopbackRealtimeModelAdapter` 与 `HalfDuplexFallbackRealtimeModelAdapter`，避免第一版强绑定模型供应商。
3. 现有 `VoiceRuntime` 持有实时语音运行时，并共享 `PlaybackArbiter`。
4. `/ws_realtime_audio` 作为实时媒体入口，继续复用 SDK `MediaFrame` 编码。
5. 控制面支持 `voice.realtime.session.open/opened/closed`、`voice.realtime.input.started/committed` 和 `voice.realtime.user_interrupt`。
6. 实时输出转换为 `PlaybackIntent(source=agent_reply)`，用户插话转换为播放仲裁器 `user_interrupt` 决策。
7. 用户插话后，SDK 下发 `actuator.audio.interrupt` 与 `voice.realtime.output.cancelled`，并丢弃同一输出流的迟到分片。
8. 运行态快照新增实时会话、输入输出流、打断、回声拒绝计数和延迟指标。

### 当前边界

1. 第一版采用 WebSocket `MediaFrame` 路径，不强制 WebRTC。
2. 服务端不实现声学 AEC/VAD 算法，只消费端侧结构化字段。
3. 真实实时模型供应商尚未绑定到默认运行时，后续通过 `RealtimeModelAdapter` 接入。
4. 半双工 `/ws_audio` 链路保持兼容，功能开发者不需要迁移已有业务能力。

### 验证结果

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py -q
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_voice_runtime.py openaiglass-sdk/tests/unit/test_playback_arbiter.py -q
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit -q
python -m compileall -q openaiglass-sdk/server-python
```

### 真机验收建议

1. 服务端启动后确认 `/api/runtime/devices` 中能看到 `realtime_state` 和 `active_realtime_session`。
2. 眼镜端或手机中继端连接 `/ws_realtime_audio`，发送 `voice.realtime.input.delta` 媒体帧。
3. 播放期间上报 `voice.realtime.user_interrupt`，确认服务端下发 `actuator.audio.interrupt` 和 `voice.realtime.output.cancelled`。
4. 注入 `voice_activity=echo` 或低置信度回声候选，确认 `realtime_echo_rejected_count` 增加且没有 `user_interrupt` 决策。

---

<a id="iteration-v18"></a>
## iteration-v18：语音会话模式启动配置

来源：`iteration-v18.md`


### 本轮目标

把全双工或半双工语音会话选择从代码行为改为服务端启动配置。默认使用 `full_duplex_realtime`，旧眼镜固件、半双工回放或只验证 `/ws_audio` 的场景可以显式配置为 `half_duplex`。

本轮对应对外 SDK 版本：`sdk-v19`。

### 主要改动

1. `ServerSettings` 新增 `voice_session_mode`，可从环境变量 `VOICE_SESSION_MODE` 读取。
2. 配置值只允许 `full_duplex_realtime` 和 `half_duplex`，非法值会触发结构化配置错误。
3. 眼镜注册后，控制面按配置下发 `voice.realtime.session.open` 或旧的 `voice.session.open`。
4. `openaiglass.sdk.server` 启动默认环境新增 `VOICE_SESSION_MODE=full_duplex_realtime`。
5. 运行态快照新增顶层字段 `configured_voice_session_mode`，便于真机联调时确认当前服务端模式。
6. 更新业务侧配置样例和 SDK 安装与能力开发指南，说明默认全双工和半双工回退方式。

### 当前边界

1. 该配置只决定注册后服务端默认打开哪条语音链路，不替代端侧 AEC/VAD 能力协商。
2. `half_duplex` 仍走原 `/ws_audio` 链路，适合旧固件和当前半双工回放工具。
3. `full_duplex_realtime` 仍需要端侧或手机中继连接 `/ws_realtime_audio` 并上报 `voice.realtime.*` 事件。

### 验证结果

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_settings.py openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py -q
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/integration/test_control_register_flow.py -q
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/integration/test_voice_dialog_flow.py -q
python -m compileall -q openaiglass-sdk/server-python
```

### 真机验收建议

1. 默认启动服务端，眼镜注册后确认收到 `voice.realtime.session.open`。
2. 在 `config/local_server.env` 中设置 `VOICE_SESSION_MODE=half_duplex` 后重启服务端，确认眼镜注册后收到 `voice.session.open`。
3. 打开 `/api/runtime/devices` 或运行态快照，确认 `configured_voice_session_mode` 与实际配置一致。

---

<a id="iteration-v19"></a>
## iteration-v19：SDK v20 阻塞点收口

来源：`iteration-v19.md`


### 本轮目标

根据业务功能团队反馈，收口 SDK 阻塞业务能力继续迭代的四个公共能力缺口。

本轮对应对外 SDK 版本：`sdk-v20`。

### 主要改动

1. 真实 `ControlRuntime` 初始化时自动为 `DeviceGroupRuntime` 绑定视频链路启动和停止适配器。
2. `DeviceGroupContext.start_phone_video_link(...)` 返回标准 `phone_video_link_task` 快照字段，业务可继续通过 `query_task(link["task_id"])` 查询阶段、流编号、目标地址和错误信息。
3. `openaiglasses` 公开入口导出 `BaseMcpAdapter` 和 `McpMethodSpec`，业务 MCP Adapter 不再需要直接导入 `agent_core` 包路径。
4. `phone-mock` 配置支持 `task_class` 与 `processor_plugins`，可加载 Python `BasePhoneTask` / `BasePhoneProcessor` 作为 mock 插件。
5. `glass-playback` 支持响应 `voice.realtime.session.open`，保存并复用服务端下发的 `session_id`。
6. SDK 预检新增真实服务端句柄视频链路 adapter 绑定检查。

### 当前边界

1. `/api/debug/phone-video-link/start|stop` 仍保留为调试兼容入口；业务能力不要依赖它。
2. Python phone 插件加载只服务于 `phone-mock` 和本地契约测试；真实 iPhone 插件仍通过 Swift 侧 `PhoneTaskCapabilityRegistry.register(taskType:)` 接入。
3. `glass-playback` 已补齐全双工打开握手，但触发音频仍复用 `/ws_audio` 上传；真正实时媒体帧验收仍需 `/ws_realtime_audio`。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/phone-mock:openaiglass-sdk/glass-playback:openaiglass-for-blind uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_sdk_phase_two.py \
  openaiglass-sdk/tests/unit/test_phone_mock_config.py \
  openaiglass-sdk/tests/unit/test_playback_config.py -q

PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/phone-mock:openaiglass-sdk/glass-playback:openaiglass-for-blind uv run python -m compileall -q \
  openaiglass-sdk/server-python openaiglass-sdk/phone-mock openaiglass-sdk/glass-playback
```

---

<a id="iteration-v20"></a>
## iteration-v20：SDK v21 glass-playback 控制循环修复

来源：`iteration-v20.md`


### 本轮目标

根据业务功能团队在 2026-04-28 的设备级回放反馈，修复 `glass-playback` 保存播放音频时阻塞控制消息循环的问题，并补齐最小设备级服务端产物断言。

本轮对应对外 SDK 版本：`sdk-v21`。

### 主要改动

1. `actuator.audio.play` 处理不再同步下载 `/stream.wav`。
2. `actuators.audio_play.save_audio_to` 改为后台线程保存，控制消息循环可以继续处理后续 `sensor.camera.capture`。
3. 保存成功、失败和调度都会写入事件日志，便于排查下行播放流保存问题。
4. `glass-playback` 配置新增 `assertions.server_artifacts`，可断言真实服务端业务产物文件已生成。
5. CLI 输出新增 `assertions_ok` 和 `assertion_failures`；断言失败时退出码为 `1`。

### 当前边界

1. 设备级断言当前只覆盖服务端文件产物存在性和最小大小，不做业务语义判断。
2. `{session_id}` 和 `{device_id}` 占位符只用于产物路径替换。
3. 更细的 Tool、Task、模型请求和业务结果语义断言仍由服务端回归工具或业务侧测试覆盖。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback:openaiglass-for-blind uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_playback_config.py -q

PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/phone-mock:openaiglass-sdk/glass-playback:openaiglass-for-blind uv run python -m compileall -q \
  openaiglass-sdk/server-python openaiglass-sdk/phone-mock openaiglass-sdk/glass-playback
```

---

<a id="iteration-v21"></a>
## iteration-v21：SDK v22 日志观测增强

来源：`iteration-v21.md`


### 本轮目标

根据业务功能团队在 2026-04-28 的联调反馈，补齐回放眼镜和服务端语音链路的关键时间点日志，减少排查时对业务侧临时打印的依赖。

本轮对应对外 SDK 版本：`sdk-v22`。

### 主要改动

1. `glass-playback` 启动后会打印命令行状态，避免命令运行期间没有任何可见进展。
2. 回放眼镜命令行只打印收到的控制消息，不打印自身发送的控制消息。
3. 服务端 `VoiceRuntime` 在收到首个模型文本增量时打印 `大模型返回首个 token`，并记录 `first_token_latency_ms`。
4. 回放眼镜保存播放音频时按流式读取 `/stream.wav`，收到第一段下行音频后立即打印 `elapsed_ms` 和首段字节数。
5. 单元测试覆盖全双工握手日志边界和播放音频首段到达日志。

### 当前边界

1. `first_token_latency_ms` 从语音链路开始调用 AgentFacade 前计时，主要用于联调排查，不作为业务 SLA 口径。
2. 回放眼镜首段音频日志基于 `/stream.wav` HTTP 响应首个非空字节块；真实 ESP32 端侧还需要在固件或端侧 SDK 中补同类日志。
3. 设备侧“只打印收到消息”约束当前先落在 `glass-playback` 命令行输出；服务端调试日志仍会按服务端运行时策略记录必要的收发细节。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_playback_config.py -q

PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py -q

PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py -q
```

---

<a id="iteration-v22"></a>
## iteration-v22：SDK v23 服务端配置收口

来源：`iteration-v22.md`


### 本轮目标

根据 2026-04-28 的 SDK 使用反馈，把服务端模型、ASR、TTS、系统提示词和音频上限等运行时配置显式暴露到 `openaiglass-for-blind/config/local_server.env`，避免业务开发者改代码或依赖临时 shell 环境变量。

本轮对应对外 SDK 版本：`sdk-v23`。

### 主要改动

1. `local_server.env.example` 和当前 `local_server.env` 显式列出 `DASHSCOPE_API_KEY`、`VOICE_MODEL_BASE_URL`、`VOICE_ASR_MODEL_NAME`、`AGENT_MODEL_NAME`、`VOICE_MODEL_NAME`、`VOICE_MODEL_VOICE`、`TTS_MODEL_NAME`、`TTS_VOICE`、`TTS_WEBSOCKET_API_URL`、`TTS_SAMPLE_RATE_HZ`、`VOICE_MODEL_TIMEOUT_MS`、`VOICE_SYSTEM_PROMPT` 和 `MAX_SEGMENT_AUDIO_BYTES`。
2. 服务端 CLI 默认环境补齐上述配置项，保持本地启动、远程启动和 `ServerSettings.from_env()` 使用同一组环境变量。
3. 远程启动环境导出不再只透传少量白名单变量，而是透传 SDK 服务端默认配置集合和必要的地址派生变量。
4. 增加 CLI 单元测试，确认 `local_server.env` 中的模型配置会进入服务端子进程环境。

### `ServerSettings` 的职责

`ServerSettings` 不只是配置值容器。它是服务端运行时的类型化配置边界，负责：

1. 从环境变量读取 SDK 运行时配置。
2. 对端口、日志级别、心跳、模型名、TTS、语音会话模式和音频上限做启动前校验。
3. 为 HTTP 健康检查、运行时摘要和日志输出提供脱敏后的配置摘要。
4. 为 `ControlRuntime`、`VoiceRuntime`、`AgentFacade`、MCP 和设备组运行时提供一致配置对象。

### 当前边界

1. 后台启动时 CLI 仍会把子进程 `LOG_FILE` 置空，因为 stdout/stderr 已经重定向到启动器日志文件，避免同一条日志写两次。
2. `DASHSCOPE_API_KEY=""` 会覆盖 shell 中已有的同名变量；本地联调应把真实 key 写入 `local_server.env` 或删除该行后改用 shell 环境。
3. 模型供应商仍按当前 DashScope/OpenAI-compatible 接口适配，其他供应商需要后续通过模型 Adapter 扩展。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_server_cli.py \
  openaiglass-sdk/tests/unit/test_settings.py -q
```

---

<a id="iteration-v23"></a>
## iteration-v23：SDK v24 Agent 模型兼容性与可观测性

来源：`iteration-v23.md`


### 本轮目标

根据 2026-04-28 的联调反馈，修复 `AGENT_MODEL_NAME=qwen-turbo` 时设备侧只看到超时、服务端没有明确异常日志的问题。

本轮对应对外 SDK 版本：`sdk-v24`。

### 主要改动

1. `VoiceRuntime` 在音频段进入 ASR 前打印 INFO 日志，包含输入流、音频段、时长、字节数、ASR 模型和 Agent 模型。
2. `VoiceRuntime` 在 ASR 完成后打印 INFO 日志，明确即将进入 agent-core。
3. `OpenAIAgentLoopRunner` 在调用模型前打印 INFO 日志，包含模型名、运行模式、消息数、工具数和超时时间。
4. agent-core 结构化失败和非结构化失败改为 ERROR 日志，不再隐藏在 DEBUG 中。
5. 对 `qwen-turbo`、`qwen-plus`、`qwen-max` 这类不适合当前 `stream=True + tools` 组合的模型直接返回 `INVALID_CONFIG`，避免设备侧等待超时。
6. 流式 Agent 调用增加 SDK 层超时保护，超过 `VOICE_MODEL_TIMEOUT_MS` 后返回结构化失败。

### 当前边界

1. 当前语音链路依赖流式文本增量进入 TTS，同时需要 SDK Tools 支持 Task、MCP 和硬件能力调用，因此默认使用流式 Agent + tools。
2. 非流式工具模式尚未实现；如果要使用只支持非流式工具调用的模型，需要后续增加独立运行模式。
3. 用户贴出的日志只到设备注册和实时会话降级，没有出现 `语音链路开始处理音频段`，说明那段日志本身还不能证明已经进入模型调用。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py -q
```

---

<a id="iteration-v24"></a>
## iteration-v24：SDK v25 撤销模型硬编码黑名单

来源：`iteration-v24.md`


### 本轮目标

修正上一轮把 `qwen-turbo`、`qwen-plus`、`qwen-max` 写入 SDK 内置不兼容模型集合的问题。该判断来自联调现象和推断，不应作为 SDK 规则硬编码。

本轮对应对外 SDK 版本：`sdk-v25`。

### 主要改动

1. 移除 `OpenAIAgentLoopRunner` 中的 `incompatible_models` 硬编码集合。
2. 移除对应的 `qwen-turbo` 启动前拦截单元测试。
3. 保留上一轮新增的 Agent 调用前 INFO 日志、agent-core ERROR 日志和流式 Agent 超时保护。
4. 更新开发指南，说明模型兼容性应通过真实错误日志、超时配置和 `model_request` 诊断，不靠 SDK 黑名单。

### 当前边界

1. 当前语音链路仍默认使用流式 Agent，并把 SDK Tools 暴露给模型。
2. 如果某个模型实际不支持当前组合，应由模型接口返回错误或由 SDK 超时保护暴露，而不是预设模型名黑名单。
3. 后续可以增加显式运行模式，例如 `AGENT_RUN_MODE=stream_tools` / `non_stream_tools`，再按模式和模型能力做配置校验。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py -q
```

---

<a id="iteration-v25"></a>
## iteration-v25：SDK v26 绑定等待诊断

来源：`iteration-v25.md`


### 本轮目标

根据 2026-04-28 的联调日志反馈，补齐 `glass-playback` 在发送触发音频前卡住时的诊断信息。用户看到服务端只有 voice session 日志但没有 ASR/Agent 日志时，应能直接判断是否还没进入音频链路。

本轮对应对外 SDK 版本：`sdk-v26`。

### 主要改动

1. 服务端自动绑定未满足条件时打印 INFO 日志，包含当前在线 glass、在线 phone、期望绑定设备和提示。
2. `glass-playback` 等待目标 phone 绑定前打印 `等待设备绑定`。
3. `glass-playback` 开始发送触发音频前打印流编号、音频段编号、音频路径和 chunk 数。
4. `glass-playback` 触发音频发送完成后打印完成状态。
5. `glass-playback` 运行失败时打印失败原因并写入事件日志。

### 当前边界

1. 如果配置了 `desired_phone_device_id` 且 `startup.wait_for_binding=true`，`glass-playback` 会等到目标 phone 在线并绑定后才发送触发音频。
2. 服务端没有 `sensor.audio.segment.started` 日志时，说明还没有进入 ASR/Agent 链路。
3. 如果只验证普通语音问答，不依赖手机能力，可以在回放配置中移除 `desired_phone_device_id` 或设置 `startup.wait_for_binding=false`。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_playback_config.py \
  openaiglass-sdk/tests/integration/test_control_register_flow.py -q
```

---

<a id="iteration-v26"></a>
## iteration-v26：SDK v27 服务端默认配置单一来源

来源：`iteration-v26.md`


### 本轮目标

修复 `settings.py` 和服务端 CLI 同时维护大量相同配置默认值的问题，避免模型、音色、心跳等默认值在两处漂移。

本轮对应对外 SDK 版本：`sdk-v27`。

### 主要改动

1. `ServerSettings` 继续作为服务端运行时配置的唯一默认值、读取和校验入口。
2. `openaiglasses.cli.server.SERVER_DEFAULTS` 改为通过 `ServerSettings()` 派生，不再手写第二份模型配置。
3. CLI 仍保留 `HOST` / `PORT` 作为 `local_server.env` 的用户友好别名，并在启动时转换为运行时实际读取的 `SERVER_HOST` / `SERVER_PORT`。
4. 新增单元测试，验证 CLI 默认值与 `ServerSettings` 默认值一致。

### 为什么以前会重复

`settings.py` 面向服务端进程运行时，负责类型化配置和校验；`server.py` 面向启动器，负责合并 `local_server.env`、命令行参数和默认环境变量。历史上为了让启动器在配置文件缺项时仍能启动，CLI 复制了一份默认值，但这会造成配置漂移。

### 当前边界

1. `LOG_FILE` 仍由 CLI 特殊处理：后台启动时 stdout/stderr 已经重定向到启动器日志文件，所以子进程 `LOG_FILE` 会被置空，避免重复写日志。
2. `SERVER_PUBLIC_HOST` 是业务侧同步配置，不属于 `ServerSettings` 运行时监听配置。
3. `VOICE_RUNS_ROOT` 在 CLI 中仍可按 repo root 派生默认路径，运行时继续通过 `ServerSettings` 读取。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_server_cli.py \
  openaiglass-sdk/tests/unit/test_settings.py -q
```

---

<a id="iteration-v27"></a>
## iteration-v27：SDK v28 Agent 运行热路径拆薄

来源：`iteration-v27.md`


### 本轮目标

降低语音问答链路中 `AgentFacade.handle_turn(...)` 之后的运行时热路径复杂度，避免单轮 Agent 执行中混杂依赖导入、provider 创建、上下文装配、流式事件观察和拍照续跑逻辑。

本轮对应对外 SDK 版本：`sdk-v28`。

### 主要改动

1. 将 `OpenAIAgentLoopRunner.run_turn(...)` 中的单轮上下文装配拆到 `AgentTurnRuntimeFactory`。
2. 将 OpenAI Agents SDK 的导入、`MultiProvider` 缓存、`RunConfig` 创建和 `Runner` 调用收敛到 `OpenAIAgentsSdkBridge`。
3. 将流式文本增量、`capture_photo` 进度播报、抓拍图片等待和图片续跑观察逻辑拆到 `StreamedAgentTurnObserver`。
4. 新增 `OpenAIAgentLoopRunner.preload_resources()`，真实服务端通过 `build_agent_facade_from_sdk(...)` 和 `build_default_agent_facade(...)` 构建时会主动预热 Agents SDK 模块和 provider。
5. 保持单测和业务宿主可替换性：通用 runner 构造函数不强制预热，便于测试注入 fake Agents SDK 或宿主自行控制预热时机。

### 延迟边界

1. 预热阶段只做依赖入口和 provider 级资源准备，不创建每轮会话上下文。
2. 单轮热路径仍必须动态读取 active Skill、工具白名单、历史消息和设备上下文，因为这些数据随会话变化。
3. `first_token_latency_ms` 口径不变：从 ASR 完成准备进入 `AgentFacade.handle_turn(...)` 前开始，到首个模型文本增量到达 `VoiceRuntime` 为止。

### 当前边界

1. `Agent` 和 `RunConfig` 仍按轮创建，因为 system prompt、工具列表和 group_id 都可能随会话变化。
2. 拍照后的多模态图片续跑仍走当前 SDK 主链路兼容实现，后续可继续收敛成标准 Tool result + Agent loop。
3. 本轮不引入业务能力代码，不修改 `openaiglass-for-blind/capabilities`。

### 验证结果

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py -q
```

结果：24 passed。

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py \
  openaiglass-sdk/tests/unit/test_sdk_phase_two.py \
  openaiglass-sdk/tests/unit/test_server_cli.py \
  openaiglass-sdk/tests/unit/test_settings.py -q
```

结果：68 passed。

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run python -m compileall -q \
  openaiglass-sdk/server-python/agent_core/runtime/runner.py \
  openaiglass-sdk/server-python/openaiglasses/server.py
```

结果：通过。

---

<a id="iteration-v28"></a>
## iteration-v28：SDK v29 真实眼镜实时语音打开兼容

来源：`iteration-v28.md`


### 本轮目标

修复真实 ESP32 眼镜在服务端默认 `VOICE_SESSION_MODE=full_duplex_realtime` 下注册成功但 WakeNet 没有效果的问题。

本轮对应对外 SDK 版本：`sdk-v29`。

### 问题原因

当前服务端默认会在眼镜注册后下发 `voice.realtime.session.open`。此前真实 ESP32 眼镜固件只处理旧的 `voice.session.open`，因此不会：

1. 保存服务端下发的 `session_id`。
2. 设置 `s_voice_session_opened=true`。
3. 建立 `/ws_audio` 上行连接。
4. 打开 WakeNet 门控。

结果表现为控制连接已经注册，但用户说唤醒词后没有任何语音段上传。

### 主要改动

1. ESP32 眼镜运行时新增 `voice.realtime.session.open` 控制消息处理。
2. 真实眼镜收到实时打开请求后，回复 `voice.realtime.session.opened`。
3. 回复 payload 声明 `accepted_mode=half_duplex`，并声明 `capabilities.aec=false`、`vad=true`、`barge_in=false`、`output_cancel=false`。
4. 服务端根据 `aec=false` 把实时会话降级为半双工；眼镜端复用现有 WakeNet 和 `/ws_audio` 链路。
5. 新增静态测试，防止真实眼镜运行时再次遗漏实时打开兼容分支。

### 当前边界

1. 本轮不是实现 ESP32 真全双工 AEC/VAD 实时音频，只是让默认全双工服务端配置下的真实眼镜可降级工作。
2. 若需要验收真正全双工插话，应使用支持端侧 AEC 的新固件或手机音频中继。
3. 修改服务端公网地址后仍必须重新同步 `host/glass/config/local_build.env` 并重新构建烧录固件。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_glass_esp32_runtime.py \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py -q
```

---

<a id="iteration-v29"></a>
## iteration-v29：SDK v30 顶层 Python 安装入口

来源：`iteration-v29.md`


### 本轮目标

让本地开发者可以使用更自然的安装命令：

```bash
uv pip install -e openaiglass-sdk
```

此前需要安装 `openaiglass-sdk/server-python`，暴露了 SDK 内部目录结构。

本轮对应对外 SDK 版本：`sdk-v30`。

### 主要改动

1. 在 `openaiglass-sdk/` 顶层新增 `pyproject.toml`。
2. 顶层项目名仍为 `openaiglasses-sdk`，CLI 入口仍为 `openaiglass.*`。
3. `setuptools.package-dir` 指向 `server-python`，包发现范围为 `server-python` 下的 `openaiglasses`、`agent_core`、`protocol`、`runtime` 等模块。
4. 保留 `server-python/pyproject.toml`，便于内部包源码目录继续独立构建和调试。
5. 更新开发指南和 SDK README，把本地 editable 安装命令改为顶层入口。

### 当前边界

1. 顶层 `pyproject.toml` 是 Python SDK 的聚合安装入口，不代表 iOS 和 ESP32 已经变成 Python 包。
2. `server-python` 仍是实际 Python 源码目录，端侧源码仍分别位于 `phone-ios` 和 `glass-esp32`。
3. 后续若做正式多包 workspace 发布，可以再把顶层入口扩展为统一版本治理和构建入口。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_package_check.py -q
```

也可以在临时虚拟环境中验证：

```bash
uv venv /tmp/openaiglass-sdk-install-check
/tmp/openaiglass-sdk-install-check/bin/python -m pip install -e openaiglass-sdk
/tmp/openaiglass-sdk-install-check/bin/python -c "import openaiglasses; print(openaiglasses.__name__)"
```

---

<a id="iteration-v30"></a>
## iteration-v30：SDK v31 服务端前台运行生命周期

来源：`iteration-v30.md`


### 本轮目标

让 `openaiglass.server.run` 成为真正的前台运行命令。开发者用 Ctrl+C 结束命令或关闭当前终端时，本地服务端应随命令一起退出，不再需要额外执行 `openaiglass.server.stop`。

本轮对应对外 SDK 版本：`sdk-v31`。

### 主要改动

1. `server local all` 改为直接调用前台运行逻辑，而不是后台 `start` 后再 `tail -F` 日志。
2. 新增 `run_local_foreground(...)`，以前台子进程启动 `openaiglasses.cli.server_runtime`。
3. 前台运行不写 PID 文件、不重定向 stdout/stderr，也不使用 `start_new_session`。
4. Ctrl+C 时 CLI 会终止子进程；如果子进程未能及时退出，会升级为 kill。
5. `openaiglass.server.start/stop/logs` 仍保留原有后台管理语义。

### 使用边界

1. 日常联调推荐 `openaiglass.server.run`。
2. 需要跨终端保留服务端时，使用 `openaiglass.server.start`，然后用 `openaiglass.server.logs` 看日志，用 `openaiglass.server.stop` 停止。
3. 如果已经有后台 PID 文件指向正在运行的服务端，前台 `run` 会拒绝启动并提示先 stop 或 logs。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_server_cli.py -q
```

---

<a id="iteration-v31"></a>
## iteration-v31：SDK v32 服务端 Secret 环境合并

来源：`iteration-v31.md`


### 本轮目标

修复开发者已经在外部环境变量中配置 `DASHSCOPE_API_KEY`，但服务端仍提示缺少 key 的问题。

本轮对应对外 SDK 版本：`sdk-v32`。

### 问题原因

`openaiglass.server.run/start` 启动器此前按以下顺序合并环境：

1. 继承当前 shell 环境变量。
2. 读取 `config/local_server.env` 并覆盖同名变量。
3. 补齐 SDK 默认值。

如果 `local_server.env` 保留模板占位：

```env
DASHSCOPE_API_KEY=""
```

它会把 shell 或 CI 中已经注入的真实 key 覆盖为空，导致运行时 `ServerSettings.dashscope_api_key` 为空。

### 主要改动

1. `merged_env(...)` 对 `DASHSCOPE_API_KEY` 增加 secret 合并规则。
2. 当配置文件中的 `DASHSCOPE_API_KEY` 为空，且外部环境已有非空 key 时，保留外部 key。
3. 普通配置仍然由 `local_server.env` 覆盖外部环境，避免改变现有模型、端口和设备配置语义。
4. 新增单元测试覆盖空本地占位符不覆盖外部 key。

### 当前边界

1. 如果外部环境没有 key，且 `local_server.env` 仍为空，服务端仍会按预期报 `缺少 DASHSCOPE_API_KEY`。
2. 如果 `local_server.env` 中写了非空 key，它仍会覆盖外部环境。
3. 修改 key 后必须重启服务端，已运行进程不会自动刷新环境变量。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_server_cli.py \
  openaiglass-sdk/tests/unit/test_settings.py -q
```

---

<a id="iteration-v32"></a>
## iteration-v32：SDK v33 视觉拍照播报去重

来源：`iteration-v32.md`


### 本轮目标

修复真实眼镜视觉问答中，图片解读内容已经播出后，又播报“好的，你保持别动，我拍一张帮你看”的重复和倒序体验问题。

本轮对应对外 SDK 版本：`sdk-v33`。

### 问题原因

视觉链路中存在两个播报来源：

1. 模型在调用 `capture_photo` 前通过普通流式文本输出拍照提示，例如“我来拍张照，看看你面前有什么”。
2. SDK 在观察到 `capture_photo` 工具调用事件时，又通过 `progress_callback` 注入固定播报“好的，你保持别动，我拍一张帮你看。”

这两路会进入不同的 TTS/播放请求，真实设备上可能发生排队倒序，导致用户先听到图片解读结果，随后又听到拍照提示。

### 主要改动

1. `StreamedAgentTurnObserver` 不再在 `capture_photo` 工具调用事件上注入固定中间播报。
2. 视觉链路仍保留模型流式文本增量和图片解读主链路流式输出。
3. 调整 agent-core 单测，验证拍照工具调用不会额外产生 SDK 固定 progress 播报。

### 当前边界

1. 模型仍可能自行输出“我来拍张照”这类文本，这是 Agent 回复的一部分，会按普通流式 TTS 播放。
2. 后续如果要更强约束，可在模型 prompt 或 stream observer 中对“工具调用前文本”做策略化过滤；本轮只去掉 SDK 额外注入的重复固定播报。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py -q
```

---

<a id="iteration-v33"></a>
## iteration-v33：SDK v34 语音结束自动照片

来源：`iteration-v33.md`


### 本轮目标

修复真实眼镜视觉问答中“模型说正在拍照，但实际解读的是几秒前镜头位置”的体验问题，将视觉照片采集从模型主动触发改为语音段结束后 SDK 自动异步触发。

本轮对应对外 SDK 版本：`sdk-v34`。

### 问题原因

旧链路中，模型需要先输出或决策到 `capture_photo` 工具调用，服务端才向眼镜发送 `sensor.camera.capture`。真实播放和抓拍并行时，用户听到“我拍一张看一下”时，抓拍可能已经完成，容易误以为照片应该对应播报时刻的镜头方向。

### 主要改动

1. `VoiceRuntime` 在每个语音段结束并进入服务端处理后，立即后台启动一次 `utterance_finished` 抓拍。
2. 新增 `UtterancePhotoStore`，用于保存语音轮次、后台抓拍状态、上传结果和错误信息。
3. 新增模型可见工具 `get_latest_utterance_photo`，只读取本轮语音结束后的自动照片，必要时等待数秒上传完成。
4. `capture_photo` 仍作为 SDK 内部兼容工具保留，但不再注册到模型工具列表。
5. 图片解读主链路继续复用现有多模态 follow-up，不要求业务能力自行上传或管理图片。

### 当前边界

1. 自动照片通过控制连接中的 `sensor.camera.capture` / `sensor.camera.captured` 完成，仍不是独立二进制图片通道。
2. 每个语音段都会尝试后台抓拍；如果设备没有相机网关或端侧抓拍失败，语音主链路继续执行，只有模型调用 `get_latest_utterance_photo` 时才会看到结构化错误。
3. 业务 Skill 不应再把 `capture_photo` 写入 `allowed_tools`。需要视觉问答时使用 `get_latest_utterance_photo`。
4. SDK 只在内存中保留最近若干轮自动照片记录，避免非视觉对话持续抓拍导致内存无界增长。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py \
  openaiglass-sdk/tests/unit/test_sdk_phase_two.py::test_build_agent_facade_from_sdk_preloads_agent_resources \
  openaiglass-sdk/tests/unit/test_sdk_phase_two.py::test_openai_glasses_sdk_registers_skill_runtime -q
```

---

<a id="iteration-v34"></a>
## iteration-v34：SDK v35 实时 ASR 热路径

来源：`iteration-v34.md`


### 本轮目标

把语音转写从“用户说完后提交整段 WAV”改为“用户说话时同步送入实时 ASR”，降低用户停止说话到 Agent 开始运行之间的等待时间。

本轮对应对外 SDK 版本：`sdk-v35`。

### 问题原因

旧链路中，`/ws_audio` 收到的 `audio_chunk` 只写入 `SegmentBuffer`。直到控制面收到 `sensor.audio.segment.finished`，`VoiceRuntime` 才把整段音频封装为 WAV 并通过非流式 Chat Completions ASR 请求转写。这样 ASR 的网络请求和模型处理全部发生在用户说完之后，无法达到 200ms 级首音频体验。

### 主要改动

1. 新增 `StreamingSpeechRecognitionSession` 抽象，支持音频帧到达时持续追加 PCM。
2. `DashscopeSpeechRecognitionClient` 在 `VOICE_ASR_MODE=realtime` 时创建百炼 Qwen ASR Realtime WebSocket 会话。
3. `VoiceRuntime.on_audio_frame(...)` 在缓存本地音频的同时，把每个 PCM 分片送入实时 ASR。
4. `VoiceRuntime._run_model_pipeline(...)` 优先读取实时 ASR 最终文本；实时 ASR 失败、超时或返回空文本时，自动回退原有整段 WAV ASR。
5. `config/local_server.env.example` 增加 `VOICE_ASR_MODE`、`VOICE_ASR_REALTIME_MODEL_NAME` 和 `VOICE_ASR_REALTIME_TIMEOUT_MS`。

### 当前边界

1. 这轮只解决 ASR 非流式问题。Agent 首 token、工具调用、视觉图片解读和 TTS 首音频仍可能成为后续瓶颈。
2. 当前 TTS 仍通过 CosyVoice 流式 WebSocket 边推文本边收音频；如果要继续压低首音频，需要接入实时 TTS 并减少按句提交等待。
3. 视觉问答会额外等待自动照片上传和多模态图片解读，不应拿视觉链路作为普通语音问答的最低延迟指标。
4. 实时 ASR 只在 16kHz、单声道、PCM16 输入下启用；其他输入会自动回退批量 ASR。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_server_cli.py -q
```

---

<a id="iteration-v35"></a>
## iteration-v35：SDK v36 glass-playback 安装包入口收敛

来源：`iteration-v35.md`


### 本轮目标

让功能开发者在只安装 Python SDK 包的情况下启动 `glass-playback`，不再依赖 SDK 源码目录，也不需要在命令中手动传 `--sdk-root`。

本轮对应对外 SDK 版本：`sdk-v36`。

### 问题原因

旧命令在 `openaiglass.glass.start --runtime playback` 分支中固定按 `<sdk-root>/glass-playback` 查找运行时代码。这个设计适合 SDK 仓库源码开发，但会把 SDK 内部目录结构泄露给业务能力开发者。业务项目只安装 SDK 包时，开发者未必拥有 `openaiglass-sdk` 源码目录，`--sdk-root` 也不应该成为设备级回放的必填知识。

### 主要改动

1. `openaiglass.glass.start --runtime playback` 优先直接导入已安装包中的 `openaiglass_glass_playback`。
2. 仅在安装包中找不到 playback 运行时时，才回退到源码开发态的 `<sdk-root>/glass-playback`。
3. SDK 顶层 `pyproject.toml` 将 `glass-playback/openaiglass_glass_playback` 纳入 Python 包发现范围。
4. 开发指南中的 `glass-playback` 启动命令去掉 `--sdk-root`，并说明该参数只用于 SDK 源码或固件开发态。

### 当前边界

1. `--sdk-root` 仍保留给 ESP32 固件源码构建、烧录、监看等 SDK 开发场景。
2. `glass-playback` 的配置和测试资产仍由业务工程提供，例如 `host/glass-playback/config/*.json` 和 `testdata/*`。
3. 当前只处理 Python SDK 包形态；iOS XCFramework 和 ESP32 component registry 发布仍是后续工作。

### 验证建议

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_playback_config.py -q

PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m unittest discover \
  -s openaiglass-sdk/tests/unit -p 'test_*.py' -v
```

---

<a id="iteration-v36"></a>
## iteration-v36：SDK v37 glass-playback 状态日志格式统一

来源：`iteration-v36.md`


### 本轮目标

让 `glass-playback` 命令行状态日志能和服务端日志按时间直接对齐，同时去掉固定 `[glass-playback]` 前缀，减少多设备联调时的人工整理成本。

本轮对应对外 SDK 版本：`sdk-v37`。

### 主要改动

1. `glass-playback` 的 `_print_status(...)` 统一输出 UTC ISO 时间戳。
2. 状态日志格式调整为 `时间-INFO-glass.playback---消息 key=value`。
3. 继续保持“只打印收到的控制消息，不打印自身发送的控制消息正文”的设备侧日志边界。

### 当前边界

1. 本轮只调整 `glass-playback` 命令行状态日志，不改变事件 JSONL 和执行器 JSONL 的结构。
2. 本轮不改变回放协议、设备注册、绑定等待、音频上传或执行器处理逻辑。

### 验证

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback:openaiglass-for-blind \
uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_playback_config.py -q
```

---

<a id="iteration-v37"></a>
## iteration-v37：SDK v38 glass-playback 下行语音直接播放

来源：`iteration-v37.md`


### 本轮目标

让功能开发者在使用 `glass-playback` 做设备级回放时，可以直接听到服务端下行语音，而不是只能把音频保存到文件后再手动打开。

本轮对应对外 SDK 版本：`sdk-v38`。

### 主要改动

1. `actuators.audio_play.mode` 新增 `play_and_auto_finish`。
2. 新模式会从服务端 `/stream.wav` 下载下行语音到系统临时文件，调用本机播放器播出，播放结束后删除临时文件。
3. 新模式会在收到播放命令时上报 `actuator.audio.started`，并在播放线程结束后上报 `actuator.audio.finished`。
4. 默认播放器选择：macOS 使用 `afplay`；Linux 依次尝试 `paplay`、`aplay`、`ffplay`；配置 `audio_play.player_command` 时优先使用业务指定命令。

### 当前边界

1. 直接播放模式不会写入 `save_audio_to` 目录；需要留存音频用于断言或回溯时仍应使用 `record_and_auto_finish`。
2. 当前实现为“下载到临时文件后播放”，不是边下载边播放的低延迟播放器。
3. 找不到本机播放器时只记录 `actuator.audio.play_failed` 事件，并仍会上报播放结束，避免服务端等待执行器状态。

### 验证

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback:openaiglass-for-blind \
uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_playback_config.py -q
```

---

<a id="iteration-v38"></a>
## iteration-v38：SDK v39 实时 ASR 延迟指标口径修正

来源：`iteration-v38.md`


### 本轮目标

修正实时 ASR 首文本耗时日志的起点，避免把实时 ASR 会话创建、控制消息时序或首个音频 chunk 之前的等待时间算入 `first_asr_partial_latency_ms`。

本轮对应对外 SDK 版本：`sdk-v39`。

### 主要改动

1. `DashscopeRealtimeSpeechRecognitionSession` 在收到第一段非空音频 chunk 时记录 `first_audio_chunk_at_ms`。
2. `first_asr_partial_latency_ms` 改为从 `first_audio_chunk_at_ms` 到 ASR 服务返回第一段文本的耗时。
3. `实时 ASR 完成` 日志新增 `asr_total_latency_ms`，从首个音频 chunk 到 ASR 最终文本完成。
4. `StreamingSpeechRecognitionSession` 增加 `metrics()` 扩展面，方便 mock 或其他 ASR 实现输出一致的延迟指标。

### 指标口径

| 字段 | 起点 | 终点 |
| --- | --- | --- |
| `first_asr_partial_latency_ms` | 服务端收到眼镜第一个音频 chunk，并送入实时 ASR session | ASR 服务返回第一段文本 |
| `asr_total_latency_ms` | 服务端收到眼镜第一个音频 chunk，并送入实时 ASR session | ASR 服务返回最终完整文本 |

这两个指标不包含设备注册、语音会话打开、绑定等待和 `sensor.audio.segment.started` 到首个 `/ws_audio` chunk 之间的空档。

### 验证

```bash
PYTHONPATH=openaiglass-sdk/server-python \
uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v
```

---

<a id="iteration-v39"></a>
## iteration-v39：SDK v40 实时 ASR 切换官方 Recognition 接口

来源：`iteration-v39.md`


### 本轮目标

把实时 ASR 从原来的 Qwen Omni Realtime 转写路径切换到阿里云百炼官方实时语音识别接口，确保 `fun-asr-realtime` 按文档要求工作。

本轮对应对外 SDK 版本：`sdk-v40`。

### 问题判断

上一版虽然在服务端收到首个音频 chunk 时开始打点，但实时 ASR 实现仍使用 `dashscope.audio.qwen_omni.OmniRealtimeConversation`，并在音频结束后 `commit()`。这不是 `fun-asr-realtime` 官方文档中的 `Recognition.start()`、`send_audio_frame(...)`、`RecognitionCallback.on_event(...)` 链路，因此首段文本和总耗时容易表现为完全一致。

### 主要改动

1. `DashscopeRealtimeSpeechRecognitionSession` 改为使用 `dashscope.audio.asr.Recognition`。
2. 会话启动时调用 `Recognition.start()`。
3. 每个眼镜上行 PCM chunk 到达后立即调用 `send_audio_frame(...)`，不再先进入 SDK 自己的 ASR 发送队列。
4. 语音结束时调用 `Recognition.stop()`，等待 `on_complete()`。
5. `on_event(...)` 读取 `RecognitionResult.get_sentence()`，用 `end_time` 是否存在判断最终句子。
6. 默认 `VOICE_ASR_REALTIME_MODEL_NAME` 改为 `fun-asr-realtime`。

### 指标口径

| 字段 | 起点 | 终点 |
| --- | --- | --- |
| `first_asr_partial_latency_ms` | 服务端收到眼镜第一个音频 chunk，并调用 `send_audio_frame(...)` | `RecognitionCallback.on_event(...)` 收到第一段非空文本 |
| `asr_total_latency_ms` | 服务端收到眼镜第一个音频 chunk，并调用 `send_audio_frame(...)` | `RecognitionCallback.on_complete(...)` 收到完成事件 |

如果两者仍然完全一致，优先判断 ASR 服务是否只在句尾返回文本，而不是 SDK 仍走非实时链路。

### 验证

```bash
PYTHONPATH=openaiglass-sdk/server-python \
uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v
```

---

<a id="iteration-v40"></a>
## iteration-v40：SDK v41 实时 ASR 分段延迟诊断与 VAD 阈值

来源：`iteration-v40.md`


### 本轮目标

继续排查 `fun-asr-realtime` 首个 ASR 结果约 1 秒的问题，把实时 ASR 链路拆成更细的可观测阶段，并降低官方 Recognition 的句尾静音阈值，减少用户说完后的收尾等待。

本轮对应对外 SDK 版本：`sdk-v41`。

### 主要改动

1. `DashscopeRealtimeSpeechRecognitionSession` 记录实时 ASR 会话创建、连接打开、首个音频 chunk、首个 `send_audio_frame(...)` 返回、首个文本回调、stop 请求和 complete 回调的时间。
2. `实时 ASR 返回首个文本` 日志新增累计音频时长、已发帧数、已发字节数和 DashScope SDK 的 `get_first_package_delay()`。
3. `实时 ASR 完成` 日志新增 `recognition_open_latency_ms`、`session_start_to_first_audio_ms`、`first_audio_send_cost_ms`、`audio_ms_before_first_partial`、`dashscope_first_package_delay_ms`、`dashscope_last_package_delay_ms`、`stop_to_complete_ms`、`audio_frame_count` 和 `audio_bytes_sent`。
4. 新增 `VOICE_ASR_REALTIME_MAX_SENTENCE_SILENCE_MS` 配置，默认 `300`，传给官方 Recognition 的 `max_sentence_silence`。

### 排查方法

1. 如果 `recognition_open_latency_ms` 很高，瓶颈在实时 ASR WebSocket 建连或 SDK 启动。
2. 如果 `first_audio_send_cost_ms` 很高，瓶颈在本地 SDK 发帧或线程阻塞。
3. 如果 `audio_ms_before_first_partial` 接近 1000ms，说明 ASR 服务本身需要约 1 秒语音才返回首个文本。
4. 如果 `stop_to_complete_ms` 接近原来的 1300ms，说明句尾 VAD 静音阈值是主要问题，可继续调小 `VOICE_ASR_REALTIME_MAX_SENTENCE_SILENCE_MS`。

### 验证

```bash
PYTHONPATH=openaiglass-sdk/server-python \
uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v

PYTHONPATH=openaiglass-sdk/server-python \
uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_server_cli.py -q
```

---

<a id="iteration-v42"></a>
## iteration-v42：SDK v42 迭代记录

来源：`iteration-v42.md`


### 背景

2026-04-29 的真实链路日志显示，视觉问答在 ASR 完成后仍会先发起一轮“是否调用照片工具”的模型请求，再发起多模态图片解读请求。即使关闭思考模式，这一跳也会给首 token 增加约 2 秒延迟。

### 变更

1. 语音结束自动照片不再暴露为模型工具。
2. `UtterancePhotoStore` 增加一次性消费语义，只返回当前会话中已就绪、尚未使用的自动照片。
3. `AgentFacade.handle_turn(...)` 在进入 agent-core 前消费自动照片，把图片落盘为当前 turn 的 `MediaAssetRef`，并挂接到当前用户消息。
4. `OpenAIAgentLoopRunner` 组装当前 user message 时，如果 turn 中有图片资产，会发送 `content=[text, image_url...]` 的多模态输入；持久化的 `model_request` 会把图片 base64 脱敏为占位符。
5. 删除模型可见内置工具 `get_latest_utterance_photo`。
6. 默认 `AGENT_MODEL_NAME` 调整为 `qwen3.5-omni-plus`。

### 业务影响

1. 视觉问答类 Skill 不再需要把照片工具写入 `allowed_tools`。
2. 业务 Tool/Task 仍可通过 `DeviceGroupContext.capture_photo(...)` 主动控制设备抓拍；这不是模型默认可见工具。
3. 如果自动照片在当前 turn 进入 agent-core 前尚未上传完成，本轮会按纯文本输入执行；照片完成后会作为未使用照片进入后续 turn。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`

---

<a id="iteration-v43"></a>
## iteration-v43：SDK v43 迭代记录

来源：`iteration-v43.md`


### 背景

2026-04-29 的回放链路日志显示，服务端在首个模型 token 后约 700ms 已下发 `actuator.audio.play`，但 `glass-playback` 端直接播放模式仍要等 `/stream.wav` 完整下载到临时文件后才调用播放器，导致本地听到声音明显滞后。同时 `actuator.audio.started` 在真正下载或播放前就已上报，状态语义不准确。

### 变更

1. `glass-playback` 的 `play_and_auto_finish` 模式优先使用支持 stdin 的播放器流式播放。
2. 未配置播放器且本机存在 `ffplay` 时，默认使用 `ffplay -nodisp -autoexit -loglevel error -fflags nobuffer -flags low_delay -probesize 32 -analyzeduration 0 -f wav -i -`。
3. 配置 `player_command="ffplay ..."` 时，SDK 会自动补齐低延迟 stdin WAV 输入参数；显式包含 `{stdin}`、`-` 或 `pipe:0` 的命令会按配置使用。
4. 不支持 stdin 的播放器继续回退到“整段下载到临时文件后播放”，但会打印明确状态日志。
5. `play_and_auto_finish` 下的 `actuator.audio.started` 改为首段音频写入播放器后再上报。
6. 服务端新增 TTS 下行链路关键日志：`TTS 返回首段音频`、`下行播放请求已发送`、`播放流写出首段音频`。

### 业务影响

1. 业务回放配置如需直接听到下行语音，推荐安装 `ffplay` 并配置：

```json
"audio_play": {
  "mode": "play_and_auto_finish",
  "player_command": "ffplay -nodisp -autoexit -loglevel error"
}
```

2. 回放端 `actuator.audio.started` 不再表示“收到播放请求”，而表示“首段音频已经写入播放器”。
3. 排查下行语音延迟时，可以同时对比服务端和 glass-playback 的首包日志，区分 TTS、HTTP 播放流和本机播放器缓冲耗时。`本机播放器已启动，等待下行音频` 只表示播放器进程已启动，实际首包以 `收到第一段下行音频` 和 `下行音频已写入播放器` 为准。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_playback_config.py -q`
2. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`

---

<a id="iteration-v44"></a>
## iteration-v44：SDK v44 迭代记录

来源：`iteration-v44.md`


### 背景

修复 `glass-playback` 下行语音播放后，需要确认真实 ESP32 眼镜是否存在同类问题。代码检查显示，ESP32 固件当前不是“整段下载后播放”：它在收到 `actuator.audio.play` 后启动 `playback_stream_task`，打开 `/stream.wav` HTTP 流，读取 44 字节 WAV 头后按约 20ms 的 PCM 分片写入 I2S，并在首次写入扬声器后才上报 `actuator.audio.started`。

### 变更

1. 在 ESP32 固件播放入口增加 `准备启动播放流` 日志，打印 `stream_id` 和 `/stream.wav` 地址。
2. 在播放任务中增加 `播放流 HTTP 已打开` 日志，记录收到播放请求到 HTTP 打开的耗时。
3. 增加 `播放流 WAV 头已读取` 日志，记录收到播放请求到 WAV 头完成的耗时。
4. 增加 `播放流收到首段 PCM` 日志，记录首段真实 PCM 到达设备的耗时。
5. 增加 `播放流首段音频已写入扬声器` 日志，记录首段音频写入 I2S 的耗时。

### 结论

真实 ESP32 眼镜没有发现 `glass-playback` 原先那种“先完整下载再播放”的结构性问题。后续如果真机听感仍然延迟明显，应把 ESP32 日志和服务端 `TTS 返回首段音频`、`下行播放请求已发送`、`播放流写出首段音频` 对齐，定位延迟是在服务端 TTS、HTTP 首包、网络读取、I2S 写入还是功放实际出声阶段。

### 验证

1. `git diff --check -- openaiglass-sdk/glass-esp32/main/glass_main.c`

本机当前没有 `idf.py`，未执行 ESP-IDF 固件编译；需要在已安装 ESP-IDF 的环境中用 `uv run openaiglass.glass.start --app-root openaiglass-for-blind --sdk-root openaiglass-sdk --port '<串口>'` 完成构建、烧录和串口日志验证。

---

<a id="iteration-v45"></a>
## iteration-v45：SDK v45 迭代记录

来源：`iteration-v45.md`


### 背景

TTS 首段音频日志只显示了“首个模型文本增量到 SDK 播放队列首段音频”的总耗时，无法区分耗时发生在 CosyVoice WebSocket 建连、首次文本推送、百炼 TTS 服务首包回调，还是 SDK 回调后的重采样和入队。

### 变更

1. `DashscopeCosyVoiceTtsSession` 增加 TTS WebSocket 打开时间日志：`TTS WebSocket 已打开`。
2. 首次调用 `streaming_call(text_delta)` 后打印 `TTS 首次文本已推送`，包含本地调用耗时、session 创建到首次推送耗时、WebSocket 打开到首次推送耗时。
3. 首个 `on_data(...)` 音频回调到达时打印 `TTS 服务返回首段音频`，包含：
   - `tts_first_audio_latency_ms`：首次文本推送开始到 TTS 首段音频回调。
   - `tts_first_audio_after_call_return_ms`：首次 `streaming_call(...)` 返回后到 TTS 首段音频回调。
   - `session_create_to_first_audio_ms`：TTS session 创建到首段音频回调。
   - `websocket_open_to_first_audio_ms`：WebSocket 打开到首段音频回调。
   - `text_chars_before_first_audio` / `text_push_count_before_first_audio`：首段音频前已经推给 TTS 的文本量。
4. 原有 `TTS 返回首段音频` 保留，继续表示首段音频进入 SDK 后完成重采样并放入播放队列的时间。

### 结论

当前 SDK 本地 TTS 热路径没有图片、上下文或模型消息级重处理。首包耗时需要通过 `TTS 服务返回首段音频` 与 `TTS 返回首段音频` 对比判断：如果前者已经很大，耗时主要在 TTS 服务首包；如果两者差距大，才说明 SDK 回调后处理或重采样入队存在问题。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
2. `git diff --check -- openaiglass-sdk/server-python/runtime/voice_runtime.py`

---

<a id="iteration-v46"></a>
## iteration-v46：SDK v46 迭代记录

来源：`iteration-v46.md`


### 背景

TTS 首包诊断显示，首次 CosyVoice WebSocket 建连和首次 `streaming_call(...)` 会带来约数百毫秒耗时。此前 SDK 在首个模型 token 到达后才创建 TTS 会话，导致这段耗时叠加在首听延迟上。

### 变更

1. `VoiceRuntime` 在调用 `AgentFacade.handle_turn(...)` 前创建最终回复的 `ReplySynthesisContext` 和流式 TTS session。
2. 新增 `TTS 预热已启动` 日志，标记 TTS WebSocket 预热开始，并携带本轮回复 `stream_id`。
3. 首个模型文本增量到达时，直接复用已预热 TTS session 推送文本。
4. 如果预热 session 因模型首 token 或工具链路耗时过长而失效，首次推送失败时会记录 `TTS 预热会话推送失败，重建后重试`，然后重建 session 并重试当前文本。
5. 如果 Agent 没有返回流式文本增量，SDK 会把最终回复文本推入已经预热的 TTS session，不再重新走一条未预热 TTS 路径。

### 预期效果

大模型首 token 等待期间可以并行完成 CosyVoice WebSocket 建连，降低首 token 到首段 TTS 音频之间的可见延迟。实际收益取决于模型首 token 耗时和百炼 TTS 服务端是否会等待足够文本后才返回首段音频。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
3. 使用本地假 `VoiceModelClient` / `AgentFacade` 执行 `_run_model_pipeline(...)`，确认 TTS session 在 Agent `handle_turn(...)` 前创建，首个文本 delta 复用同一 session。

---

<a id="iteration-v47"></a>
## iteration-v47：SDK v47 迭代记录

来源：`iteration-v47.md`


### 背景

`sdk-v46` 已经把最终回复的 TTS session 创建提前到 Agent 请求之前，但真实联调日志显示 DashScope `SpeechSynthesizer` 仍在首次 `streaming_call(...)` 时才打开 WebSocket 并启动流式任务。因此 `TTS WebSocket 已打开` 仍出现在大模型首 token 之后，首次文本提交还会承担建连和 run-task 握手耗时。

### 变更

1. `DashscopeCosyVoiceTtsSession` 创建后会启动后台预热线程。
2. 预热线程提前启动 CosyVoice 流式任务，让 WebSocket 建连和 run-task 握手与 Agent 首 token 等待并行发生。
3. 首个文本增量到达时，SDK 会优先复用已经预启动的流式任务，只提交文本。
4. 新增 `TTS 预热流已启动` 日志，携带 `prewarm_stream_cost_ms`、`session_create_to_prewarm_stream_ms` 和 `session_create_to_open_ms`。
5. 如果预热失败，SDK 退化为首次文本触发建连；如果首次推送失败，上层仍会记录 `TTS 预热会话推送失败，重建后重试` 并重建 TTS session。

### 预期效果

正常情况下，服务端日志中 `TTS WebSocket 已打开` 和 `TTS 预热流已启动` 应出现在 `大模型返回首个 token` 之前。首个文本到来后的 `first_streaming_call_cost_ms` 应明显下降，剩余 `tts_first_audio_after_call_return_ms` 主要反映百炼 TTS 服务在收到文本后返回首段音频的耗时。

### 风险和边界

本轮为了压低首包延迟，使用了 DashScope Python SDK 的内部流启动能力。SDK 已保留失败退化和重建重试，但如果后续 DashScope SDK 改动内部方法名，预热会退化为首次文本触发，不会阻断语音主链路。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
3. 使用真实服务端观察 TTS 日志顺序：`TTS 预热已启动`、`TTS WebSocket 已打开`、`TTS 预热流已启动` 应在首个模型文本增量之前出现。

---

<a id="iteration-v48"></a>
## iteration-v48：SDK v48 迭代记录

来源：`iteration-v48.md`


### 背景

功能开发团队提出 SDK 应支持 Agent 长期记忆：Agent 需要主动记住用户行为习惯、基本信息和稳定偏好，也要允许用户通过自然语言要求新增或删除记忆。该能力跨业务 Tool、Task 和 Skill，应放在 SDK agent-core 中统一实现。

### 调研结论

本轮参考了 Letta / MemGPT、Mem0、LangGraph / LangChain 和 Zep / Graphiti 的公开方案。共同点是：记忆不应只是长聊天记录拼接，而应有持久化存储、作用域隔离、检索、删除和 Agent 可主动维护的工具面。

当前 SDK 第一版选择轻量本地实现，不直接引入外部服务：

1. 用 JSON 文件保存可审计记忆。
2. 先提供稳定接口和 Tool 语义。
3. 后续可在同一接口下替换为向量库、图数据库或外部记忆服务。

### 变更

1. 新增 `agent_core.memory` 模块：
   - `AgentMemoryRecord`
   - `AgentMemoryRuntime`
   - `InMemoryAgentMemoryStore`
   - `JsonFileAgentMemoryStore`
2. 新增模型可见工具 `manage_memory`，支持 `add/search/list/delete`。
3. `ToolRegistry` 支持注入 `AgentMemoryRuntime`，并在启用时自动暴露 `manage_memory`。
4. `AgentTurnRuntimeFactory` 每轮按当前 `device_id` 检索相关记忆，并注入系统提示词。
5. `model_request` 新增 `memory_prompt_fragment`，方便联调和回归产物核对。
6. `ServerSettings` 新增：
   - `AGENT_MEMORY_ENABLED`
   - `AGENT_MEMORY_STORE_PATH`
   - `AGENT_MEMORY_MAX_PROMPT_ITEMS`
7. `build_agent_facade_from_sdk(...)` 和默认服务端门面会按配置创建记忆运行时。
8. 更新 `SDK安装与能力开发指南.md` 到 `sdk-v48`。
9. 新增设计文档 `structure-design/Agent长期记忆设计.md`。

### 业务开发边界

业务能力不要自建长期记忆表、记忆 Tool 或提示词拼接逻辑。稳定偏好、基本信息和行为习惯交给 SDK 的 `manage_memory`；当前任务阶段、临时观测和短时状态继续放在 Task 上下文或当前会话。

### 风险和边界

1. 当前检索是轻量关键词匹配，不是语义向量检索。
2. 当前作用域按 `device_id` 隔离，尚未打通用户级和账号级记忆。
3. 当前支持新增、查询、列出和删除，尚未支持精确 update。
4. Agent 主动写入仍依赖模型按提示调用 `manage_memory`，后续可增加独立记忆抽取器和审计策略。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`

本轮未执行设备级回放。记忆能力第一版主要影响 agent-core 工具面和模型请求装配；后续与真实语音联调时应观察服务端 `model_request.memory_prompt_fragment`、`manage_memory` Tool trace 和 `runs/memory/agent_memories.json`。

---

<a id="iteration-v49"></a>
## iteration-v49：SDK v49 迭代记录

来源：`iteration-v49.md`


### 背景

`sdk-v47` 已经把 CosyVoice TTS 建连和流式任务启动前移，首个模型 token 到首段 TTS 音频的耗时主要剩在独立 TTS 服务首包。为了继续压低首听延迟，本轮新增 Qwen Omni Realtime 语音直出分支，让模型在同一次全模态调用中直接返回音频。

### 变更

1. 新增服务端配置：
   - `VOICE_REPLY_MODE=agent_tts|omni_realtime`
   - `VOICE_OMNI_REALTIME_MODEL_NAME`
   - `VOICE_OMNI_REALTIME_URL`
   - `VOICE_OMNI_PHOTO_WAIT_MS`
2. 默认 `agent_tts` 分支保持现有 ASR + agent-core + CosyVoice 流式 TTS，不删除任何已有链路。
3. 新增 `DashscopeOmniRealtimeReplyClient`，通过 DashScope `OmniRealtimeConversation`：
   - 关闭服务端 VAD，复用 SDK 当前语音段边界。
   - 发送本轮 16k PCM 音频和可选自动照片。
   - 监听 `response.audio.delta`，将模型音频分片直接写入现有播放流。
4. `VoiceRuntime` 在 `VOICE_REPLY_MODE=omni_realtime` 时绕过独立 ASR、agent-core 和独立 TTS，直接执行 Omni Realtime 语音直出。
5. 新增日志：
   - `Omni Realtime 请求已发送`
   - `Omni Realtime 返回首个文本`
   - `Omni Realtime 返回首段音频`
   - `Omni Realtime 最终回复`

### 边界

1. `omni_realtime` 当前用于低延迟普通问答和视觉问答，不执行 SDK Tool、Task、Skill 或长期记忆工具。
2. 需要导航、计时器、找物体、红绿灯等工具编排的能力时，应继续使用默认 `VOICE_REPLY_MODE=agent_tts`。
3. 本轮仍按半双工语音段边界提交 Omni 请求，没有把眼镜上行音频实时透传给 Omni Realtime。后续若要进一步降低 ASR/提交延迟，需要把 `/ws_audio` 的音频分片直接桥接到 Omni Realtime。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
3. 单测新增假 Omni Realtime 会话，验证 `response.audio.delta` 能直接转成播放音频分片。

---

<a id="iteration-v50"></a>
## iteration-v50：SDK v50 迭代记录

来源：`iteration-v50.md`


### 背景

`sdk-v48` 已经提供统一长期记忆池，但它每轮会直接把检索到的完整记忆注入 system prompt，查询和管理也都塞在 `manage_memory` 里。为了降低上下文污染，并让长内容记忆可以按需读取，本轮按基本信息和个性化信息两类重新收敛记忆模型。

### 变更

1. `AgentMemoryRecord` 新增信息类型模型：
   - `memory_type=basic|personalized`
   - `topic`
   - `content`
2. 基本信息用于姓名、年龄、性别等短小稳定信息，每轮完整注入 system prompt。
3. 个性化信息用于住址、电话、爱好、习惯、任务设置等长内容或可能变化的信息，每轮只注入主题。
4. 新增 `memory_search` 工具：
   - 入参为 `topic` 或 `topics`。
   - 只按记忆主题读取详细内容。
   - 不负责新增、更新或删除。
5. `manage_memory` 改为只负责新增、更新和删除，不再承担搜索或列表功能。
6. 新增记忆管理子 Agent 抽象：
   - `MemoryManagementAgent`
   - `LlmMemoryManagementAgent`
   - `HeuristicMemoryManagementAgent`
7. 真实服务端默认使用 `LlmMemoryManagementAgent`，模型不可用时退回确定性 fallback。
8. `model_request.memory_prompt_fragment` 改为保存基本信息正文和个性化信息主题目录。
9. 更新 `SDK安装与能力开发指南.md` 到 `sdk-v50`。

### 边界

1. 当前个性化信息详情查询仍是主题精确匹配，不是语义召回。
2. 当前记忆仍按 `device_id` 隔离，用户级和账号级作用域留到后续迭代。
3. 记忆管理子 Agent 负责生成结构化计划，真正落盘仍由 SDK 运行时执行。
4. `VOICE_REPLY_MODE=omni_realtime` 会绕过 agent-core，因此不会使用本轮长期记忆工具。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python uv run python -m py_compile openaiglass-sdk/server-python/agent_core/memory/*.py openaiglass-sdk/server-python/agent_core/tools/builtins/manage_memory.py openaiglass-sdk/server-python/agent_core/tools/builtins/memory_search.py openaiglass-sdk/server-python/agent_core/tools/registry.py openaiglass-sdk/server-python/agent_core/runtime/runner.py openaiglass-sdk/server-python/openaiglasses/server.py`

---

<a id="iteration-v51"></a>
## iteration-v51：SDK 迭代记录：语音输入模式与下行音频日志口径

来源：`iteration-v51.md`


对应对外 SDK 版本：`sdk-v51`。

### 背景

`sdk-v49` 引入 `VOICE_REPLY_MODE=omni_realtime` 后，服务端已经可以绕过独立 ASR、agent-core 和 CosyVoice TTS，把语音与自动照片直接提交给 Qwen Omni Realtime。但实际联调日志暴露两个问题：

1. 共用播放流日志仍写成 `TTS 返回首段音频`，在 Omni Realtime 分支会误导排障。
2. 是否启用独立 ASR 只隐含在 `VOICE_REPLY_MODE` 中，缺少显式配置入口。

### 本轮变更

1. 新增 `VOICE_INPUT_MODE=auto|asr_text|raw_audio`：
   - `auto` 为默认值。
   - `VOICE_REPLY_MODE=agent_tts` 时实际等价于 `asr_text`。
   - `VOICE_REPLY_MODE=omni_realtime` 时实际等价于 `raw_audio`。
2. 增加配置校验：
   - `agent_tts + raw_audio` 会阻止启动，因为当前 Agent + TTS 分支需要文本输入。
   - `omni_realtime + asr_text` 会阻止启动，因为当前 Omni Realtime 分支直接消费原始音频。
3. `VoiceRuntime.on_segment_started(...)` 改为按实际语音输入模式决定是否启动实时 ASR。
4. 语音链路起始日志新增 `voice_input_mode`、`reply_mode`、`reply_model`，并在跳过独立 ASR 时显示 `asr_model=<skipped>`。
5. 共用播放层首包日志改为 `下行音频源返回首段音频`，并携带 `audio_source=tts|omni_realtime`。
6. `下行播放请求已发送` 和 `播放流写出首段音频` 日志改用 `source_audio_to_*` 字段，不再写死为 `tts_audio_to_*`。

### 使用说明

默认配置不需要修改：

```env
VOICE_REPLY_MODE="agent_tts"
VOICE_INPUT_MODE="auto"
```

低延迟 Omni Realtime 直出：

```env
VOICE_REPLY_MODE="omni_realtime"
VOICE_INPUT_MODE="auto"
VOICE_OMNI_REALTIME_MODEL_NAME="qwen3.5-omni-plus-realtime"
```

如果当前模型不支持语音输入，应使用 Agent + TTS 分支：

```env
VOICE_REPLY_MODE="agent_tts"
VOICE_INPUT_MODE="asr_text"
```

### 边界

`VOICE_INPUT_MODE` 目前只控制服务端是否启动独立 ASR。`omni_realtime` 分支仍会使用 Omni Realtime 的原始音频输入能力，并通过模型返回的转写文本记录本轮 transcript；`sdk-v75` 起该分支已支持 SDK Tool 调用和工具结果回填。

---

<a id="iteration-v52"></a>
## iteration-v52：SDK 迭代记录：Omni 默认链路与说话期间预推音频

来源：`iteration-v52.md`


对应对外 SDK 版本：`sdk-v52`。

### 背景

`sdk-v51` 已经明确 `VOICE_INPUT_MODE`，但 Omni Realtime 分支仍在 `sensor.audio.segment.finished` 之后才建连、追加整段音频、追加图片并提交请求。真实日志显示用户说完后到首段模型音频之间仍有明显串行耗时，其中一部分来自建连和整段音频提交。

### 本轮变更

1. 默认语音回复模式从 `agent_tts` 切换为 `omni_realtime`：
   - 默认 `VOICE_INPUT_MODE=auto`，因此实际输入模式为 `raw_audio`。
   - 需要 Tool、Task、Skill、MCP 或长期记忆编排时，开发者应显式配置 `VOICE_REPLY_MODE=agent_tts`。
2. `VoiceRuntime` 在 `sensor.audio.segment.started` 时预启动 Omni Realtime：
   - 预先建立 WebSocket。
   - 预先创建下行播放上下文。
   - 建连失败时只记录 DEBUG，结束阶段回退到普通提交路径。
3. `/ws_audio` 每个音频 chunk 到达时，除写入本地 `SegmentBuffer` 外，也同步追加到 Omni Realtime 会话。
4. `sensor.audio.segment.finished` 后只执行：
   - 等待自动照片。
   - 追加图片。
   - `commit()` 和 `create_response()`。
   - 等待 `response.audio.delta` 并流式下发播放。
5. 新增日志：
   - `Omni Realtime 预连接已建立`
   - `Omni Realtime 首段上行音频已推送`
   - `Omni Realtime 请求已提交`

### 延迟预期

这轮优化主要去掉用户说完后的建连和整段音频一次性追加开销。剩余首听延迟主要来自：

1. `VOICE_OMNI_PHOTO_WAIT_MS` 等待自动照片的时间。
2. `commit/create_response` 后 Omni 服务首段音频返回时间。
3. HTTP 播放流首包和端侧播放器写入时间。

### 边界

当前仍按半双工语音段边界提交响应：服务端不会在用户尚未说完时请求模型开始回答。真正全双工响应、用户插话打断和服务端 VAD 模式仍需要端侧 AEC/VAD 能力继续配合。

---

<a id="iteration-v53"></a>
## iteration-v53：SDK 迭代记录：glass-playback 本机麦克风输入

来源：`iteration-v53.md`


对应对外 SDK 版本：`sdk-v53`。

### 背景

`glass-playback` 原本只能通过 `sensors.trigger_audio.path` 回放固定 WAV 文件。稳定回归需要固定音频资产，但日常联调时开发者经常只想直接对着开发机麦克风说一句话，验证真实服务端、Omni/ASR、下行播放和设备事件链路。

### 本轮变更

1. `sensors.trigger_audio` 新增 `source` 字段：
   - `file`：默认值，继续读取 WAV 文件，保持原有回归语义。
   - `microphone`：采集开发机真实麦克风。
2. 麦克风模式支持配置：
   - `sample_rate_hz`
   - `channels`
   - `chunk_ms`
   - `duration_ms`
   - `device`
3. `glass-playback` 在麦克风模式下仍发送同一套真实眼镜协议：
   - `sensor.audio.segment.started`
   - `/ws_audio` 的 `MediaFrame(audio_chunk)`
   - `sensor.audio.segment.finished`
4. 命令行状态日志会标明 `source=microphone`、分片数、字节数和录音时长。

### 边界

麦克风模式是本地手动调试能力，不替代稳定自动化回归。它不做本机 VAD、唤醒词检测或自动停止，当前按 `duration_ms` 固定录音。正式验收仍应使用 WAV 文件资产，保证每次回放输入一致。

### 依赖

麦克风采集使用可选依赖 `sounddevice`。如果环境缺少该依赖，运行时会给出明确错误；开发者可执行 `uv pip install sounddevice`，macOS 如遇 PortAudio 问题可先执行 `brew install portaudio`。

---

<a id="iteration-v54"></a>
## iteration-v54：SDK 迭代记录：Omni 语义实时连续对话接线

来源：`iteration-v54.md`


对应对外 SDK 版本：`sdk-v54`。

### 背景

真实语音对话如果每轮都要求唤醒词，室外和连续追问场景体验很差。当前 `sdk-v52` 的 Omni Realtime 已经把建连和音频上行前移到用户说话期间，但仍按一次语音段结束后提交模型响应，不具备真正连续对话的 turn detection 和插话事件桥。

本轮选择“方案二”：一次唤醒后进入连续对话窗口，由 Qwen Omni Realtime 的 `semantic_vad` 负责判断用户 turn，并以真实 `glass-esp32` 为最终落地点。

### 本轮变更

1. 新增设计文档 [Omni语义实时连续对话设计](../structure-design/Omni语义实时连续对话设计.md)，明确唤醒、接收、结束、等待、插话和嘈杂环境边界。
2. `ServerSettings` 新增连续对话配置：
   - `VOICE_CONVERSATION_MODE`
   - `VOICE_REALTIME_TURN_DETECTION`
   - `VOICE_REALTIME_SEMANTIC_VAD_THRESHOLD`
   - `VOICE_REALTIME_SILENCE_DURATION_MS`
   - `VOICE_REALTIME_PREFIX_PADDING_MS`
3. `voice.realtime.session.open` 的 `input` payload 新增 `conversation_mode` 和 `turn_detection`，让真实眼镜可以知道服务端期望的 turn detection 归属。
4. Omni Realtime 会话创建时按配置传入官方 SDK 的 turn detection 参数。
5. 补充单元测试，覆盖配置校验、实时语音 open payload 和 Omni 会话参数。
6. 更新 `local_server.env.example` 和业务开发指南，说明当前默认仍是稳定 `segment_turn`，`realtime_semantic_vad` 是实验模式。

### 当前边界

本轮是方案二第一阶段，不是完整生产级连续对话：

1. `VOICE_CONVERSATION_MODE=segment_turn` 仍是默认稳定模式。
2. `realtime_semantic_vad` 当前完成配置、协议和 Omni 会话参数接线，服务端连续事件桥和 `glass-esp32` 固件配合仍需后续迭代。
3. Omni Realtime 直出分支不执行 SDK Tool、Task、Skill、MCP；需要业务编排时仍应使用 `VOICE_REPLY_MODE=agent_tts`。
4. `semantic_vad` 不等于声纹识别，室外旁人说话仍需要真实眼镜端的近场拾音、AEC、VAD 阈值和退出策略配合。

### 后续计划

1. 在服务端增加 Omni 连续会话管理器，把 `speech_started`、`speech_stopped`、`response.audio.delta`、`response.done` 等事件转换为 SDK 内部事件。
2. 在播放中检测用户插话时调用 Omni `cancel_response`，并通过播放仲裁下发 `actuator.audio.interrupt`。
3. 为 `glass-esp32` 增加一次唤醒后的连续收音窗口、播放期间收音、按键退出、长静音退出和首包播放日志。
4. 为 `glass-playback` 增加多 turn 时间线回放，用于协议和回归验收。

---

<a id="iteration-v55"></a>
## iteration-v55：SDK 迭代记录：Omni semantic_vad 默认连续对话

来源：`iteration-v55.md`


对应对外 SDK 版本：`sdk-v55`。

### 背景

`sdk-v54` 已完成 Omni `semantic_vad` 的配置、协议和会话参数接线，但默认仍是 `segment_turn`。本轮继续把方案二推进到默认链路：用户一次 WakeNet 唤醒后，真实 `glass-esp32` 打开短时间连续对话窗口，服务端把上行音频和本轮自动照片交给 Omni Realtime，由 Omni 自动判断 turn 并直接返回语音。

官方依据见 Qwen-Omni-Realtime 文档：该模型支持 WebSocket 实时会话、流式音频与图片输入、`semantic_vad` turn detection，以及 `response.audio.delta` 音频增量输出。

### 本轮变更

1. 默认 `VOICE_CONVERSATION_MODE` 改为 `realtime_semantic_vad`，保留 `segment_turn` 作为回退模式。
2. 服务端在 `sensor.audio.segment.started` 时提前启动本轮自动抓拍，照片就绪后等待至少一段音频已追加到 Omni，再异步追加图片，避免官方接口报错“append image before append audio”。
3. `OmniRealtimeStreamingSession.finish(...)` 在 semantic_vad 模式下不再调用 `commit()` 和 `create_response(...)`，只等待 Omni 自动提交和自动响应。
4. Omni 事件回调补充 `speech_started`、`speech_stopped`、`input_audio_buffer.committed`、`response.created` 等观测点，便于区分模型等待、VAD 提交和首段音频延迟。
5. `glass-esp32` 在收到服务端声明的 `realtime_semantic_vad` 后，向服务端上报连续对话能力；一次 WakeNet 命中后打开 30 秒连续对话窗口，后续可由本地 VAD 直接触发下一段语音。
6. 更新 SDK 开发指南、设计文档、`local_server.env.example` 和 `sdk-version`。

### 当前边界

1. ESP32 固件当前仍上报 `aec=false`、`barge_in=false`，因此播放期间保持半双工，避免助手声音被麦克风回灌给模型。
2. 本轮支持“助手播完后的自然追问”，尚不支持播放中自然插话。播放中插话需要端侧 AEC 或可靠回声抑制后再开启。
3. Omni Realtime 直出分支仍不执行 SDK Tool、Task、Skill、MCP；需要业务编排时应使用 `VOICE_REPLY_MODE=agent_tts` 或后续接入 Omni Function Calling。
4. `glass-playback` 仍只用于协议回放和验收，不代表真实麦克风、AEC、旁人说话过滤或室外噪声效果。

### 验证

1. 单元测试覆盖 semantic_vad 模式下不手动 commit/create response，确保等待 Omni 自动响应。
2. 单元测试覆盖默认配置、实时语音协议 payload 和旧 `segment_turn` 分支的回归。
3. ESP32 固件本轮做源码级实现，实际 AEC、播放中插话和嘈杂环境效果需要真机联调继续验证。

### 后续计划

1. 评估并接入 ESP32 端 AEC 或同等回声抑制能力。
2. 在具备 AEC 后实现播放中插话：端侧触发 interrupt，服务端 cancel Omni response 并中断下行播放。
3. 增加多 turn `glass-playback` 时间线回放，用于协议和回归验收。
4. 为室外嘈杂环境增加 VAD profile 和误触发保护策略。

---

<a id="iteration-v57"></a>
## iteration-v57：SDK 迭代记录：ESP32 首次唤醒轻提示音

来源：`iteration-v57.md`


对应对外 SDK 版本：`sdk-v57`。

### 背景

连续对话链路下，真实眼镜在首次 WakeNet 命中后会进入一段连续对话窗口。用户需要一个轻微、短暂的本地反馈，确认唤醒词已经被端侧识别成功，避免不知道是否可以开始说话。

### 本轮变更

1. `glass-esp32` 增加本地唤醒提示音，只在 `WakeNet detected` 分支播放。
2. 连续对话窗口内由本地 VAD 触发的新语音段不会重复播放提示音，避免每轮追问都打扰用户。
3. 提示音由端侧直接生成短 PCM 写入扬声器 I2S，不走服务端播放链路，不产生额外网络延迟。
4. 提示音和扬声器预装静音帧使用堆内存缓冲，不占用 `sr_pipeline_task` 栈，避免 WakeNet 命中后栈溢出重启。
5. 新增 Kconfig 配置：
   - `CONFIG_GLASS_WAKE_PROMPT_TONE_ENABLE`
   - `CONFIG_GLASS_WAKE_PROMPT_TONE_DURATION_MS`
   - `CONFIG_GLASS_WAKE_PROMPT_TONE_FREQ_HZ`
   - `CONFIG_GLASS_WAKE_PROMPT_TONE_GAIN_PERMILLE`

### 验证

1. 单元测试静态检查提示音只挂在 WakeNet 分支，不挂在连续 VAD 分支。
2. 单元测试检查提示音不依赖 AEC 参考缓冲，避免回退方案 A 时重新引入播放中插话链路。
3. 单元测试检查提示音缓冲不使用 SR 任务栈上的大数组。
4. 真机仍需要听感验证：提示音应短促、轻微，不应盖住用户开始说话的前几个字。

### 风险和后续

1. 如果提示音仍被 Omni 识别为输入噪声，应继续降低 `CONFIG_GLASS_WAKE_PROMPT_TONE_GAIN_PERMILLE` 或缩短时长。
2. 后续可按产品形态增加不同状态提示音，例如进入连续对话、退出连续对话、网络断开，但要避免提示音过多造成干扰。

---

<a id="iteration-v64"></a>
## iteration-v64：SDK 迭代记录：回退播放中自然插话试验

来源：`iteration-v64.md`


对应对外 SDK 版本：`sdk-v64`。

### 背景

ESP32-S3 上的 AEC 播放中自然插话试验会在真实播放期间把扬声器声音误判成用户语音，导致下行音频刚播放就被反复打断，并不断创建新的候选语音段。该问题已经影响主链路稳定性，因此本轮先回退到方案 A。

### 本轮变更

1. 回退 ESP32 AEC 播放参考通道、播放中 VAD 候选段、Omni 语义确认插话和相关兜底重连逻辑。
2. ESP32 实时语音能力继续上报 `aec=false`、`barge_in=false`、`output_cancel=false`。
3. 播放期间关闭本地唤醒/连续 VAD 门控，不启动新的 `sensor.audio.segment.started`。
4. 播放结束后刷新连续对话窗口，用户仍可直接继续说下一句，不需要重复唤醒词。
5. 保留首次 WakeNet 唤醒轻提示音，但提示音不再依赖 AEC 参考缓冲。
6. 更新 SDK 开发指南和 Omni 连续对话设计文档，明确当前真机默认形态是方案 A。
7. 保留 `glass-playback` 和半双工有限语音段的稳定性兜底：当 Omni `semantic_vad` 在 `sensor.audio.segment.finished` 后仍未自动提交时，服务端关闭该实时会话并改用 `segment_turn` 重连提交完整 PCM，避免一直等待到 45 秒超时。

### 验证

1. 单元测试静态检查 ESP32 固件不包含 AEC 配置、播放中候选段字段和播放中 VAD 候选日志。
2. 单元测试静态检查首次 WakeNet 唤醒提示音仍只挂在唤醒词分支。
3. 真机联调观察点：播放期间不应再出现“播放中 VAD 触发候选语音段”，服务端也不应收到带 `started_during_playback` 的语音段。
4. 回放联调观察点：如果日志出现“Omni semantic_vad 未自动提交，准备改用 segment_turn 重连兜底”，后续应继续出现“Omni Realtime 返回首段音频”和 `actuator.audio.play`，不应停在“Omni semantic_vad 等待自动响应”直到超时。

### 后续

播放中自然插话暂不作为默认能力。后续如果继续尝试，需要先解决端侧回声抑制的可靠性，并用真实眼镜验证不会把助手自己的声音回灌成用户输入。

---

<a id="iteration-v65"></a>
## iteration-v65：SDK 迭代记录：Agent 长期记忆自然语言更新删除增强

来源：`iteration-v65.md`


对应对外 SDK 版本：`sdk-v65`。

### 背景

功能开发团队需要 Agent 能主动记住用户的稳定偏好、基本信息和行为习惯，也要允许用户通过自然语言主动新增、更新或删除记忆。SDK 已在 `sdk-v50` 提供长期记忆、`memory_search` 和 `manage_memory`，但无模型兜底路径对“忘掉刚才那条记忆”“删除我的导航偏好”等自然语言控制不够稳。

本轮继续沿用 SDK 自研轻量记忆运行时，不引入外部依赖。调研结论仍保持：Mem0、Letta / MemGPT、LangGraph 和 Zep / Graphiti 都说明长期记忆应有明确存储、检索、更新和删除语义；当前 SDK 先稳定接口和可回放行为，后续再评估向量库、图数据库或外部记忆服务。

### 本轮变更

1. `HeuristicMemoryManagementAgent` 增强中文删除指令解析，支持从“删除我的导航偏好”“忘掉刚才那条记忆”等表达中提取目标。
2. `AgentMemoryRuntime.manage_memory(...)` 将 `add` 和 `update` 分开执行；`update` 会优先复用 `memory_id`，避免更新后引用失效。
3. 删除流程增加多级兜底：先按 `memory_id`，再按主题和类型，再按主题不限定类型，最后按原始自然语言查询匹配。
4. 记忆存储增加写入顺序记录；当多条记忆在同一毫秒写入时，“最近一条”仍能稳定指向最后写入的记录。
5. 新增单元测试覆盖无模型环境下的自然语言删除、最近记忆删除和按 `memory_id` 更新。
6. 更新 `SDK安装与能力开发指南.md`、`Agent长期记忆设计.md` 和 `sdk-version`。

### 业务开发边界

业务能力仍不应自建记忆表、记忆 Tool 或额外提示词拼接逻辑。用户稳定偏好、基本信息和行为习惯交给 SDK 的 `manage_memory`；业务当前任务阶段、临时状态和回放观测仍放在 Task 上下文或本轮会话里。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`

本轮未执行设备级回放。改动集中在 agent-core 记忆运行时和模型工具语义，后续真机或回放验证时应重点观察 `manage_memory` Tool trace、`model_request.memory_prompt_fragment` 和 `runs/memory/agent_memories.json`。

---

<a id="iteration-v66"></a>
## iteration-v66：SDK 迭代记录：Agent 长期记忆维护语义收敛

来源：`iteration-v66.md`


对应对外 SDK 版本：`sdk-v66`。

### 背景

上一版长期记忆虽然支持新增、更新和删除，但仍把过多结构化字段暴露给主 Agent，并保留了无模型启发式兜底。长期记忆维护本质上需要理解用户自然语言、聊天上下文和已有记忆之间的关系，应由专门的 MemoryAgent 决定具体动作，而不是让主 Agent 拼装 CRUD 参数。

### 本轮变更

1. 移除 `HeuristicMemoryManagementAgent`，模型不可用时记忆维护明确失败，不做规则降级。
2. `ManageMemoryInput` 收敛为 `query` 和 `memory_context` 两个字段。
3. `MemoryOperationRequest` 不再包含 `operation/topic/content/memory_id/category/source` 等主 Agent 不应关心的字段。
4. 新增 `MemoryOperationAction`，`MemoryOperationPlan` 改为动作列表，支持一次请求内串行执行多个动作，例如先删除再新增。
5. `memory_id` 只在 MemoryAgent 与 `AgentMemoryRuntime` 内部使用；`manage_memory` 和 `memory_search` 返回给主 Agent 的结果不再包含内部编号。
6. 记忆记录移除 `category` 字段；`reason` 不再作为计划字段。
7. `memory_search` 改为按主题读取记忆详情，未命中时返回文本反馈“没有找到匹配的记忆”。
8. `AgentMemoryRuntime` 默认使用本地 JSON 文件存储，真实服务端继续通过 `AGENT_MEMORY_STORE_PATH` 配置路径。
9. 更新 `Agent长期记忆设计.md`、`SDK安装与能力开发指南.md` 和 `sdk-version`。

### 业务开发边界

业务能力仍不应自建记忆表、记忆 Tool 或额外提示词拼接逻辑。主 Agent 只需要在用户表达记住、更新、忘记、删除等意图时调用 `manage_memory(query, memory_context)`；是否新增、更新、删除、拆成几步动作，全部由 SDK MemoryAgent 决定。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`

本轮未执行设备级回放。改动集中在 agent-core 记忆工具入参、内部动作计划和公开返回语义，后续真实链路验证时应重点观察 `manage_memory` Tool trace、`model_request.memory_prompt_fragment` 和 `runs/memory/agent_memories.json`。

---

<a id="iteration-v67"></a>
## iteration-v67：SDK 迭代记录：长期记忆分类描述统一

来源：`iteration-v67.md`


对应对外 SDK 版本：`sdk-v67`。

### 背景

上一版长期记忆已经收敛为 MemoryAgent 内部动作计划，但分类描述容易让开发者误解为存储形态或缓存层级。本轮把长期记忆的对外描述统一为“基本信息”和“个性化信息”，让业务开发者更容易按内容语义判断应该保存什么。

### 本轮变更

1. `MemoryType` 只保留 `basic` 和 `personalized`：
   - `basic`：基本信息，例如姓名、年龄、性别、称呼等短小稳定信息。
   - `personalized`：个性化信息，例如住址、电话、爱好、习惯、任务设置等较长或可能变化的信息。
2. `LlmMemoryManagementAgent` 提示词和动作计划字段说明统一使用 `memory_type(basic/personalized)`。
3. `AgentMemoryRuntime.build_prompt_fragment(...)` 改为完整注入基本信息，只注入个性化信息主题。
4. `memory_search` 的输入说明改为按“记忆主题”查询，不再限定某一类记忆。
5. 本地 JSON 文件缺少 `memory_type` 时默认按 `personalized` 加载；不保留旧分类值兼容分支。
6. 更新长期记忆设计文档、能力开发指南、SDK 版本记录和相关单元测试。

### 业务开发边界

业务能力仍不应自建记忆表、记忆 Tool 或额外提示词拼接逻辑。主 Agent 只需要在用户表达记住、更新、忘记、删除等意图时调用 `manage_memory(query, memory_context)`；是否保存为基本信息或个性化信息，由 SDK MemoryAgent 根据自然语言、聊天上下文和已有记忆判断。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests -q`

本轮未执行设备级回放。改动集中在 agent-core 记忆分类语义、提示注入和工具说明，后续真实链路验证时应重点观察 `model_request.memory_prompt_fragment`、`manage_memory` Tool trace 和 `runs/memory/agent_memories.json`。

---

<a id="iteration-v68"></a>
## iteration-v68：SDK 迭代记录：主 Agent 主动记忆提示补强

来源：`iteration-v68.md`


对应对外 SDK 版本：`sdk-v68`。

### 背景

`sdk-v67` 已经把长期记忆统一描述为基本信息和个性化信息，但主 Agent 提示词仍偏向“用户明确要求记住”才调用 `manage_memory`。这会导致用户自然说出“我叫小明”这类基本信息时，主 Agent 只回答用户，而不触发记忆保存。

### 本轮变更

1. 主 Agent 系统提示词新增主动记忆规则：用户自然说出值得长期保存的信息时，即使没有说“记住”，也应调用 `manage_memory`。
2. 明确应主动保存的基本信息：
   - 姓名、年龄、性别、称呼、语言偏好、沟通偏好。
3. 明确应主动保存的个性化信息：
   - 住址、常去地点、联系人称呼、导航偏好、出行习惯、饮食偏好、无障碍偏好、提醒或任务设置。
4. 明确不应保存的边界：
   - 一次性任务、当前路况、临时找物线索、敏感密钥、设备 token、WiFi 密码、真实用户媒体数据或未经确认的推断。
5. 补充单元测试，确保主 Agent 提示词包含主动记忆要求和保存边界。
6. 更新长期记忆设计文档、能力开发指南和 SDK 版本记录。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests -q`

本轮未执行设备级回放。后续真实链路验证时，应重点观察用户说出姓名、称呼、导航偏好后，模型是否调用 `manage_memory`，以及 `runs/memory/agent_memories.json` 是否出现对应记录。

---

<a id="iteration-v69"></a>
## iteration-v69：SDK 迭代记录：工具调用前置播报

来源：`iteration-v69.md`


对应对外 SDK 版本：`sdk-v69`。

### 背景

当前 `agent_tts` 链路在工具调用场景下仍可能出现明显静默等待：模型需要先决定工具调用，SDK 再执行工具，工具完成后模型才生成最终回复。调研 OpenAI Realtime / Responses 工具调用事件后，本轮不把“模型在返回工具调用前先说等待语”作为稳定契约，而是在 SDK 工具执行入口提供框架级前置播报。

### 本轮变更

1. `ToolSpec` 新增 `progress_message`，用于声明工具执行前的短提示。
2. `AgentToolContext` 新增 `progress_callback` 和单轮去重记录。
3. `ToolGateway` 在工具真正执行前触发一次 `progress_message`，同一轮同一工具只播报一次。
4. 公开 SDK `BaseTool` 新增 `progress_message`，`SdkToolAdapter` 会透传到 agent-core。
5. 内置工具 `query_device_state`、`query_task_status`、`cancel_task`、`capture_photo` 和 `start_phone_video_link` 增加默认前置播报。
6. `manage_memory` 和 `memory_search` 增加默认前置播报，避免记忆管理子 Agent 请求期间静默等待。
7. 最终回复 TTS 仍在 Agent 请求前预热，但最终回复播放流延迟到首个最终回复文本到达时才注册，避免预热流占住播放仲裁器。
8. 中间播报改为先同步注册播放流，再异步执行 TTS 合成，确保后续最终回复排在前置播报之后。
9. 更新 `普通文本流式与TTS首包延迟优化设计.md`、`SDK安装与能力开发指南.md` 和 `sdk-version`。

### 业务开发边界

业务 Tool 只需要声明一句简短、口语化的 `progress_message`，不要自行调用播放器、TTS、WebSocket 控制消息或播放仲裁器。前置播报只覆盖“模型已经决定调用工具后，工具执行期间”的静默等待；模型首轮决策前的延迟仍由 SDK 通过 ASR 前移、模型/工具面收敛、TTS 预热和 Realtime 链路继续优化。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
3. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q`

本轮未执行设备级回放。改动集中在 agent-core ToolGateway、公开 Tool 适配和语音中间播报触发点；后续真实链路验证时应重点观察 `tool.call` 日志、前置播报播放流和最终回复是否按顺序进入播放仲裁。

---

<a id="iteration-v70"></a>
## iteration-v70：SDK 迭代记录：工具前置播报静态音频缓存

来源：`iteration-v70.md`


对应对外 SDK 版本：`sdk-v70`。

### 背景

`sdk-v69` 已经支持工具执行前置播报，但前置播报仍需要实时请求 TTS。对于 `progress_message` 这类静态短文本，每次工具调用都重新合成会增加首播延迟，也会产生重复 TTS 调用费用。

### 本轮变更

1. `ToolRegistry` 新增 `list_progress_messages()`，用于汇总当前注册工具的静态前置播报文案。
2. `VoiceRuntime` 启动后异步预加载工具前置播报音频缓存，缓存文件位于 `VOICE_RUNS_ROOT/progress-audio-cache`。
3. 缓存键按播报文本、TTS 模型、音色、TTS 采样率和目标播放采样率生成，避免配置变更后误用旧音频。
4. 缓存文件存在且格式符合 16k 单声道 16bit PCM WAV 时直接加载到内存。
5. 缓存不存在时，启动阶段调用当前配置的 TTS 生成一次 WAV；生成失败不阻塞服务启动。
6. 工具调用时命中缓存会直接读取本地 PCM 写入播放流，不再请求 TTS；缓存未就绪、未命中或失败时自动回退实时 TTS。
7. 更新 `普通文本流式与TTS首包延迟优化设计.md`、`SDK安装与能力开发指南.md` 和 `sdk-version`。

### 业务开发边界

业务 Tool 仍然只需要声明一句简短、口语化的 `progress_message`。业务代码不需要也不应该管理音频文件、调用 TTS、写播放控制消息或访问播放仲裁器。

如果业务侧频繁调整 `progress_message`、TTS 模型、音色或采样率，SDK 会自动生成新的缓存文件；旧缓存位于运行产物目录，可按运维需要手动清理。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
3. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q`

本轮未执行设备级回放。后续真实链路验证时应重点观察 `工具前置播报音频缓存预加载完成`、`工具前置播报命中静态音频缓存`、前置播报播放流和最终回复是否按顺序进入播放仲裁。

---

<a id="iteration-v71"></a>
## iteration-v71：SDK 迭代记录：工具前置播报随机候选

来源：`iteration-v71.md`


对应对外 SDK 版本：`sdk-v71`。

### 背景

`sdk-v70` 已经把工具前置播报做成本地静态音频缓存，但每个工具仍然只有一条固定提示语。真实语音交互中，固定句子高频重复会显得机械；业务 Tool 更适合声明 3 到 5 条口语化候选，由 SDK 在每次调用前随机选择。

### 本轮变更

1. `ToolSpec.progress_message` 从单字符串扩展为 `str | list[str] | None`，旧单句写法继续兼容。
2. 公开 SDK `BaseTool.progress_message` 同步支持字符串列表。
3. `AgentToolContext.announce_tool_progress()` 会规范化候选文案，并在工具执行前随机选择一条播报。
4. 同一轮同一工具仍然只播报一次，避免工具循环或重试导致重复提示。
5. `ToolRegistry.list_progress_messages()` 会展开所有候选文案，供启动阶段静态音频缓存预生成。
6. SDK 内置设备状态、任务状态、取消任务、抓拍、手机视频连接、Skill 读取和长期记忆工具改为 3 条默认候选。
7. 更新 `普通文本流式与TTS首包延迟优化设计.md`、`SDK安装与能力开发指南.md` 和 `sdk-version`。

### 业务开发边界

业务 Tool 仍然只声明 `progress_message`，不要自行随机、调用 TTS、管理音频文件或写播放控制消息。建议候选句保持短、口语化、不中断用户理解，不要包含工具执行后的最终结论。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`
2. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
3. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q`

本轮未执行设备级回放。后续真实链路验证时应重点观察同一工具多次调用时前置播报是否来自候选集合，以及 `工具前置播报音频缓存预加载完成` 中的缓存数量是否覆盖所有候选句。

---

<a id="iteration-v72"></a>
## iteration-v72：SDK 迭代记录：ESP32 连续对话门控收紧

来源：`iteration-v72.md`


对应对外 SDK 版本：`sdk-v72`。

### 背景

真实 ESP32 当前只有麦克风输入，没有扬声器回声参考。`realtime_semantic_vad` 连续对话窗口打开后，原固件在播放结束后只要本地 VAD 单帧进入 `VAD_SPEECH`，就会启动免唤醒新语音段。这在安静环境下能实现连续追问，但在真实佩戴环境中容易被尾音、环境声或播放残留触发，表现为用户没说话时眼镜自动进入下一轮并继续说话。

### 本轮变更

1. ESP32 连续对话播放结束后增加短冷却窗口，冷却期内不允许 VAD 免唤醒启动新段。
2. 连续对话 VAD 追问要求连续多帧 `VAD_SPEECH`，不再由单帧 VAD 触发。
3. WakeNet 唤醒首轮不受连续 VAD 帧门控影响，仍然可以立即开始本轮语音段。
4. 播放结束、语音段结束和连续窗口关闭时会重置连续 VAD 计数。
5. `连续对话 VAD 触发新语音段` 日志补充 `speech_frames`，便于真机排查触发来源。
6. 更新 `SDK安装与能力开发指南.md` 和 `sdk-version`。

### 验证

1. 已执行 C 语法级编译检查。
2. 本轮未执行 ESP-IDF 真机烧录和设备级回放。

真机验证时，需要重新烧录 ESP32 固件，并保持服务端 `VOICE_CONVERSATION_MODE=realtime_semantic_vad`。观察点：

1. 首次唤醒仍应打印 `WakeNet detected` 并进入语音段。
2. 播放结束后，短时间尾音不应立刻触发 `连续对话 VAD 触发新语音段`。
3. 用户在连续窗口内继续说话时，应出现 `连续对话 VAD 触发新语音段: ... speech_frames=...`。
4. 如果仍有误触发，优先继续调大冷却窗口或连续语音帧数，再考虑回退 `VOICE_CONVERSATION_MODE=segment_turn`。

---

<a id="iteration-v73"></a>
## iteration-v73：SDK 迭代记录：ESP32 播放任务创建可靠性

来源：`iteration-v73.md`


对应对外 SDK 版本：`sdk-v73`。

### 背景

真实眼镜连续对话时，服务端已经下发播放流，但 ESP32 可能打印 `创建 playback_stream_task 失败`。这表示播放任务没有创建出来，通常由内部堆可用连续块不足或任务栈分配失败导致。此前失败时端侧只写一条错误日志，没有向服务端回报播放失败，也没有输出堆内存诊断，容易导致状态排查困难。

### 本轮变更

1. 新增 `PLAYBACK_STREAM_TASK_STACK_SIZE` 常量，统一描述播放任务栈大小。
2. 播放任务从 `xTaskCreate` 改为 `xTaskCreateWithCaps(..., MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)`，优先把任务栈放入 PSRAM，减少内部堆压力。
3. 播放任务创建失败时打印内部堆和 PSRAM 的剩余量与最大连续块。
4. 播放任务创建失败时向服务端回报 `actuator.audio.state=failed`，原因是 `playback_task_create_failed`，并补发 `actuator.audio.finished`。
5. 失败清理时关闭扬声器通道、恢复本地监听状态，并给连续对话恢复加冷却，避免失败后立刻再次被 VAD 触发。
6. 更新 `SDK安装与能力开发指南.md` 和 `sdk-version`。

### 验证

1. `git diff --check`
2. `uv run openaiglass.glass.start --repo-root . --build-only`

本轮未执行真机烧录。真机验证时应重新烧录 ESP32 固件；如果仍出现播放任务创建失败，日志中的 `largest_internal` 与 `largest_spiram` 可以直接判断是否仍有堆碎片或 PSRAM 不足。

---

<a id="iteration-v74"></a>
## iteration-v74：SDK 迭代记录：音频原生链路流式返回

来源：`iteration-v74.md`


对应对外 SDK 版本：`sdk-v74`。

### 背景

`OpenAIAgentLoopRunner._run_direct_audio_turn()` 是音频原生 Chat Completions 分支，用于把当前轮 WAV 音频和自动照片直接交给 Omni 主模型，并保留 Tool 调用能力。此前这条分支使用 `stream=False`，即使上层提供了 `reply_text_delta_callback`，也只能等最终文本完整返回后一次性回调，导致 TTS 首包延迟高于普通文本和图片解读链路。

### 本轮变更

1. 音频原生 Chat Completions 请求改为 `stream=True`。
2. 新增流式消费逻辑，持续提取 `choices[].delta.content` 并透传给 `reply_text_delta_callback`。
3. 新增工具调用分片累积逻辑，支持从 `choices[].delta.tool_calls` 组装完整工具调用。
4. 工具调用仍在流结束后通过 `ToolGateway` 执行，工具结果回填后下一轮模型请求继续使用流式模式。
5. 保持旧的手写工具循环上限和模型请求快照结构。
6. 更新 `普通文本流式与TTS首包延迟优化设计.md`、`SDK安装与能力开发指南.md` 和 `sdk-version`。

### 验证

1. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py -v`

本轮未执行设备级回放。真机验证时应观察音频原生链路最终回复是否在模型完整结束前开始进入 TTS 播放。

---

<a id="iteration-v75"></a>
## iteration-v75：SDK 迭代记录：Omni 音频直出支持工具调用

来源：`iteration-v75.md`


对应对外 SDK 版本：`sdk-v75`。

### 背景

`sdk-v74` 已经让音频原生 Chat Completions 分支改为流式文本返回，但真实语音体验仍容易落到“模型文本增量 + CosyVoice TTS”。这会让支持音频输出的 Omni 模型没有充分发挥低延迟优势。

同时，Omni 音频直出和工具调用本身并不冲突：模型可以先输出自然语音反馈，再触发 function calling；SDK 执行工具并回填结果后，模型继续输出最终音频。已经播放给用户的前置语音不应默认取消。

### 本轮变更

1. 默认 `VOICE_REPLY_MODE=omni_realtime` 重新接回 Omni Realtime 音频直出 pipeline。
2. `sensor.audio.segment.started` 时预连接 Omni Realtime，并把上行音频分片同步追加到 Omni 会话。
3. Omni Realtime session 会携带当前模型可见 SDK Tool schema。
4. 新增 Realtime 工具桥，监听 `response.function_call_arguments.done`，执行 SDK `ToolGateway`，再以 `function_call_output` 回填给 Omni 并继续请求文本与音频输出。
5. 工具执行不默认取消已经播放的模型音频；工具成功或失败都作为后续上下文交给模型继续播报。
6. 更新功能开发指南、配置模板和相关单元测试。

### 验证

1. `python -m py_compile openaiglass-sdk/server-python/runtime/voice_runtime.py openaiglass-sdk/server-python/agent_core/runtime/runner.py openaiglass-sdk/server-python/infra/config/settings.py`
2. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_voice_runtime.py -v`
3. `PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run python -m unittest openaiglass-sdk/tests/unit/test_agent_core.py openaiglass-sdk/tests/unit/test_settings.py -v`

本轮未执行真实设备级回放。真机联调时应重点观察 `Omni Realtime 工具调用请求`、`Omni Realtime 工具结果已回填`、`Omni Realtime 返回首段音频` 和眼镜端 `播放流收到首段 PCM`。

---

<a id="iteration-v76"></a>
## iteration-v76：SDK 迭代记录：Omni Realtime 上行字节流透传

来源：`iteration-v76.md`


对应对外 SDK 版本：`sdk-v76`。

### 背景

`sdk-v75` 已经让 Omni Realtime 直出链路重新接入 Agent-Core 和 SDK 工具，但真实日志仍显示一个关键问题：服务端会在 `sensor.audio.segment.finished` 之后才打开 Omni Realtime WebSocket，并把整段音频一次性追加给 Omni。这只能算“模型输出流式”，不是“麦克风输入到 Omni 的全链路字节流”。

本轮目标是让音频从端侧开始上传后，服务端立即把每个 PCM 分片转发给 Omni Realtime；语音段结束时只负责补图片、commit 和请求响应。

### 本轮变更

1. `AgentLoopRunner` 新增 `PreparedNativeAudioReply` 预备运行态，用于提前构造 Agent-Core 的系统提示词、工具 schema、工具处理器和调试请求摘要。
2. `AgentFacade` 新增原生音频轮次的准备与完成接口：
   - `prepare_native_audio_turn(...)`：语音段开始时准备 Agent-Core 上下文，不保存消息、不调用模型。
   - `complete_prepared_native_audio_turn(...)`：Omni 响应完成后，把用户转写、助手文本、工具轨迹、资产和模型请求摘要写回会话。
3. `VoiceRuntime` 在 `sensor.audio.segment.started` 时启动 Omni Realtime 会话，并创建 `ReplySynthesisContext`。
4. `/ws_audio` 每收到一段 `audio_chunk`，除写入本地 `SegmentBuffer` 外，也同步调用 `OmniRealtimeStreamingSession.append_audio(...)`。
5. 建连期间已经进入本地缓存的 PCM 会按顺序补推给 Omni，再切换到实时逐帧转发，避免丢帧或乱序。
6. `sensor.audio.segment.finished` 后复用已打开的 Omni 会话：
   - 等待本轮自动抓拍的短超时结果。
   - 按图片输入策略追加可直传图片。
   - 执行 `commit()` 和 `create_response(...)`。
   - 将 Omni 返回的音频 delta 继续写入同一条下行播放流。
7. 保留整段提交兜底：预连接失败、采样格式不支持或预推失败时，仍回退到旧的 segment-batch 路径。

### 验证

1. 单元测试：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q
```

结果：`69 passed`。

2. 设备级回放：

```bash
LOG_LEVEL=DEBUG uv run openaiglass.server.start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env \
  --log-file openaiglass-for-blind/logs/server-byte-stream-check.log

uv run openaiglass.phone.mock \
  --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json

uv run openaiglass.glass.start \
  --runtime playback \
  --config openaiglass-for-blind/host/glass-playback/config/look_look.json
```

结果：`glass-playback` 返回 `assertions_ok=true`，`actuator_count=1`。

关键日志：

```text
2026-05-01T08:12:40.708639+00:00 glass-playback 开始发送触发音频 chunks=119
2026-05-01T08:12:41.064664+00:00 server.voice Omni Realtime 首段上行音频已推送 bytes=11520 frame_count=1
2026-05-01T08:12:41.064742+00:00 server.voice Omni Realtime 端到端输入流已启动 buffered_audio_bytes=11520 tool_count=6
2026-05-01T08:12:45.952571+00:00 glass-playback 触发音频发送完成 bytes=151552
2026-05-01T08:12:45.957739+00:00 server.voice Omni Realtime 请求已提交 audio_bytes=151552 audio_frame_count=111 image_count=1
2026-05-01T08:12:46.775006+00:00 server.voice Omni Realtime 返回首段音频 bytes=15360
```

这组日志确认：Omni 首段上行推送发生在端侧音频上传完成前约 4.9 秒，且最终提交时 `audio_frame_count=111`，不再是一整段音频一次性提交。

验证结束后已停止本地 server 和 phone mock。

---

<a id="iteration-v77"></a>
## iteration-v77：SDK 迭代记录：Omni 音频直出旁路 ASR 转写

来源：`iteration-v77.md`


对应对外 SDK 版本：`sdk-v77`。

### 背景

`sdk-v76` 已把眼镜 PCM 上行改成真正的字节流：端侧开始发送音频后，服务端立即把分片推给 Omni Realtime。但用户文本仍主要来自 Omni Realtime 自己返回的 transcript 事件，这会把“回答模型”和“转写来源”绑定在一起。

本轮把转写拆成旁路能力：Omni 继续直接消费音频字节流并返回音频回复；ASR 作为 sidecar 节点并行接收同一份 PCM，用于日志、会话上下文和后续记忆输入。

### 本轮变更

1. `SegmentBuffer` 新增旁路 ASR 状态：
   - `sidecar_asr_session`
   - `sidecar_transcript_done`
   - `sidecar_transcript_text`
   - `sidecar_transcript_source`
   - `sidecar_transcript_error`
   - `sidecar_asr_metrics`
2. `VOICE_REPLY_MODE=omni_realtime` 且输入是原始音频时，`VoiceRuntime` 会在启动 Omni 输入流后启动旁路 ASR。
3. `/ws_audio` 每个 PCM 分片会 fan-out 到三个位置：
   - 本地 `SegmentBuffer`，用于落盘和兜底。
   - Omni Realtime 会话，作为主回答链路。
   - 旁路 ASR 会话，作为异步转写链路。
4. `sensor.audio.segment.finished` 后：
   - Omni 主链路不等待 ASR，继续 commit 并请求音频回复。
   - 旁路 ASR 在后台 finish；如果结果已就绪，则优先写入 Agent-Core。
   - 如果旁路 ASR 晚于 Omni 回复完成，会异步回填 Agent 会话中的用户消息和 transcript artifact。
5. Transcript artifact 和文字交互日志新增来源字段：
   - `transcript_source=sidecar_realtime_asr`
   - `transcript_source=sidecar_batch_asr`
   - `transcript_source=omni_fallback`
   - `transcript_source=unavailable`
6. 保留降级逻辑：实时旁路 ASR 未启用或启动失败时，段结束后尝试批量 ASR 旁路转写；旁路失败不影响 Omni 主回答链路。

### 验证

1. 单元测试：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q
```

结果：`71 passed`。

2. 设备级回放：

```bash
LOG_LEVEL=DEBUG uv run openaiglass.server.start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env \
  --log-file openaiglass-for-blind/logs/server-sidecar-asr-check.log

uv run openaiglass.phone.mock \
  --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json

uv run openaiglass.glass.start \
  --runtime playback \
  --config openaiglass-for-blind/host/glass-playback/config/look_look.json
```

结果：`glass-playback` 返回 `assertions_ok=true`，`actuator_count=1`。

关键日志：

```text
2026-05-01T13:51:00.919352+00:00 glass-playback 开始发送触发音频 chunks=119
2026-05-01T13:51:01.217123+00:00 server.voice Omni Realtime 首段上行音频已推送 bytes=8960 frame_count=1
2026-05-01T13:51:01.217181+00:00 server.voice Omni Realtime 端到端输入流已启动 buffered_audio_bytes=8960 tool_count=6
2026-05-01T13:51:01.219425+00:00 server.voice 旁路 ASR 实时输入流已启动 buffered_audio_bytes=8960
2026-05-01T13:51:06.176986+00:00 glass-playback 触发音频发送完成 bytes=151552
2026-05-01T13:51:06.187663+00:00 server.voice Omni Realtime 请求已提交 audio_bytes=151552 audio_frame_count=113 image_count=1
2026-05-01T13:51:06.230186+00:00 server.voice 旁路 ASR 转写完成 source=sidecar_realtime_asr text='我叫文刀。文字的文，刀锋的刀。'
2026-05-01T13:51:06.831067+00:00 server.voice Omni Realtime 返回首段音频 bytes=15360
2026-05-01T13:51:07.106568+00:00 server.voice Omni Realtime 文字交互 user='我叫文刀。文字的文，刀锋的刀。' transcript_source=sidecar_realtime_asr
```

这组日志确认：回答主链路仍走 Omni 输入字节流，转写文本来自旁路 ASR，不再依赖 Omni transcript。

验证结束后已停止本地 server 和 phone mock。

---

<a id="iteration-v78"></a>
## iteration-v78：SDK 迭代记录：Omni 最终回复播放流延迟注册

来源：`iteration-v78.md`


对应对外 SDK 版本：`sdk-v78`。

### 背景

工具调用前会播报一段等待提示。问题出现在 Omni 原生音频回复链路：服务端在语音段开始时就提前创建了最终回复播放流，虽然那时还没有任何模型音频，但播放仲裁器已经把这条最终回复流登记为当前播放流。

当模型随后触发工具调用时，工具前置播报只能排队等待；等 Omni 返回最终音频后，最终回复反而先播放，前置播报延后播放。这和“工具调用前提示用户等待”的产品预期相反。

### 根因

播放仲裁器只看播放流登记顺序，不关心该流是否已经有音频数据。

旧逻辑：

1. `sensor.audio.segment.started` 时启动 Omni Realtime 上行字节流。
2. 同时提前创建 `omni_realtime` 最终回复播放上下文。
3. 工具调用发生时创建 `agent_progress` 前置播报。
4. 播放仲裁器认为 `omni_realtime` 已经占用当前播放位，导致 `agent_progress` 被排队。

### 本轮变更

1. Omni Realtime 上行会话仍然在语音段开始时建立，继续保持端到端字节流输入。
2. 最终回复播放上下文不再在语音段开始时创建。
3. 只有当 Omni 返回首段 `response.audio.delta` 时，才懒创建 `omni_realtime` 播放上下文。
4. 如果 Omni 没有返回音频但链路需要收尾，才在最终阶段创建兜底播放上下文。
5. 保留工具前置播报的同步注册逻辑，确保工具调用发生时播放仲裁器能立即看到 `agent_progress` 流。

### 验证

1. 单元测试：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q
```

结果：`72 passed`。

新增或调整的关键用例：

- `test_omni_mode_prestreams_realtime_audio_direct_session`
  - 验证语音段开始时只建立 Omni 输入会话，不提前创建最终回复播放上下文。
- `test_omni_final_audio_does_not_jump_ahead_of_progress_reply`
  - 验证工具前置播报已占位时，Omni 最终音频不会抢先播放。

2. 设备级回放：

```bash
LOG_LEVEL=DEBUG uv run openaiglass.server.start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env \
  --log-file openaiglass-for-blind/logs/server-progress-order-check.log

uv run openaiglass.phone.mock \
  --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json

uv run openaiglass.glass.start \
  --runtime playback \
  --config openaiglass-for-blind/host/glass-playback/config/look_look.json
```

结果：`glass-playback` 返回 `assertions_ok=true`，`actuator_count=2`。

关键日志：

```text
2026-05-01T14:07:01.981635 server.voice Omni Realtime 工具调用请求 tool_name=manage_memory
2026-05-01T14:07:01.982420 server.voice-reply_8ba8f1e97708 工具前置播报命中静态音频缓存
2026-05-01T14:07:01.984038 server.voice-reply_8ba8f1e97708 下行播放请求已发送 audio_source=tts
2026-05-01T14:07:02.660490 server.voice Omni Realtime 返回首段音频
2026-05-01T14:07:02.664485 server.voice-reply_0073e5d2f8b1 下行音频源返回首段音频 audio_source=omni_realtime
2026-05-01T14:07:04.367587 server.voice-reply_0073e5d2f8b1 下行播放请求已发送 audio_source=omni_realtime
```

眼镜端顺序：

```text
2026-05-01T14:07:01.982815 glass-playback actuator.audio.play stream_id=reply_8ba8f1e97708
2026-05-01T14:07:04.365781 glass-playback 本机播放器播放结束 stream_id=reply_8ba8f1e97708
2026-05-01T14:07:04.367688 glass-playback actuator.audio.play stream_id=reply_0073e5d2f8b1
```

这确认前置播报先播放，最终回复在前置播报结束后播放。

验证结束后已停止本地 server 和 phone mock。

---

<a id="iteration-v79"></a>
## iteration-v79：SDK 迭代记录：工具前置播报缓存指纹校验

来源：`iteration-v79.md`


对应对外 SDK 版本：`sdk-v79`。

### 背景

工具前置播报支持启动时预生成 WAV 缓存，用于降低工具调用前提示音的首包延迟。此前缓存 key 只包含 TTS 模型、TTS 音色和播放格式，没有显式记录缓存是通过什么方式生成的，也不会在启动时清理旧版本缓存。

当最终回复切到 Omni Realtime 音频直出后，前置播报仍使用 CosyVoice TTS 缓存。即使这是当前实现的有意设计，也必须让缓存系统知道当前最终播报链路的模型和音色，否则切换 `VOICE_MODEL_VOICE`、`VOICE_OMNI_REALTIME_MODEL_NAME` 或后续切换前置播报生成方式时，旧缓存可能继续被复用。

### 本轮变更

1. 工具前置播报缓存新增 `.json` 元数据文件。
2. 缓存指纹新增以下字段：
   - `progress_audio_provider`
   - `tts_model_name`
   - `tts_voice`
   - `tts_sample_rate_hz`
   - `reply_audio_provider`
   - `reply_model_name`
   - `reply_voice`
   - `playback_sample_rate_hz`
   - `channels`
3. 启动预加载时会扫描缓存目录：
   - 没有元数据的旧 WAV 会被删除。
   - 元数据与当前配置不一致的缓存会被删除。
   - 不属于当前工具前置播报文案集合的缓存会被删除。
4. 删除后按当前配置重新生成缓存。

### 注意

当前默认实现仍是：

- 最终回复：`Omni Realtime`，由 `VOICE_OMNI_REALTIME_MODEL_NAME + VOICE_MODEL_VOICE` 控制。
- 工具前置播报：`CosyVoice TTS`，由 `TTS_MODEL_NAME + TTS_VOICE` 控制。

本轮解决的是“缓存是否与当前配置一致”的问题；如果要让前置播报和最终回复在声学模型层面完全一致，下一步还需要实现 Omni 生成前置播报缓存或统一把最终回复也切回 TTS。

### 验证

1. 单元测试：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q
```

结果：`73 passed`。

新增关键用例：

- `test_progress_audio_cache_prunes_stale_profile_on_startup`
  - 构造旧缓存 WAV 和旧元数据。
  - 启动 `VoiceRuntime` 后确认旧缓存被删除。
  - 确认新元数据包含当前最终播报链路的 provider、模型和音色。

2. 设备级回放：

```bash
LOG_LEVEL=DEBUG uv run openaiglass.server.start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env \
  --log-file openaiglass-for-blind/logs/server-progress-cache-profile-check.log

uv run openaiglass.phone.mock \
  --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json

uv run openaiglass.glass.start \
  --runtime playback \
  --config openaiglass-for-blind/host/glass-playback/config/look_look.json
```

结果：`glass-playback` 返回 `assertions_ok=true`，`actuator_count=2`。

关键日志：

```text
2026-05-01T14:29:33.815189 server.voice-reply_a775b0422e74 工具前置播报命中静态音频缓存
2026-05-01T14:29:33.817884 server.voice-reply_a775b0422e74 下行播放请求已发送 audio_source=tts
2026-05-01T14:29:34.488136 server.voice Omni Realtime 返回首段音频
2026-05-01T14:29:36.128809 server.voice-reply_388fd383fc57 下行播放请求已发送 audio_source=omni_realtime
```

缓存元数据示例：

```text
progress_audio_provider=tts
reply_audio_provider=omni_realtime
reply_model_name=qwen3.5-omni-plus-realtime
reply_voice=Tina
tts_voice=longanhuan
```

验证结束后已停止本地 server 和 phone mock。

---

<a id="iteration-v80"></a>
## iteration-v80：SDK 迭代记录：工具前置播报改为首输出自动判定

来源：`iteration-v80.md`


对应对外 SDK 版本：`sdk-v80`。

### 背景

工具调用前的等待提示此前由静态配置控制。这个开关会把“是否播报”变成静态配置，但真实语义应当由模型输出决定：

- 如果模型本轮一开始就调用工具，用户还没有收到任何回复，应播放工具配置的静态等待提示。
- 如果模型已经先返回文本或音频，说明用户已经听到或看到反馈，后续再插入等待提示会打断体验。

### 本轮变更

1. `AgentToolContext` 新增本轮首个模型输出类型记录。
2. 工具前置播报不再依赖静态配置：
   - 首输出为 `tool_call`：自动播报工具 `progress_message`。
   - 首输出为 `text` 或 `audio`：工具照常执行，但不播报等待提示。
3. Agent-Core 流式链路会在收到文本增量时记录首输出为 `text`。
4. Chat Completions 音频原生链路会识别首个文本增量或工具调用增量。
5. Omni Realtime 链路会识别首个 `response.audio_transcript.delta`、`response.text.delta`、`response.audio.delta` 或 `response.function_call_arguments.done`。
6. 工具前置播报缓存预加载只要工具配置了播报文案且 TTS 配置可用，就会预生成或加载缓存。
7. 删除旧静态开关，避免配置项继续误导业务开发。

### 验证

1. 单元测试：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py -q
```

结果：通过。

新增关键用例：

- `test_tool_gateway_suppresses_progress_when_text_is_first_model_output`
- `test_openai_runner_direct_audio_path_suppresses_progress_after_text_delta`
- Omni Realtime 测试补充断言首输出为 `text` 或 `tool_call` 时的识别结果。

2. 设备级回放：

```bash
LOG_LEVEL=DEBUG uv run openaiglass.server.start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env \
  --log-file openaiglass-for-blind/logs/server-auto-progress-check.log

uv run openaiglass.phone.mock \
  --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json

uv run openaiglass.glass.start \
  --runtime playback \
  --config openaiglass-for-blind/host/glass-playback/config/look_look.json
```

结果：`glass-playback` 返回 `assertions_ok=true`，`actuator_count=2`。

关键日志：

```text
Omni Realtime 工具调用请求 tool_name=manage_memory
工具前置播报命中静态音频缓存 stream_id=reply_675abca14d2e
下行播放请求已发送 stream_id=reply_675abca14d2e audio_source=tts
Omni Realtime 返回首段音频
下行播放请求已发送 stream_id=reply_ea10f2078681 audio_source=omni_realtime
```

验证结束后已停止本地 server 和 phone mock。

---

<a id="iteration-v81"></a>
## iteration-v81：SDK 迭代记录：工具前置播报音频来源可配置

来源：`iteration-v81.md`


### 背景

`sdk-v80` 已经把工具前置播报改为由模型首输出类型自动判定：首输出是工具调用时播报 `ToolSpec.progress_message`，首输出是文本或音频时不插入等待提示。

真实听感验证后发现，预生成缓存音频虽然首包延迟低，但可能和实时生成的最终回复存在音色、情感和停顿差异。业务侧需要能按产品目标选择“更快的缓存播报”或“更一致的实时播报”。

### 变更

1. 新增服务端配置 `TOOL_PROGRESS_AUDIO_MODE`：
   - `cached`：启动阶段预生成或复用本地工具前置播报缓存。
   - `realtime`：工具调用前实时创建 TTS 流，边生成边下发。
2. SDK 默认值为 `cached`，保持已有低延迟缓存行为兼容。
3. 盲人业务本地配置模板默认设置为 `TOOL_PROGRESS_AUDIO_MODE="realtime"`，优先验证提示音和实时回复的一致性。
4. `cached` 模式读取本地 PCM 后，不再直接绕过播放合成框架，而是通过 `_emit_synthesis_chunk(...)` 和 `_finalize_synthesis_context(...)` 写入同一条下行播放路径。
5. `realtime` 模式会跳过启动阶段缓存预加载，工具调用前复用原有 `_synthesize_text_into_context(...)` 流式 TTS 路径。

### 配置示例

```bash
# 更关注首包延迟和 TTS 调用成本
TOOL_PROGRESS_AUDIO_MODE="cached"

# 更关注工具提示音和实时回复听感一致
TOOL_PROGRESS_AUDIO_MODE="realtime"
```

### 观察日志

`cached` 模式应看到：

```text
工具前置播报音频缓存预加载完成
工具前置播报命中静态音频缓存
```

`realtime` 模式应看到：

```text
工具前置播报音频缓存已跳过 mode=realtime
工具前置播报使用实时流式 TTS
```

两种模式下，眼镜端都只接收 `actuator.audio.play` 和 `/stream.wav` 下行播放流。

### 验证

- `uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_settings.py openaiglass-sdk/tests/unit/test_voice_runtime.py openaiglass-sdk/tests/unit/test_agent_core.py -q`
- `uv run openaiglass.glass.start --runtime playback --config openaiglass-for-blind/host/glass-playback/config/look_look.json`
  - 配置摘要确认 `tool_progress_audio_mode=realtime`。
  - 服务端启动日志确认 `工具前置播报音频缓存已跳过 mode=realtime`。
  - 回放结果 `assertions_ok=true`，眼镜端收到并播放 Omni Realtime 下行音频。
- 使用“我叫文刀，文字的文，刀锋的刀”样例做记忆工具回归：
  - 发现旧提示词仍要求模型“调用任何工具前先简单回复用户”，会诱导 Omni 先输出文本并跳过 `manage_memory`。
  - 已改为“工具调用前的等待提示由系统自动播报”，并明确姓名等基本信息必须调用 `manage_memory`，不能只用文字声称已经记住。
  - 回放验证恢复 `manage_memory` 工具调用，并在 `TOOL_PROGRESS_AUDIO_MODE=realtime` 下触发 `工具前置播报使用实时流式 TTS`。
- 使用“帮我设置一个三分钟的计时器”样例做临时回放验证：
  - Omni Realtime 触发 `start_timer` 工具调用，工具结果成功回填。
  - 本轮模型首输出先是文本和音频，随后才调用工具；按 `sdk-v80` 的自动判定规则，SDK 不插入工具前置播报，因此本次真实链路不会出现 `工具前置播报使用实时流式 TTS` 日志。
  - `TOOL_PROGRESS_AUDIO_MODE=realtime` 下“首输出即工具调用”的实时 TTS 分支由单元测试 `test_progress_reply_uses_realtime_tts_when_configured` 覆盖。

---

<a id="iteration-v82"></a>
## iteration-v82：sdk-v82 配置分层与 YAML 化

来源：`iteration-v82.md`


### 背景

服务端配置长期堆在 `local_server.env` 中，模型、语音链路、设备令牌、日志、记忆和工具前置播报混在一起。随着 Omni Realtime、旁路 ASR、TTS、工具前置播报、长期记忆等配置增多，继续用扁平 env 文件很难看出配置项之间的组合关系。

### 配置分层

新的业务侧推荐配置是：

```text
openaiglass-for-blind/config/local_server.yaml  # 非敏感运行配置，不提交真实本地文件
openaiglass-for-blind/config/.env               # API Key 等敏感信息，不提交
```

`local_server.yaml.example` 按以下层次组织：

```text
app                 运行环境
server              监听地址、端口、局域网公开地址
logging             日志级别和日志文件
devices             服务端、眼镜、手机设备编号和配对令牌
heartbeat           心跳间隔和超时
models              base_url、Agent、Omni Realtime、ASR、TTS
voice               会话模式、回复模式、连续对话、turn detection、落盘目录
tools               工具前置播报全局开关和音频模式
agent.memory        长期记忆开关、路径和提示词注入数量
```

敏感信息不进入 YAML。当前 `.env.example` 只保留：

```bash
DASHSCOPE_API_KEY=""
```

### 兼容策略

1. `openaiglass.server.start --config` 支持 `.yaml/.yml` 和旧 `.env` 格式。
2. 如果传入 YAML，启动器会把分层配置转换为现有运行时环境变量，`ServerSettings` 暂不感知 YAML 结构，降低改动风险。
3. 同目录 `.env` 会自动加载，适合放 `DASHSCOPE_API_KEY`。
4. 旧的 `local_server.env` 仍可用，便于已有部署平滑迁移。
5. `openaiglass.config.sync` 支持从 YAML 读取 `server.public_host`、`server.port` 和 `devices.tokens`，并能回写 `server.public_host`。

### 关键映射

| YAML 路径 | 运行时环境变量 |
| --- | --- |
| `server.host` / `server.port` | `HOST` / `PORT`，启动器再同步为 `SERVER_HOST` / `SERVER_PORT` |
| `server.public_host` | `SERVER_PUBLIC_HOST` |
| `devices.tokens` | `DEVICE_TOKEN_MAP` |
| `models.agent.model` | `AGENT_MODEL_NAME` |
| `models.voice.model` / `models.voice.voice` | `VOICE_MODEL_NAME` / `VOICE_MODEL_VOICE` |
| `models.omni_realtime.*` | `VOICE_OMNI_REALTIME_*` |
| `models.asr.*` | `VOICE_ASR_*` |
| `models.tts.*` | `TTS_*` |
| `voice.*` | `VOICE_*` 和 `MAX_SEGMENT_AUDIO_BYTES` |
| `tools.progress_audio.enabled` | `TOOL_PROGRESS_AUDIO_ENABLED` |
| `tools.progress_audio.mode` | `TOOL_PROGRESS_AUDIO_MODE` |
| `agent.memory.*` | `AGENT_MEMORY_*` |

### 验证

已新增单元测试覆盖：

1. YAML 分组配置转换为运行时 env。
2. 同目录 `.env` 注入 `DASHSCOPE_API_KEY`。
3. 旧 env 配置读取仍保持兼容。

### 后续修正：工具前置播报音频来源

`tools.progress_audio.mode=realtime` 不再固定走 TTS。实际音频生成方跟随当前主回复链路：

1. `voice.reply_mode=omni_realtime` 时，工具前置播报使用 `models.omni_realtime.model` 和 `models.voice.voice` 创建独立 Omni Realtime 会话生成音频。
2. `voice.reply_mode=agent_tts` 时，工具前置播报使用 `models.tts.model` 和 `models.tts.voice` 创建流式 TTS 会话生成音频。
3. `cached` 缓存只服务于 TTS 主链路；Omni 主链路不会复用 TTS 缓存，避免提示音和最终回复音色、情感不一致。

### 后续修正：工具前置播报全局开关与缓存校验

`tools.progress_audio.enabled` 是工具前置播报的全局开关：

1. `true` 时，SDK 仍按模型首输出类型自动判定是否播报：首输出为工具调用才播，首输出为文本或音频不播。
2. `false` 时，即使 Tool 配置了 `progress_message`，调用工具前也不会插入任何提示音。
3. `tools.progress_audio.mode=cached` 且主链路为 TTS 时，Server 启动会读取当前工具注册表的所有 `progress_message`，按当前文本、TTS 模型、音色、采样率生成缓存指纹。
4. 如果某个 Tool 删除了 `progress_message`，对应旧缓存会在启动阶段被清理。
5. 如果某个 Tool 修改了 `progress_message`，旧文案缓存会被清理，新文案会重新生成离线音频。
6. `mode=realtime` 或 Omni 主链路不依赖离线提示音缓存，启动时不需要更新提示音文件。

---

<a id="iteration-v84"></a>
## iteration-v84：sdk-v84 外部 MCP、Task 调度与通知链路修复

来源：`iteration-v84.md`


### 背景

业务侧反馈三类 SDK 边界问题会影响真实业务闭环：

1. 业务只能注册 `BaseMcpAdapter`，不能直接配置并连接官方 MCP Server 的 stdio/SSE 进程。
2. SDK 自定义 Task 没有公开通用定时调度接口，也缺少终态事件“先回流 Agent 决策，再通知用户”的声明字段。
3. `DeviceGroupContext.submit_notification(...)` 只进入 `DeviceGroupRuntime` 通知记录，真实 `ControlRuntime` 没有把通知适配器绑定到 `VoiceRuntime` 播报入口。

### 变更

1. 新增 `ExternalMcpServerConfig` 和 `ExternalMcpAdapter`：
   - 支持 `stdio`、`sse`、`streamable_http` 三种外部 MCP Server 连接方式。
   - 通过官方 MCP Python SDK 读取 tools/list，并映射成 SDK `McpMethodSpec`。
   - 新增 `OpenAIGlassesSDK.register_external_mcp_server(...)`，业务宿主可用配置注册官方 AMap MCP Server。
   - 新增可选依赖 `openaiglasses-sdk[mcp]`，避免普通 SDK 安装强制拉取 MCP client 依赖。
2. 新增 SDK 自定义 Task 调度能力：
   - `TaskContext.schedule_event(...)` 可安排一次性延迟事件。
   - `DeviceGroupContext.schedule_task_event(...)` 可从设备组上下文安排目标任务事件。
   - `TaskRuntimeManager` 负责定时器、幂等事件编号、终态保护和调度事件日志。
3. 新增 Task 终态事件策略字段：
   - `terminal_event_requires_agent_decision`
   - `terminal_event_allow_direct_notify`
   - `terminal_event_priority`
   - 调度器触发的终态事件会按这些字段发布给后台任务事件监听器。
4. 修复设备组通知真实播报链路：
   - `ControlRuntime` 初始化时绑定 `DeviceGroupRuntime.notification_adapter`。
   - `VoiceRuntime.submit_notification(...)` 统一把外部通知送入 `NotificationCoordinator` 和播放仲裁链路。
   - `context.submit_notification(...)` 现在会触发真实 `assistant.reply` / `actuator.audio.play`，不再只增加 `notification_count`。

### 开发者使用方式

外部 MCP Server：

```python
sdk.register_external_mcp_server(
    ExternalMcpServerConfig(
        name="amap",
        transport="stdio",
        command="npx",
        args=["-y", "@amap/amap-maps-mcp-server"],
        env={"AMAP_MAPS_API_KEY": "..."},
        method_prefix="amap",
    )
)
```

Task 定时调度：

```python
class TimerTask(BaseTask):
    task_type = "timer_task"
    terminal_event_requires_agent_decision = True
    terminal_event_allow_direct_notify = False

    def on_start(self, context):
        context.emit_state("running")
        context.schedule_event(delay_ms=3000, event_name="timer.fired")

    def on_event(self, context, event):
        if event.name == "timer.fired":
            context.complete({"message": "计时结束"})
```

### 验证

已执行：

```bash
uv run python -m py_compile \
  openaiglass-sdk/server-python/agent_core/mcp/external_client.py \
  openaiglass-sdk/server-python/openaiglasses/capabilities/base_task.py \
  openaiglass-sdk/server-python/openaiglasses/runtime/tasks.py \
  openaiglass-sdk/server-python/openaiglasses/runtime/device_group.py \
  openaiglass-sdk/server-python/openaiglasses/server.py \
  openaiglass-sdk/server-python/runtime/voice_runtime.py \
  openaiglass-sdk/server-python/api/ws/control_runtime.py

uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_sdk_phase_two.py \
  openaiglass-sdk/tests/unit/test_task_event_runtime.py \
  -q
```

结果：

1. 静态编译通过。
2. 相关单元测试 50 条通过。

### 设备级回放状态

本轮未执行完整 `glass-playback` 设备级回放。原因是改动集中在 SDK 公开扩展面、任务运行时和通知适配绑定，已用单元测试覆盖外部 MCP 映射、调度事件、终态策略和通知协调入口。下一轮业务侧可用 timer 场景通过 `glass-playback` 验证 `assistant.reply` / `actuator.audio.play` 是否出现在事件日志中。

---

<a id="iteration-v85"></a>
## iteration-v85：sdk-v85 真实眼镜连续 VAD 自循环修复

来源：`iteration-v85.md`


### 背景

2026-05-02 真机联调时，首次 WakeNet 唤醒后的天气问答可以正常完成，但播放结束后眼镜持续出现 `连续对话 VAD 触发新语音段`。服务端后续轮次的 `transcript_source=unavailable`，用户文本为空，却仍把自动抓拍图片交给 Omni Realtime，模型开始反复描述画面并继续下发播放，形成“空语音 + 自动抓拍 + 看图回复 + 再触发 VAD”的自循环。

同时，循环期间眼镜长时间处于播放和重新开段状态，用户再次呼叫“嗨乐鑫”时体验上表现为没有响应。

### 原因

1. 真实 ESP32 固件没有端侧 AEC，但收到服务端 `realtime_semantic_vad` 请求后仍保留 `semantic_continuous=1`。
2. 播放结束后，连续 VAD 只要满足短冷却和少量语音帧就会免唤醒启动下一段。
3. 服务端 Omni Realtime 字节流分支会在 `sensor.audio.segment.started` 时前置自动抓拍；即使最终没有 ASR 文本，图片仍可进入模型输入，导致模型把空段当成看图请求回答。

### 变更

1. ESP32 半双工降级时关闭免唤醒连续 VAD：
   - `voice.realtime.session.open` 仍可接收服务端的 realtime 请求。
   - 由于当前端侧声明 `aec=false`、`accepted_mode=half_duplex`，固件将 `s_realtime_semantic_dialog_enabled` 固定为 `false`。
   - 日志改为同时打印 `semantic_continuous_requested` 和 `semantic_continuous_enabled`。
   - 能力回报中的 `continuous_dialog=false`，`turn_detection_owner=endpoint`。
2. 语音段协议补充触发来源：
   - `sensor.audio.segment.started.payload.trigger` 为 `wake_word` 或 `continuous_vad`。
   - WakeNet 触发时保留 `wake_word` 详情；连续 VAD 触发时不再伪装成唤醒词触发。
3. 服务端增加连续 VAD 空段保护：
   - `SegmentBuffer.start_trigger` 记录端侧触发来源。
   - 对 `trigger=continuous_vad` 的语音段，服务端等待旁路 ASR。
   - 如果旁路 ASR 为空，则在进入 Omni Realtime 回复链路前抑制本轮，关闭预连接会话，并丢弃该段自动抓拍。
   - 该保护避免旧固件或自定义端侧仍误上报连续 VAD 时继续触发模型看图回复。

### 联调观察点

正常真机日志应看到：

```text
收到 voice.realtime.session.open，当前固件降级为半双工: session_id=... semantic_continuous_requested=1 semantic_continuous_enabled=0
WakeNet listening enabled for realtime-degraded session_id=...
```

一次问答播放结束后，不应继续自动出现：

```text
连续对话 VAD 触发新语音段
```

如果旧固件仍触发连续 VAD 空段，服务端应出现：

```text
已抑制连续 VAD 空语音段 segment_id=... input_stream_id=...
```

并且不应继续下发本轮 `assistant.reply` / `actuator.audio.play`。

### 验证

已执行：

```bash
uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_glass_esp32_runtime.py -q
uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_voice_runtime.py -q
uv run --with pytest python -m pytest openaiglass-sdk/tests/unit -q
```

结果：

1. ESP32 源码边界测试 3 条通过。
2. VoiceRuntime 单元测试 44 条通过。
3. SDK 全量 unit 通过。

### 设备级联调建议

本轮修复需要重新烧录真实 ESP32 固件后验证。联调顺序：

1. 同步配置：`uv run openaiglass.config.sync --app-root openaiglass-for-blind`。
2. 启动服务端并打开 `DEBUG` 日志。
3. 启动手机 App 或 `phone-mock`，确认设备绑定状态。
4. 烧录并启动新版 ESP32 眼镜。
5. 呼叫“嗨乐鑫”，说一句普通问题，例如“今天天气怎么样？”。
6. 等回复播放结束后保持安静 10 秒，观察眼镜不再自动开新段。
7. 再次呼叫“嗨乐鑫”，确认 WakeNet 仍能响应并开始新一轮。

---

<a id="iteration-v86"></a>
## iteration-v86：sdk-v86 受限连续对话和唤醒词打断修复

来源：`iteration-v86.md`


### 背景

2026-05-03 真机联调时，`sdk-v85` 已经阻断“空语音 + 自动抓拍 + 模型看图回复”的自循环，但也把真实 ESP32 的免唤醒连续对话完全关闭。业务体验上仍有三个问题：

1. 用户说“结束对话”“安静”等控制指令时，模型仍可能把它当成普通语音继续回复。
2. 连续窗口内背景音误触发时，仍可能产生空语音段和自动抓拍。
3. 播放期间用户无法主动打断当前回复。

### 变更

1. 真实 ESP32 恢复受限连续对话窗口：
   - `voice.realtime.session.open` 请求 `realtime_semantic_vad` 时，端侧回报 `continuous_dialog=true`。
   - 播放结束后冷却时间从 900ms 增加到 1500ms。
   - 连续 VAD 触发门槛从 4 帧增加到 10 帧。
   - 播放期间不允许普通 VAD 启动新语音段，避免扬声器回灌触发自循环。
2. 服务端拦截停止连续对话指令：
   - 旁路 ASR 转写命中“结束对话”“停止对话”“安静”“别说了”等短句时，不再进入 Omni/Agent 回复。
   - 服务端关闭本轮 Omni 预连接，丢弃本轮自动抓拍，并下发 `voice.dialog.close`。
   - 眼镜收到后关闭连续对话窗口，恢复 WakeNet 待命。
3. 播放中支持唤醒词打断：
   - 端侧播放期间继续运行 WakeNet，但不启动普通语音段。
   - 播放中命中 WakeNet 时，本地请求停止播放，并上报 `user.voice.interrupt`。
   - 服务端复用统一播放仲裁器，停止当前播报并清理待播队列。
   - 能力声明新增 `barge_in=true`、`output_cancel=true`、`barge_in_mode=wake_word`。

### 边界

当前 ESP32-S3 端侧仍没有可用 AEC，因此本轮不开放无唤醒词自然插话。播放期间只有“嗨乐鑫”级别的 WakeNet 打断是默认能力；普通说话仍可能被扬声器回灌污染，不能直接作为打断判据。

### 真机验证

已执行真实 ESP32 烧录和自动声场测试：

```bash
uv run openaiglass.glass.start \
  --repo-root . \
  --app-root openaiglass-for-blind \
  --sdk-root openaiglass-sdk \
  --config openaiglass-for-blind/host/glass/config/local_build.env \
  --build-dir /tmp/openaiglass-realtime-fix-build \
  --sdkconfig /tmp/openaiglass-realtime-fix-sdkconfig \
  --port /dev/cu.usbmodem2101 \
  --flash-only

LOG_LEVEL=DEBUG uv run openaiglass.server.start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env \
  --log-file openaiglass-for-blind/logs/realtime-device-test-server-auto-speech.log
```

观察到：

```text
semantic_continuous_requested=1 semantic_continuous_enabled=1
capabilities.barge_in=true
capabilities.output_cancel=true
capabilities.barge_in_mode=wake_word
```

用电脑扬声器播放“嗨乐鑫，结束对话”后，服务端日志出现：

```text
旁路 ASR 转写完成 ... text='结束对话。'
已发送控制消息: voice.dialog.close
已按用户指令关闭连续对话 ...
```

眼镜串口日志出现：

```text
WakeNet detected: segment_id=...
连续对话窗口已关闭: reason=conversation_stop_command
收到 voice.dialog.close，已关闭连续对话窗口并恢复 WakeNet 待命
```

该轮没有继续下发 `assistant.reply`、`actuator.audio.play` 或 Agent 最终回复。

### 自动化验证

已执行：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_glass_esp32_runtime.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py::VoiceRuntimeTestCase::test_empty_continuous_vad_segment_is_suppressed_before_model \
  openaiglass-sdk/tests/unit/test_voice_runtime.py::VoiceRuntimeTestCase::test_stop_command_closes_continuous_dialog_before_model \
  -q

uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/integration/test_control_register_flow.py \
  -q
```

结果均通过。第二组测试仅保留一个既有 PytestCollectionWarning，不影响本轮验证。

---

<a id="iteration-v87"></a>
## iteration-v87：sdk-v87 语音轮次意图裁决

来源：`iteration-v87.md`


### 背景

2026-05-03 真机联调发现，`sdk-v86` 已经支持停止指令和播放中唤醒词打断，但仍缺少完整对话层面的系统状态机：

1. 用户问“今天天气怎么样”这类非视觉问题时，SDK 仍可能在语音段开始阶段提前自动抓拍。
2. 回复播放结束后，背景音或扬声器回灌会触发短语音段。
3. 如果旁路 ASR 尚未最终返回，服务端会先把短音频提交给 Omni，模型可能回复“我在，文刀”并继续自循环。

### 变更

1. 增加 `VoiceTurnIntentDecision`，在语音段进入 Omni/Agent 前做系统层裁决：
   - `stop_conversation`：停止连续对话。
   - `ignore`：忽略误触发语音段。
   - `visual_query`：允许触发并上传自动照片。
   - `voice_query`：普通语音问题，不携带照片。
2. 取消 `sensor.audio.segment.started` 阶段的提前自动抓拍。
3. 只有命中视觉关键词的语音文本才调用自动抓拍并消费本轮照片。
4. 对短语音段增加旁路 ASR 前置等待：
   - 旁路 ASR 在 3 秒内返回有效文本时，用文本裁决。
   - 1.8 秒以内短语音段如果 ASR 仍未返回，按误触发忽略。
5. 在 Omni 返回转写后增加第二道裁决，防止早期裁决无法覆盖的空转写、语气词或助手回声继续进入 Agent。

### 真机验证

已用 macOS 合成语音从电脑扬声器播放：

```bash
say -v Tingting '嗨，乐鑫，今天天气怎么样'
```

观察结果：

1. 非视觉天气问题提交 Omni 时 `image_count=0`，没有自动照片进入模型。
2. 回复播放结束后，眼镜又误触发了一个 1344ms 的短语音段。
3. 服务端在提交 Omni 前关闭预连接，并下发 `voice.dialog.close`：

```text
语音段已由系统意图裁决忽略 segment_id=seg_22_8e1f80fd ... reason=empty_transcript close_continuous_dialog=True
已发送控制消息: voice.dialog.close
```

该误触发轮次没有继续下发 `assistant.reply` 或新的播放请求。

### 自动化验证

已执行：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py \
  openaiglass-sdk/tests/integration/test_control_register_flow.py \
  -q
```

结果通过。保留一个既有 `PytestCollectionWarning`，原因是集成测试里的 `TestWebSocketClient` 是辅助类且定义了 `__init__`。

---

<a id="iteration-v88"></a>
## iteration-v88：sdk-v88 连续 VAD 空段收口修复

来源：`iteration-v88.md`


### 背景

2026-05-03 真机联调发现，服务端在 `sdk-v87` 中已经能抑制连续 VAD 空语音段，但抑制路径只关闭服务端的 Omni 预连接并返回，没有向眼镜端发送任何“本轮结束”控制消息。

因此眼镜端在 `sensor.audio.segment.finished` 后仍处于等待服务端回复状态，直到 `SERVER_REPLY_TIMEOUT_MS=45000` 超时才恢复待命，表现为“聊着聊着没响应”。

### 变更

1. `VoiceRuntime._should_suppress_empty_continuous_segment(...)` 在确认抑制空段时，改为复用 `_close_segment_without_reply(...)`。
2. 该路径会同步下发：

```text
voice.dialog.close
```

3. 眼镜端已有 `voice.dialog.close` 处理，会关闭连续对话窗口、重置连续 VAD 门控、调用 `clear_reply_wait_state()` 并恢复 WakeNet 待命。

### 验证

已执行：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_glass_esp32_runtime.py \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py \
  openaiglass-sdk/tests/integration/test_control_register_flow.py \
  -q
```

结果通过。保留一个既有 `PytestCollectionWarning`，不影响本轮修复。

关键单测已更新：连续 VAD 空段被抑制时必须包含 `voice.dialog.close`，避免端侧等待回复超时。

---

<a id="iteration-v89"></a>
## iteration-v89：sdk-v89 Omni semantic_vad 主链路恢复

来源：`iteration-v89.md`


### 背景

真机联调发现，`sdk-v87` 到 `sdk-v88` 的 ASR 前置裁决虽然能挡住部分空段和误触发，但它把旁路 ASR 放到了 Omni Realtime 前面，带来三个问题：

1. 正常问答首响会被旁路 ASR 最终文本拖慢。
2. 空转写、背景音、附和声这类问题重复实现了 Omni `semantic_vad` 已经承担的职责。
3. “结束对话、安静、先这样”等自然指令更适合由模型理解后调用系统工具关闭连续窗口，而不是只靠 ASR 硬规则。

回声抑制仍然是端侧音频工程问题，不能交给 Omni 代替。当前版本只调整服务端连续对话主链路和关闭语义。

### 变更

1. `omni_realtime + realtime_semantic_vad` 不再强制降级为 `segment_turn`。预连接的 Omni 会话会使用真实服务端配置，让 Omni 官方 `semantic_vad` 负责是否自动响应。
2. 旁路 ASR 改为非阻塞辅助链路：
   - 默认只做日志、转写回填和调试观测。
   - 如果进入 Omni 前已经完成，可顺手处理停止指令、明显助手回声和明确视觉关键词。
   - 不再因为空 ASR、语气词或 ASR 等待超时阻塞 Omni。
3. 增加模型可见系统工具 `close_continuous_dialog`。模型识别到用户要求结束连续对话时调用该工具，SDK 会在当前回复播放完成后下发 `voice.dialog.close`。
4. 保留极轻系统硬保护：没有音频帧、没有 PCM 字节、极短异常段会直接丢弃并关闭端侧连续窗口。
5. 如果 Omni `semantic_vad` 返回没有自动响应，SDK 将本轮视为无有效用户请求，关闭连续窗口并恢复待命。

### 验证

已执行：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  -q
```

结果通过。

本轮尚未完成真实设备 A/B 对比。下一轮真机测试应对比：

1. 旧 ASR 前置策略与 `sdk-v89` Omni `semantic_vad` 直连策略的误触发率。
2. 用户说完到首段下行音频的首响延迟。
3. 背景音、助手回声和空段造成的 token 消耗。
4. 用户通过“结束对话、安静、先这样”自然结束连续窗口的成功率。

---

<a id="iteration-v90"></a>
## iteration-v90：sdk-v90 Omni 音频完成事件收口修复

来源：`iteration-v90.md`


### 背景

2026-05-04 真机联调发现，Omni Realtime 已经返回首段音频和完整助手文本，但服务端仍一直等待 `response.done`，导致 `/stream.wav` HTTP 流不结束。真实眼镜持续读取播放流，直到几十秒后读流失败，进而影响播放结束后的连续追问和再次唤醒。

对照阿里云百炼 Realtime server events 文档后，音频输出完成应优先使用 `response.audio.done`，`response.audio_transcript.done` 只代表音频转写文本完成，`response.done` 代表整体 response 对象完成。SDK 之前只把 `response.done` / `response.cancelled` 当作等待结束条件，事件使用过窄。

### 变更

1. Omni 主回复回调新增 `response.audio.done` 处理：
   - 没有待处理工具调用时，立即设置当前 Realtime 响应完成事件。
   - 播放流随后会 finalize，HTTP 下行流正常结束。
   - 如果当前响应正在执行工具调用，仍忽略旧响应的 audio done，避免工具首轮过早结束整轮。
2. 工具前置播报的 Omni Realtime 音频生成也支持 `response.audio.done` 收口，不再必须等待 `response.done`。
3. 保留 `response.done` / `response.cancelled` 兼容路径。
4. `response.audio_transcript.done` 继续只用于记录助手文本，不承担播放完成语义。

### 验证

已执行：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py \
  openaiglass-sdk/tests/integration/test_control_register_flow.py \
  -q
```

结果通过。保留既有 `PytestCollectionWarning`，不影响本轮修复。

本地 `openaiglass-for-blind/config/local_server.env` 没有配置 DashScope API Key，因此本轮未能在本机直接发起新的真实 Realtime 请求观测事件序列；修复依据来自官方 server events 文档、用户提供的真机日志和新增单元测试。

---

<a id="iteration-v91"></a>
## iteration-v91：sdk-v91 Omni 事件排障与非阻塞关闭

来源：`iteration-v91.md`


### 背景

`sdk-v90` 已经让 SDK 在收到 `response.audio.done` 后设置 Realtime 响应完成事件，但真机日志仍显示 `/stream.wav` 没有立即结束。日志顺序表明服务端已经打印 `Omni Realtime 音频输出完成`，随后仍等到 DashScope SDK 报 `request timeout after 23 seconds`，眼镜端才收到播放失败并恢复。

这说明新阻塞点不在音频完成事件识别，而在音频完成后同步调用 DashScope Realtime 会话 `close()`。底层 SDK 的关闭过程可能等待服务端响应或内部请求超时，导致 VoiceRuntime 还没来得及 finalize 播放流。

### 变更

1. Omni Realtime 主回复回调在 DEBUG 级别打印原始 server event 摘要：
   - 日志格式为 `Omni Realtime server event type=... payload=...`。
   - `response.audio.delta` 只打印 base64 长度，不打印完整音频内容。
   - 工具前置播报链路也打印同类事件摘要。
2. `OmniRealtimeStreamingSession.close(...)` 支持 `blocking=False`。
3. VoiceRuntime 在主回复完成后使用非阻塞关闭：
   - 先让 `finish(...)` 返回。
   - 先 finalize 下行播放流。
   - 再由后台线程关闭 DashScope Realtime 会话。
4. 被系统意图裁决忽略的预连接 Omni 会话也改为后台关闭，避免关闭动作影响 `voice.dialog.close` 下发。

### 验证

已执行：

```bash
uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_voice_runtime.py -q
```

结果通过。

新增单测覆盖：

1. 没有 `response.done`、只有 `response.audio.done` 时仍能正常返回。
2. DEBUG 日志中能看到 Omni server event 摘要。
3. `close(blocking=False)` 不等待底层 SDK close 完成。

---

<a id="iteration-v92"></a>
## iteration-v92：sdk-v92 Omni Realtime 长连接连续对话

来源：`iteration-v92.md`


更新时间：2026-05-04

### 背景

`sdk-v91` 解决了 `response.audio.done` 后同步关闭 DashScope Realtime 会话阻塞播放流的问题，但真机联调仍暴露出连续追问不稳定：普通每轮回复完成后模型连接被关闭，下一轮追问需要重新建连；如果 Omni `semantic_vad` 自动提交事件稍晚于端侧 `segment.finished`，SDK 会过早判定 `semantic_vad_no_auto_response` 并关闭连续窗口。

本轮按 [Omni Realtime 长连接连续对话重构设计](../structure-design/Omni-Realtime长连接连续对话重构设计.md) 落地第一阶段长连接能力。

### 变更

1. 新增 `VOICE_OMNI_SESSION_LIFECYCLE=per_turn|persistent` 配置，默认 `persistent`。
2. `VoiceSessionController` 持有设备级 `persistent_omni_realtime_session`，连续对话窗口内多轮语音复用同一条 Omni Realtime WebSocket。
3. `OmniRealtimeStreamingSession` 支持 `begin_turn(...)`：
   - 每轮重置响应事件、文本累积、response id、首包指标和工具计数。
   - 每轮刷新 instructions、tools、tool handler 和音频回调。
   - 普通 `response.audio.done` 只结束当前播放流，不关闭模型连接。
4. 用户主动结束、模型工具 `close_continuous_dialog`、端侧窗口关闭、控制连接关闭或不可恢复异常时，才后台关闭 persistent Omni 连接。
5. `semantic_vad` 未自动提交时增加短等待窗口，避免端侧 `segment.finished` 到达瞬间就误判关闭连续对话。
6. 运行态快照新增：
   - `omni_session_lifecycle`
   - `omni_persistent_connected`

### 对业务开发者的影响

1. 业务能力代码不需要修改。
2. 正常连续对话下，不应再看到每轮回复结束后都关闭 Omni Realtime WebSocket。
3. 如果真机联调需要回退旧行为，可在服务端配置中设置：

```env
VOICE_OMNI_SESSION_LIFECYCLE=per_turn
```

4. 仍然不要在业务侧自行关闭 Omni 连接；需要主动结束连续对话时，应让模型调用 SDK 内置 `close_continuous_dialog` 工具，或由端侧控制指令触发 `voice.dialog.close`。

### 验证

本轮代码级验证：

```bash
python -m py_compile openaiglass-sdk/server-python/runtime/voice_runtime.py openaiglass-sdk/server-python/infra/config/settings.py
uv run python -m unittest openaiglass-sdk.tests.unit.test_voice_runtime -v
```

真机验证应覆盖：

1. 一次唤醒后连续追问 3 轮，服务端只建立一条 persistent Omni 连接。
2. 每轮 `response.audio.done` 后眼镜播放流及时结束，但服务端不下发 `voice.dialog.close`。
3. 用户说“停下/安静/先这样”后，当前回复播报完成再下发 `voice.dialog.close` 并关闭 persistent Omni 连接。
4. DEBUG 日志中能看到每轮 `Omni Realtime server event type=...`，以及复用长连接时的 `Omni Realtime 长连接已刷新当前轮上下文`。

---

<a id="iteration-v93"></a>
## iteration-v93：sdk-v93 模型工具 reason 参数收敛

来源：`iteration-v93.md`


更新时间：2026-05-04

### 背景

真机联调中，`close_continuous_dialog` 工具调用结果里出现了由模型生成的 `reason` 字段，例如“用户表达希望助手安静，结束连续对话”。这个字段对 SDK 执行关闭连续对话没有实际必要，反而会让业务提示词误以为所有工具都必须要求模型解释调用原因。

本轮把模型可见工具契约收敛为“默认不需要 reason”。SDK 内部运行时如果需要记录关闭原因、播放原因或协议原因，继续使用系统默认值，不再要求模型或业务提示词提供。

### 变更

1. `close_continuous_dialog` 工具输入只保留 `mode`，工具结果只返回 `scheduled` 和 `mode`。
2. `capture_photo` 内置工具不再要求模型传入 `reason`，抓拍网关使用 SDK 默认系统原因。
3. `start_phone_video_link` 内置工具不再要求模型传入 `reason`。
4. `DeviceGroupContext.stop_phone_task(...)` 的 `reason` 参数改为可选，业务 Task 默认不需要提供。
5. 更新 SDK 安装与能力开发指南，说明模型工具默认不需要 `reason`，运行时日志里的原因是 SDK 系统字段。

### 对业务开发者的影响

1. 提示词中不要再要求模型为工具调用生成 `reason`。
2. 业务 Tool/Task 通过 `DeviceGroupContext` 控制设备时，也可以省略 `reason`，除非业务确实需要在自己的日志里记录细分原因。
3. 看到 `voice.dialog.close` 控制消息中带有 `reason=model_requested` 是正常现象，这表示 SDK 运行时默认关闭原因，不是模型生成内容。

### 验证

本轮建议验证：

```bash
python -m py_compile \
  openaiglass-sdk/server-python/agent_core/tools/builtins/close_continuous_dialog.py \
  openaiglass-sdk/server-python/agent_core/tools/builtins/capture_photo.py \
  openaiglass-sdk/server-python/agent_core/tools/builtins/start_phone_video_link.py \
  openaiglass-sdk/server-python/openaiglasses/runtime/device_group.py

uv run python -m unittest \
  openaiglass-sdk.tests.unit.test_agent_core \
  openaiglass-sdk.tests.unit.test_voice_runtime \
  openaiglass-sdk.tests.integration.test_agent_phase_e_flow \
  -v
```

---

<a id="iteration-v94"></a>
## iteration-v94：sdk-v94 ESP32 WakeNet SR 任务栈稳定性修复

来源：`iteration-v94.md`


更新时间：2026-05-04

### 背景

真机联调中，眼镜在呼叫唤醒词后出现：

```text
***ERROR*** A stack overflow in task sr_pipeline_tas has been detected.
```

崩溃发生在 ESP32 端 `sr_pipeline_task`。该任务原本只有 8KB 栈，但任务内同时持有预取音频环形缓冲；唤醒命中后还会同步播放本地提示音，扬声器恢复路径中也有较大的局部静音帧。两者叠加后，在 WakeNet 命中路径上容易触发 SR 任务栈溢出。

### 变更

1. 将 ESP32 端 SR 预取音频环形缓冲从 `sr_pipeline_task` 局部栈变量移到静态存储。
2. 将扬声器 I2S 恢复和播放启动使用的静音预装帧从局部栈变量移到静态常量。
3. 将 `sr_pipeline_task` 栈大小从 8KB 提升到 12KB，给 WakeNet、VAD、控制消息构造和本地提示音路径留出余量。
4. 不改变业务协议、不改变连续对话状态机、不改变 Tool/Task 扩展面。

### 对业务开发者的影响

业务能力代码不需要修改。本轮只修复真实 ESP32 眼镜端在唤醒词触发后的稳定性问题。

如果真机上仍看到 `唤醒提示音写入失败: ESP_ERR_TIMEOUT`，需要继续观察是否是扬声器 I2S 通道在上一次播放后未正常恢复；本轮已经先消除该路径上的栈溢出风险。

### 验证

已完成：

```bash
uv run openaiglass.glass.build --repo-root .
```

结果：ESP32-S3 固件编译通过，`glass_main.bin` 大小约 `0x16dc80`，分区剩余约 29%。

真机烧录验证暂被串口下载握手阻塞：

```text
Failed to connect to ESP32-S3: No serial data received.
```

需要将眼镜板进入下载模式后继续执行：

```bash
uv run openaiglass.glass.start --repo-root . --flash-only
uv run openaiglass.glass.start --repo-root . --monitor-only
```

真机验证重点：

1. 连续呼叫唤醒词 3 次，不再出现 `sr_pipeline_tas` 栈溢出。
2. 唤醒提示音正常播放，不出现或显著减少 `ESP_ERR_TIMEOUT`。
3. 首轮对话、连续追问、用户主动结束连续对话仍保持 `sdk-v92`/`sdk-v93` 的行为。

---

<a id="iteration-v95"></a>
## iteration-v95：sdk-v95 模型自决视觉拍照链路

来源：`iteration-v95.md`


本轮对应对外 SDK 版本：`sdk-v95`。

### 背景

真机连续对话调试中，SDK 曾通过旁路 ASR 关键词做视觉意图裁决，只在命中“看看、前面、画面、障碍物”等词时才触发并上传照片。这个策略虽然能减少无关看图，但它和 Omni 模型本身的语义理解形成了两套意图系统：SDK 规则可能误判、漏判，也会让业务侧难以解释为什么模型没有拿到照片。

本轮将视觉问答统一收敛到模型工具调用：模型理解用户是否需要当前画面；需要时调用 SDK 内置 `capture_photo`，SDK 完成真实抓拍并把照片交回当前模型链路。

### 主要改动

1. 默认模型工具面重新暴露 `capture_photo`，并标记为全局系统工具；即使 Skill 白名单激活，也不会屏蔽用户请求当前画面的基础能力。
2. 移除语音运行时中的视觉关键词前置裁决。旁路 ASR 仍可在已就绪时处理停止对话和明显助手回声，但不再判断视觉意图。
3. Omni Realtime function calling 调用 `capture_photo` 后，SDK 会读取工具产生的图片资产，并通过 `append_video(...)` 追加到同一条 Realtime 会话，再触发后续响应。
4. 普通 Agent/TTS 链路恢复 `capture_photo` 后的图片解读主链路：工具输出完成后切到多模态图片回答，而不是只把图片路径作为普通 JSON 返回给模型。
5. 系统提示词明确要求：需要当前视觉信息时调用 `capture_photo`；普通聊天、时间天气、记忆维护、导航规划等不需要当前画面时不要调用。

### 验证

已执行：

```bash
uv run --with pytest --python 3.11 python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py::AgentCoreTestCase::test_tool_registry_exposes_expected_model_facing_tools \
  openaiglass-sdk/tests/unit/test_agent_core.py::AgentCoreTestCase::test_skill_runtime_read_skill_activates_session_and_filters_tools \
  openaiglass-sdk/tests/unit/test_voice_runtime.py::VoiceRuntimeTestCase::test_dashscope_omni_realtime_appends_capture_photo_tool_image \
  openaiglass-sdk/tests/unit/test_voice_runtime.py::VoiceRuntimeTestCase::test_voice_turn_intent_does_not_preclassify_visual_query
```

结果：`4 passed`。

### 业务侧影响

1. 业务 Skill 不需要实现“看图意图识别”，也不要注册同名 `capture_photo`。
2. 视觉类提示词应描述业务目标，不要要求业务代码先拍照再问模型。
3. 联调时观察 `Omni Realtime 工具调用请求 tool_name=capture_photo` 和 `Omni Realtime 已追加 capture_photo 工具图片`，即可确认模型自决视觉链路生效。

---

<a id="iteration-v96"></a>
## iteration-v96：sdk-v96 Omni Realtime 事件日志收敛

来源：`iteration-v96.md`


更新时间：2026-05-04

### 背景

真机连续对话日志中，Omni Realtime 会把每个 `response.audio.delta` 音频分片都打印为 DEBUG 日志。每个分片只包含音频 base64 长度摘要，但频率很高，会淹没 `response.audio.done`、`response.done`、工具调用和播放状态等关键事件。

同一批日志里还出现了：

```text
ERROR-dashscope Request failed ... request timeout after 23 seconds.
```

从事件顺序看，主链路已经收到 `response.audio_transcript.done`、`response.audio.done` 和 `response.done`，并且后续仍能进入下一轮对话。因此这不是 Omni 回复缺少结束事件，更像是 DashScope SDK 内部某条后台请求或旁路实时 ASR 收尾超时打印出的供应商日志。该异常不应被误判为当前回复没有完成。

### 变更

1. 不再逐帧打印 `response.audio.delta` server event。
2. 保留 `response.audio.done`、`response.done`、工具调用、输入语音事件和错误事件日志。
3. `session.created` / `session.updated` 的日志 payload 改为摘要，只记录模型、音色、turn detection、工具数量和 instructions 长度，不再把完整系统提示词和工具 schema 打进日志。
4. 更新单测，验证 `response.audio.done` 仍可收口播放流，同时确认音频 delta 摘要不再出现在日志里。

### 对业务开发者的影响

业务能力代码不需要修改。

真机排障时，如果看到模型回复后仍有 DashScope timeout，但同一轮已经出现 `response.audio.done` / `response.done`，应优先判断为后台旁路链路或供应商 SDK 收尾日志，不要直接认为 Omni 主回复没有结束。

### 验证

```bash
uv run python -m unittest openaiglass-sdk.tests.unit.test_voice_runtime -v
```

结果：53 个语音运行时单测全部通过。

---

<a id="iteration-v97"></a>
## iteration-v97：sdk-v97 语音模型服务边界抽象

来源：`iteration-v97.md`


更新时间：2026-05-04

### 背景

本轮开始按《Omni Server 与 Text Server 模态隔离设计》第 13 节分阶段实施。第一阶段目标是先把配置和代码概念里的模型服务边界立起来，避免继续用 `VOICE_REPLY_MODE` 同时表达“模型服务类型、输入模式、下行音频来源、连续对话策略”。

同时复核百炼 Omni Realtime 官方文档：Omni 链路应维护 Realtime WebSocket 长连接，持续追加音频，使用服务端 turn detection / `semantic_vad` 自动提交用户 turn；`response.audio.done` 用于收口当前音频输出，不应因此关闭连续对话模型连接。

### 变更

1. 新增 `ServerSettings.voice_server_mode`，支持 `omni_server` 和 `text_server`。
2. 新增环境变量 `VOICE_SERVER_MODE` 与 YAML 配置 `voice.server_mode`。
3. 保留旧 `VOICE_REPLY_MODE`，并做兼容映射：
   - `VOICE_REPLY_MODE=omni_realtime` -> `VOICE_SERVER_MODE=omni_server`
   - `VOICE_REPLY_MODE=agent_tts` -> `VOICE_SERVER_MODE=text_server`
4. `effective_voice_input_mode()` 改为基于有效 server mode 判断：
   - `omni_server` -> `raw_audio`
   - `text_server` -> `asr_text`
5. 运行时主分支改用 `effective_voice_server_mode()`，不再直接用旧 `voice_reply_mode` 判断 Omni/Text 热路径。
6. 新增内部 `VoiceServer` 协议和 `VoiceGateway`，作为后续 Phase 2/3 抽出 `OmniVoiceServer`、`TextVoiceServer` 的稳定入口。
7. 新增 `runtime.omni` 包入口，先导出当前 Omni Realtime 类型，后续把 DashScope Realtime 热路径逐步迁入该包。

### 对业务开发者的影响

业务 Tool、Task、Skill 不需要改。

新项目建议使用：

```yaml
voice:
  server_mode: omni_server
```

旧配置仍可继续使用 `VOICE_REPLY_MODE=omni_realtime` 或 `VOICE_REPLY_MODE=agent_tts`。如果同时配置新旧字段，必须保持一致；例如 `VOICE_SERVER_MODE=omni_server` 不能搭配 `VOICE_REPLY_MODE=agent_tts`。

### 后续阶段

1. Phase 2：把 `DashscopeOmniRealtimeReplyClient`、`OmniRealtimeStreamingSession` 和 Realtime tool bridge 迁入 `runtime/omni`。
2. Phase 3：把 ASR、文本意图、Text Agent、TTS 迁入 `runtime/text`。
3. Phase 4：废弃旧 `VOICE_REPLY_MODE` 内部主分支，只保留迁移兼容。

### 验证

```bash
uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_settings.py \
  openaiglass-sdk/tests/unit/test_server_cli.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py -q
```

结果：通过。

---

<a id="iteration-v98"></a>
## iteration-v98：sdk-v98 Omni/Text Server 适配器落地

来源：`iteration-v98.md`


更新时间：2026-05-04

### 背景

`sdk-v97` 已经新增 `voice.server_mode` 和 `VoiceGateway`。本轮继续推进设计文档第 13 节 Phase 2、Phase 3 和 Phase 4，但保持一个关键约束：真机语音链路已经多轮修复过，不能为了物理搬文件一次性重写 Realtime 回调、播放仲裁、ASR 和 TTS 热路径。

因此本轮采用 adapter-first 的迁移方式：先让代码边界、配置选择和状态机归属稳定，再逐步迁移内部实现。

### 变更

1. 新增 `runtime/omni/omni_voice_server.py`。
   - `OmniVoiceServer` 只在 `VOICE_SERVER_MODE=omni_server` 下可用。
   - 当前委托已稳定的 `VoiceRuntime` Omni 热路径。
2. 新增 `runtime/text/text_voice_server.py`。
   - `TextVoiceServer` 只在 `VOICE_SERVER_MODE=text_server` 下可用。
   - 当前委托已稳定的 ASR -> Agent -> TTS 热路径。
3. 新增 `runtime/text/text_dialog_state_machine.py`。
   - Text Server 的停止指令、空文本、语气词、助手回声和短连续 VAD 文本规则集中到 `TextDialogStateMachine`。
   - `VoiceRuntime` 的文本裁决路径改为调用该状态机。
4. `VoiceGateway.from_runtime(...)` 按 `effective_voice_server_mode()` 返回 `OmniVoiceServer` 或 `TextVoiceServer`。
5. `ControlRuntime` 创建 `VoiceGateway`，后续控制入口可以逐步从直接依赖 `VoiceRuntime` 迁移到 server adapter。
6. runtime snapshot 增加 `voice_server_mode`。
7. package-check 的安装导入验证增加：
   - `runtime.voice_gateway`
   - `runtime.omni.omni_voice_server`
   - `runtime.text.text_voice_server`
   - `runtime.text.text_dialog_state_machine`

### 仍保留的兼容

1. `VOICE_REPLY_MODE` 尚未删除，只作为迁移兼容字段。
2. DashScope Omni Realtime 客户端、ASR 和 TTS 类仍在 `VoiceRuntime` 文件内，后续再物理迁移。
3. Omni Server 仍不使用 Text Server 状态机做误触发主裁决；sidecar ASR 只保留日志、回填和低风险辅助。

### 对业务开发者的影响

业务代码不需要修改。推荐配置仍是：

```yaml
voice:
  server_mode: omni_server
```

如果业务明确需要纯文本模型链路，可配置：

```yaml
voice:
  server_mode: text_server
```

### 验证

```bash
uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_server_boundaries.py \
  openaiglass-sdk/tests/unit/test_settings.py \
  openaiglass-sdk/tests/unit/test_server_cli.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_package_check.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q
```

结果：通过。

---

<a id="iteration-v99"></a>
## iteration-v99：sdk-v99 语音运行时代码物理拆分

来源：`iteration-v99.md`


更新时间：2026-05-04

### 背景

`voice_runtime.py` 已经超过 7000 行，里面同时包含共享数据结构、Omni Realtime 客户端、ASR/TTS 客户端、播放队列、通知、Task 事件和设备会话编排。继续在一个文件里叠加逻辑会让 Omni Server / Text Server 的模态隔离难以落地，也会增加真机问题排查成本。

本轮在不改变设备协议和运行行为的前提下，先把纯客户端和共享模型迁出 `VoiceRuntime`。

### 变更

1. 新增 `runtime/voice_constants.py`。
   - 收敛语音采样率、播放队列、Omni semantic VAD 兜底等共享常量。
2. 新增 `runtime/voice_models.py`。
   - 收敛 `ModelChunk` 等模型流式分片数据结构。
3. 新增 `runtime/model_payloads.py`。
   - 收敛模型返回文本提取、音频 data URL 和对象/字典字段读取工具。
4. 新增 `runtime/omni/realtime_client.py`。
   - 迁入 `DashscopeOmniRealtimeReplyClient`、`OmniRealtimeStreamingSession`、`OmniRealtimeReplyResult` 和 Omni server event 摘要逻辑。
5. 新增 `runtime/text/speech_clients.py`。
   - 迁入 `VoiceModelClient`、`DashscopeVoiceModelClient`、`SpeechRecognitionClient`、`DashscopeSpeechRecognitionClient`、`StreamingTtsSession`、`DashscopeCosyVoiceTtsSession` 和实时 ASR 会话。
6. `runtime.voice_runtime` 保留兼容导入。
   - 业务或测试中从 `runtime.voice_runtime` 导入上述类仍然可用。
   - 本轮只做物理拆分，不改变热路径行为。
7. package-check 增加新模块导入覆盖。

### 效果

`voice_runtime.py` 从 7356 行下降到 4660 行。后续可以继续按播放、通知、Task 事件、会话状态机等维度拆分。

### 对业务开发者的影响

业务代码不需要修改。公开配置仍然是：

```yaml
voice:
  server_mode: omni_server
```

如果业务侧曾经从 `runtime.voice_runtime` 导入测试替身，本轮仍兼容；但新代码建议按真实归属从 `runtime.omni.realtime_client` 或 `runtime.text.speech_clients` 导入。

### 验证

```bash
uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_voice_server_boundaries.py -q

uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_voice_server_boundaries.py \
  openaiglass-sdk/tests/unit/test_settings.py \
  openaiglass-sdk/tests/unit/test_server_cli.py \
  openaiglass-sdk/tests/unit/test_package_check.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q

uv run --python 3.11 --with pytest --with setuptools --with wheel \
  openaiglass.sdk.package-check --repo-root .
```

结果：通过；package-check 返回 `ok: true`。

---

<a id="iteration-v100"></a>
## iteration-v100：sdk-v100 共享状态与音频工具拆分

来源：`iteration-v100.md`


更新时间：2026-05-04

### 背景

`sdk-v99` 已经把 Omni Realtime 客户端和 Text ASR/TTS 客户端从 `voice_runtime.py` 迁出。本轮继续处理剩余的大文件问题，先拆出不会改变运行行为的共享状态模型和音频工具，为后续拆播放、通知和进度播报缓存做准备。

### Phase 1-4 回顾

1. Phase 1 抽象边界：`sdk-v97` 已完成 `VoiceServer`、`VoiceGateway` 和 `voice.server_mode`。
2. Phase 2 抽出 Omni Server：`sdk-v98` 建立 Omni 适配器，`sdk-v99` 迁出 Omni Realtime 客户端；后续还要拆 Realtime tool bridge 和会话生命周期管理。
3. Phase 3 抽出 Text Server：`sdk-v98` 建立 Text 适配器和 TextDialogStateMachine，`sdk-v99` 迁出 ASR/TTS 客户端；后续还要拆 Text Agent Adapter。
4. Phase 4 清理旧分支：目前仍保留 `runtime.voice_runtime` 兼容导入和 `VOICE_REPLY_MODE` 映射，后续等物理边界更稳定后再收紧。

### 本轮变更

1. 新增 `runtime/audio_utils.py`。
   - 迁入 `PCM16StreamResampler`。
   - 迁入 `build_wav_bytes(...)` 和 `wav_header_unknown_size(...)`。
2. 新增 `runtime/voice_state.py`。
   - 迁入 `MessageEntry`、`VoiceTurnIntentDecision`、`SegmentBuffer`、`PlaybackStreamContext`、`VoiceSessionController`、`ReplySynthesisContext`、`ProgressAudioCacheEntry`。
3. `runtime.voice_runtime` 保留旧导入兼容。
   - 旧测试继续可以从 `runtime.voice_runtime` 导入这些类和函数。
4. package-check 增加 `runtime.voice_state` 和 `runtime.audio_utils` 导入验证。

### 效果

`voice_runtime.py` 从 `sdk-v99` 的 4663 行下降到 4385 行。剩余主体已经更清晰地集中在设备会话、播放、通知、Task 事件和模型管线编排。

### 下一步计划

1. 拆播放子系统：把 `PlaybackStreamContext` 之外的播放队列操作、chunked WAV 输出和中断清理辅助函数迁入播放模块。
2. 拆进度播报缓存：把工具前置播报缓存预热、读取、淘汰和 profile 判断收敛成独立 service。
3. 拆通知和 Task 事件：把通知直出、TaskEvent -> AgentTurn 回流和优先级处理从 `VoiceRuntime` 主体中移出。
4. 再收紧 `OmniVoiceServer` / `TextVoiceServer` 对具体子模块的直接拥有关系。

### 验证

```bash
uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_voice_server_boundaries.py -q
```

结果：通过，57 个测试通过。
