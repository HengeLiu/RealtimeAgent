# iteration-v14：统一播放仲裁和用户打断

## 本轮目标

补齐服务端播放通道的统一仲裁能力，让普通 Agent 回复、任务通知、视觉告警和用户主动打断都进入 SDK 中央播放策略，不再由业务层或单一路径直接控制播放器。

## 主要改动

1. 新增 `runtime.playback_arbiter.PlaybackArbiter`，统一维护活动播放意图、待播队列和最近决策。
2. `VoiceRuntime` 创建播放流时会生成 `PlaybackIntent`，支持 `play_now`、`queue`、`interrupt` 和 `user_interrupt` 决策。
3. 高优先级视觉告警或任务通知可以按 `interrupt_policy` 抢占普通 Agent 回复，旧播放流会被标记为 `interrupted` 并下发 `actuator.audio.interrupt`。
4. 新增 `user.voice.interrupt` 控制消息入口，支持停止当前播报并按 `clear_queue` 清理待播队列。
5. 运行态快照新增 `active_playback_intent`、`pending_playback_intents`、`recent_playback_decisions`，用于解释播放、排队、抢播和用户打断原因。

## 当前边界

1. 本轮完成的是半双工用户主动打断，不是全双工实时语音。
2. `resume_policy` 第一版以 `drop_interrupted` 为主，尚未实现断点恢复或摘要补偿。
3. `NotificationCoordinator` 仍保留通知去重和通知级队列职责，播放层统一收敛到 `PlaybackArbiter`。
4. 账号权限、组织管理、远程配置中心和 SQLite 任务持久化仍是后续优先项。

## 验证结果

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_playback_arbiter.py openaiglass-sdk/tests/unit/test_voice_runtime.py openaiglass-sdk/tests/unit/test_task_event_runtime.py -q
python -m compileall -q openaiglass-sdk/server-python
```
