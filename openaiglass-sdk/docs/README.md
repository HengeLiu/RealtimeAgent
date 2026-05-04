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
6. [SDK 真机联调前检查与联调步骤](./sdk-design/SDK真机联调前检查与联调步骤.md)

迭代记录：

1. [SDK v89 迭代记录](./stage2/iteration-v89.md)：恢复 Omni `semantic_vad` 主链路，旁路 ASR 非阻塞化，并新增模型工具关闭连续对话。
2. [SDK v88 迭代记录](./stage2/iteration-v88.md)：连续 VAD 空段被抑制时同步关闭端侧连续窗口，避免眼镜等待回复超时。
3. [SDK v87 迭代记录](./stage2/iteration-v87.md)：增加语音轮次意图裁决，阻断非视觉问题自动抓拍和短误触发自回复。
4. [SDK v86 迭代记录](./stage2/iteration-v86.md)：真实眼镜恢复受限连续对话，支持停止指令关闭窗口和播放中唤醒词打断。
5. [SDK v85 迭代记录](./stage2/iteration-v85.md)：真实眼镜半双工降级下关闭免唤醒连续 VAD，并在服务端抑制连续 VAD 空语音段。
6. [SDK v84 迭代记录](./stage2/iteration-v84.md)：外部 MCP Server client、Task 通用调度、终态回流策略和设备组通知播报链路修复。
7. [SDK v82 迭代记录](./stage2/iteration-v82.md)：服务端配置分层与 YAML 化。
8. [SDK v69 迭代记录](./stage2/iteration-v69.md)：工具调用前置播报，减少耗时 Tool 执行期间静默等待。
9. [全部迭代记录](./stage2/)

盲人 AI 眼镜业务文档放在 [../../openaiglass-for-blind/docs](../../openaiglass-for-blind/docs)。
