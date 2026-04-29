# SDK 迭代记录：ESP32 首次唤醒轻提示音

对应对外 SDK 版本：`sdk-v57`。

## 背景

连续对话链路下，真实眼镜在首次 WakeNet 命中后会进入一段连续对话窗口。用户需要一个轻微、短暂的本地反馈，确认唤醒词已经被端侧识别成功，避免不知道是否可以开始说话。

## 本轮变更

1. `glass-esp32` 增加本地唤醒提示音，只在 `WakeNet detected` 分支播放。
2. 连续对话窗口内由本地 VAD 触发的新语音段不会重复播放提示音，避免每轮追问都打扰用户。
3. 提示音由端侧直接生成短 PCM 写入扬声器 I2S，不走服务端播放链路，不产生额外网络延迟。
4. 提示音 PCM 同步写入 AEC 播放参考缓冲，降低提示音被麦克风采集后误入用户语音的风险。
5. 新增 Kconfig 配置：
   - `CONFIG_GLASS_WAKE_PROMPT_TONE_ENABLE`
   - `CONFIG_GLASS_WAKE_PROMPT_TONE_DURATION_MS`
   - `CONFIG_GLASS_WAKE_PROMPT_TONE_FREQ_HZ`
   - `CONFIG_GLASS_WAKE_PROMPT_TONE_GAIN_PERMILLE`

## 验证

1. 单元测试静态检查提示音只挂在 WakeNet 分支，不挂在连续 VAD 分支。
2. 单元测试检查提示音会写入 AEC 参考缓冲。
3. 真机仍需要听感验证：提示音应短促、轻微，不应盖住用户开始说话的前几个字。

## 风险和后续

1. 如果提示音仍被 Omni 识别为输入噪声，应继续降低 `CONFIG_GLASS_WAKE_PROMPT_TONE_GAIN_PERMILLE` 或缩短时长。
2. 后续可按产品形态增加不同状态提示音，例如进入连续对话、退出连续对话、网络断开，但要避免提示音过多造成干扰。
