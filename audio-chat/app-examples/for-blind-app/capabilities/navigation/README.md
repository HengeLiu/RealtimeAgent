# navigation 迁移样板

旧能力价值：准备目的地、规划路线、执行期结合位置、航向和视觉事件给用户导航。

audio-chat 迁移路径：

1. Tool 调用 MCP 或 provider 做 POI、地理编码和路线准备。
2. Task 监听位置、航向、偏航、接近终点和视觉确认事件。
3. 摄像头、IMU、location 这类持续数据走 `sensor.*` stream 或小型语义事件。
4. 导航提示进入 Output Service，优先级按安全等级设置。

参考：

- `docs/phase3-migration-guide.md` 的 MCP Adapter 迁移章节。
- `examples/migration-templates/continuous_rgb_analyze/task.py` 的连续传感器消费方式。

验收要求：

- mock 路线准备不需要真实地图 key。
- 配置真实地图 key 时，provider 错误要结构化记录，不能伪装成功。
- 端侧导航动作通过 event / stream 表达。
