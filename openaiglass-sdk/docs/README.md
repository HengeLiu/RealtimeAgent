# SDK 文档入口

本目录只保留 SDK 开发框架相关文档：三端协议、统一模型、运行时、公共契约、SDK 安装发布、SDK 测试、联调方法和迭代记录。已经被当前实现取代的阶段计划和验收拆解文档已清理，避免旧计划继续被当作待办事项。

| 目录 | 内容 |
| --- | --- |
| [structure-design](./structure-design) | 协议、媒体帧、设备注册、agent-core、backend-task-core、Skill Runtime、手机/眼镜 SDK 运行时设计。 |
| [sdk-design](./sdk-design) | SDK 产品形态、开发者快速开始、测试架构、真机联调、打包和发布流程。 |
| [stage2](./stage2) | SDK 产品化过程中的迭代记录，按 `iteration-v*.md` 保留事实变更和验证命令。 |
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

1. [SDK v103 迭代记录](./stage2/iteration-v103.md)：拆出通知与 Task 事件到语音播放的桥接层。
2. [SDK v102 迭代记录](./stage2/iteration-v102.md)：拆出工具前置播报静态音频缓存管理器。
3. [SDK v101 迭代记录](./stage2/iteration-v101.md)：拆出播放流队列和 HTTP chunked WAV 输出基础逻辑。
4. [SDK v100 迭代记录](./stage2/iteration-v100.md)：拆出共享语音状态模型和 PCM/WAV 音频工具，继续缩小 `voice_runtime.py`。
5. [SDK v99 迭代记录](./stage2/iteration-v99.md)：把 Omni Realtime 客户端、ASR/TTS 客户端和共享模型从 `voice_runtime.py` 物理拆分到独立模块。
6. [SDK v98 迭代记录](./stage2/iteration-v98.md)：新增 Omni/Text Server 适配器和 TextDialogStateMachine，继续推进模态隔离。
7. [SDK v97 迭代记录](./stage2/iteration-v97.md)：新增 `voice.server_mode`，开始落地 Omni Server / Text Server 模态隔离边界。
8. [SDK v96 迭代记录](./stage2/iteration-v96.md)：收敛 Omni Realtime server event 日志，避免逐帧音频 delta 淹没关键事件。
9. [SDK v95 迭代记录](./stage2/iteration-v95.md)：移除视觉前置关键词裁决，改由 Omni 模型调用 `capture_photo` 自行获取照片并回答。
10. [SDK v93 迭代记录](./stage2/iteration-v93.md)：模型可见工具默认不再要求 `reason`，运行时原因由 SDK 系统字段生成。
11. [SDK v92 迭代记录](./stage2/iteration-v92.md)：Omni Realtime 默认改为设备语音会话级 persistent 长连接，普通轮次只收口播放流，不关闭模型连接。
12. [SDK v91 迭代记录](./stage2/iteration-v91.md)：打印 Omni server event 摘要，并把 Realtime 会话关闭改为后台执行，避免底层 close 阻塞播放流收口。
13. [SDK v90 迭代记录](./stage2/iteration-v90.md)：使用 `response.audio.done` 收口 Omni 下行播放流，避免只等 `response.done` 导致眼镜播放卡住。
14. [SDK v89 迭代记录](./stage2/iteration-v89.md)：恢复 Omni `semantic_vad` 主链路，旁路 ASR 非阻塞化，并新增模型工具关闭连续对话。
15. [SDK v88 迭代记录](./stage2/iteration-v88.md)：连续 VAD 空段被抑制时同步关闭端侧连续窗口，避免眼镜等待回复超时。
16. [SDK v87 迭代记录](./stage2/iteration-v87.md)：增加语音轮次意图裁决，阻断非视觉问题自动抓拍和短误触发自回复。
14. [SDK v84 迭代记录](./stage2/iteration-v84.md)：外部 MCP Server client、Task 通用调度、终态回流策略和设备组通知播报链路修复。
15. [SDK v82 迭代记录](./stage2/iteration-v82.md)：服务端配置分层与 YAML 化。
16. [SDK v69 迭代记录](./stage2/iteration-v69.md)：工具调用前置播报，减少耗时 Tool 执行期间静默等待。
17. [全部迭代记录](./stage2/)

盲人 AI 眼镜业务文档放在 [../../openaiglass-for-blind/docs](../../openaiglass-for-blind/docs)。
