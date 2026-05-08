# for-blind 回放测试数据

本目录给 `app-examples/for-blind-app` 的老业务能力迁移样板保留测试数据入口。

当前 F 线使用 `host/glass-playback/old-sdk-parity-capabilities.yaml` 中的内联 mock
RGB payload 完成设备级回放；后续接入真实脱敏图片、视频、地图和传感器样例时，
优先放在本目录下，再由 playback 配置引用文件路径。
