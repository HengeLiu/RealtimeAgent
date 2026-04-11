# ESP32-S3 音频链路 Spike（AFE + WakeNet + 本地端点检测 + 播放时闭麦）

这个 spike 是一个独立 ESP-IDF 工程，用于验证以下链路在 `XIAO ESP32S3 Sense` 上可跑通：

- ESP-SR AFE 持续 `feed/fetch`
- WakeNet 唤醒
- 基于 `vad_state` 的本地端点检测（尾静音收口）
- 回复播放阶段闭麦（暂停 WakeNet/VAD + 暂停麦克风处理）

## 目录

- 工程路径：`glass/spike/esp32s3_audio_sr_spike`
- 主代码：`main/sr_spike_main.c`

## 引脚

来自 [引脚表.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/glass/doc/引脚表.md)：

- PDM Mic `CLK=GPIO42`，`DATA=GPIO41`
- I2S Speaker `BCLK=GPIO7`，`LRCK=GPIO8`，`DIN=GPIO9`

## 关键配置

工程默认依赖：

- `espressif/esp-sr = 2.1.2`（见 `main/idf_component.yml`）

工程内已提供：

- `partitions.csv`，包含 `model` 分区（`data, spiffs`）
- `sdkconfig.defaults`，包含 `CONFIG_MODEL_IN_FLASH=y` 和 `CONFIG_SR_WN_WN9_HILEXIN=y`

如果本地 `sdkconfig` 里未生效，请执行 `idf.py menuconfig` 再确认：

1. `ESP Speech Recognition -> Load Multiple Models`
2. `ESP Speech Recognition -> ESP Speech Recognition Wake Word Models` 选择 `wn9_hilexin`

Wake word 定制参考：

- https://docs.espressif.com/projects/esp-sr/zh_CN/latest/esp32s3/wake_word_engine/ESP_Wake_Words_Customization.html

## 运行步骤

1. 安装并导出 ESP-IDF 环境。
2. 进入工程：
   - `cd glass/spike/esp32s3_audio_sr_spike`
3. 设定目标：
   - `idf.py set-target esp32s3`
4. 首次可先检查配置：
   - `idf.py menuconfig`
5. 编译烧录并看日志：
   - `idf.py -p <PORT> flash monitor`

## 通过判据（串口日志）

启动后看见：

- `MIC ready ...`
- `SPK ready ...`
- `WakeNet model selected: ...`

说出唤醒词（嗨，乐鑫）后看见：

- `WakeNet detected: start local segment capture`
- `VAD speech observed in current segment`
- `Endpoint detected ...`

进入播放阶段看见：

- `Playback gate ON: mic pipeline muted, WakeNet/VAD paused`
- `demo playback finished`
- `Playback gate OFF: mic pipeline resumed, WakeNet/VAD resumed`

这 3 段日志齐全即可证明这个 spike 的四个目标路径打通。

## 常见问题：PSRAM 报错

如果看到 `PSRAM ID read error` 或 `Memory exhausted`：

1. 进入 `Component config -> ESP PSRAM`。
2. 把 `Mode` 设为 `Octal Mode PSRAM`。
3. 把 `Type` 设为 `ESP-PSRAM64`（或保留 Auto-detect 后观察日志）。
4. 把 `Speed` 先设为 `80MHz`。
