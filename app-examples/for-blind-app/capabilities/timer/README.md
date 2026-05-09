# timer 迁移样板

旧能力价值：创建、查询、取消计时器，到点后触发用户提示。

audio-chat 迁移路径：

1. Tool 解析用户时长后创建 `timer` Task。
2. Task 持久化状态，支持恢复、取消和超时。
3. 到点信号通过 `TaskSignalBridge` 回流。
4. 用户提示进入 Output Service，由播放仲裁决定播报、排队或丢弃。

参考：

- `app-examples/for-blind-app/capabilities/timer/task.py`
- `app-examples/for-blind-app/templates/notification_task/task.py`

验收要求：

- 创建、取消、自然到点至少有契约测试。
- 重启恢复场景由 Task Store 覆盖。
- 不在业务 Task 中自建播放队列。
