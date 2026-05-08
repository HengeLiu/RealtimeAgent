# SDK 文档入口

本目录只保留 SDK 开发框架相关文档：三端协议、统一模型、运行时、公共契约、SDK 安装发布、SDK 测试、联调方法和迭代记录。已经被当前实现取代的阶段计划和验收拆解文档已清理，避免旧计划继续被当作待办事项。

| 目录 | 内容 |
| --- | --- |
| [structure-design](./structure-design) | 协议、媒体帧、设备注册、agent-core、backend-task-core、Skill Runtime、手机/眼镜 SDK 运行时设计。 |
| [sdk-design](./sdk-design) | SDK 产品形态、开发者快速开始、测试架构、真机联调、打包和发布流程。 |
| [stage2](./stage2) | SDK 产品化过程中的迭代记录；`v1-v100` 已合并为汇总文档，后续迭代按单文件保留事实变更和验证命令。 |
| [experimental](./experimental) | SDK 相关调研、Spike 和实验 demo。 |

当前应优先阅读：

1. [SDK 开发者快速开始](./sdk-design/SDK开发者快速开始.md)
2. [SDK 产品形态与多端职责定义](./sdk-design/SDK产品形态与多端职责定义.md)
3. [SDK 公共契约设计](./structure-design/SDK公共契约设计.md)
4. [语音对话协议与时序设计](./structure-design/语音对话协议与时序设计.md)
5. [全双工实时语音对话设计](./structure-design/全双工实时语音对话设计.md)
6. [Omni Realtime 长连接连续对话重构设计](./structure-design/Omni-Realtime长连接连续对话重构设计.md)
7. [Omni Server 与 Text Server 模态隔离设计](./structure-design/Omni-Server与Text-Server模态隔离设计.md)
8. [Omni Server 与 Text Server 拆分验收文档](./structure-design/Omni-Server与Text-Server拆分验收文档.md)
9. [SDK 真机联调前检查与联调步骤](./sdk-design/SDK真机联调前检查与联调步骤.md)

迭代记录：

1. [SDK v110 迭代记录](./stage2/iteration-v110.md)：ESP32 控制连接重建兜底。
2. [SDK v109 迭代记录](./stage2/iteration-v109.md)：ESP32 控制连接自动重连修复。
3. [SDK v108 迭代记录](./stage2/iteration-v108.md)：Omni persistent 长连接忽略 turn 修复。
4. [SDK v106 迭代记录](./stage2/iteration-v106.md)：继续压缩 VoiceRuntime 系统辅助职责。
5. [SDK v105 迭代记录](./stage2/iteration-v105.md)：轮次记录器与最终边界验收。
6. [SDK v104 迭代记录](./stage2/iteration-v104.md)：Omni 工具桥与 Text Agent Adapter 拆分。
7. [SDK v103 迭代记录](./stage2/iteration-v103.md)：拆出通知与 Task 事件到语音播放的桥接层。
8. [SDK v102 迭代记录](./stage2/iteration-v102.md)：拆出工具前置播报静态音频缓存管理器。
9. [SDK v101 迭代记录](./stage2/iteration-v101.md)：拆出播放流队列和 HTTP chunked WAV 输出基础逻辑。
10. [SDK v1-v100 迭代记录汇总](./stage2/iteration-v1-v100.md)：合并前 100 个编号范围内实际存在的 91 份迭代记录。
11. [全部迭代记录](./stage2/)

盲人 AI 眼镜业务文档放在 [../../openaiglass-for-blind/docs](../../openaiglass-for-blind/docs)。
