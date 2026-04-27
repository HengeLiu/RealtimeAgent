# SDK v4 迭代记录

本文记录 SDK 团队根据 `SDK对功能开发支持情况的说明.md` 继续进行的第三轮优化。业务侧版本记录更新为 `sdk-v4`。

## 1. 输入反馈

第 8 项后台任务管理和第 10 项大模型创建手机与眼镜直连后台任务已经能完成最小演示，但还缺少产品化任务语义：

1. `phone_video_link_task` 只能触发 `sensor.camera.stream.start/stop`，不能接收 peer-link 或 camera stream 事件。
2. 手机端准备失败、链路断开、视频开始、视频停止等状态不能回流到任务运行态。
3. 错误手机上报任务事件时缺少统一校验和结构化错误。
4. 业务团队难以通过任务查询判断视频链路当前处于准备、已就绪、推流中、失败或结束。

本轮明确暂缓实时语音打断、全双工语音、真实公网/NAT 穿透、iOS/ESP32 包化和多视觉任务并发调度。

## 2. 本轮 SDK 改动

### 2.1 系统任务事件派发

`HybridTaskGateway.dispatch_event(...)` 现在可同时路由 SDK 业务任务和 SDK 系统任务。`phone_video_link_task` 不再只能创建和取消，也可以通过统一事件入口接收端侧上报。

### 2.2 `phone_video_link_task` 生命周期

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

### 2.3 标准端侧事件

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

### 2.4 ControlRuntime 集成

`ControlRuntime.report_task_event(...)` 现在可用于 `phone_video_link_task`。服务端会先查询任务绑定的 `phone_device_id`，上报手机不匹配时返回结构化 `INVALID_MESSAGE`，避免非绑定手机污染任务状态。

`ControlRuntime` 也会在任务完成或失败后清理活动视频任务映射，避免调试停止接口长期指向已结束任务。

## 3. 本轮不进入 SDK 的内容

1. 实时语音打断、电话式实时对话和用户播放期插话。
2. 真实公网/NAT 穿透、TURN/STUN、跨网络重试和链路健康检查。
3. AMap 真实 adapter、手机端 YOLO 执行框架和多视觉任务并发调度。
4. iOS SDK / ESP32 SDK 的正式包化与发布兼容策略。

这些内容仍属于后续 SDK 系统层迭代，不应由业务能力目录自行补齐。

## 4. 文档同步

已同步更新：

1. `SDK对功能开发支持情况的说明.md`
2. `openaiglass-for-blind/SDK安装与能力开发指南.md`
3. `openaiglass-for-blind/sdk-version`

## 5. 验证范围

新增和调整测试覆盖：

1. 创建 `phone_video_link_task` 后检查初始 `state/context/event`。
2. 派发 `peer_link.ready`、`camera.stream.started` 后检查任务进入 `running/streaming`。
3. 派发 `peer_link.failed` 后检查任务进入 `failed`，并保留结构化错误。
4. 取消任务后检查 `task.cancelled`，重复取消保持幂等。
5. 通过 `/api/tasks/report-event` 验证手机上报事件可推进任务阶段。
6. 验证错误手机上报事件会被服务端拒绝。
