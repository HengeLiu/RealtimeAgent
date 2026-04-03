# voice

## 目录说明

本目录存放语音触发和任务状态查询相关的测试数据。

## 文件说明

- `voice_cases.json`：语音测试 case 清单
- `audio_manifest.json`：真实音频文件命名约束清单
- `audio_samples/`：真实音频文件目录规范

## 数据构造方法

- 第一阶段允许直接使用文本构造语音事件
- `audio_ref` 当前为占位值，后续可替换为真实音频文件路径
- `vad_confidence` 用于模拟 VAD 事件检测后的结构化结果

## 扩展约束

- 当引入真实音频文件时，文件名应与 `case_id` 对齐
- 推荐后续增加 `wav` 文件，并保持 `16k pcm` 规范
