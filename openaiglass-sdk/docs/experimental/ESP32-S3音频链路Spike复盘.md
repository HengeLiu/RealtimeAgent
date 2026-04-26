# ESP32-S3 音频链路 Spike 复盘

说明：

1. 本文档仅保留为历史复盘记录。
2. spike 验证结果已经并入主工程 `glass/src`，对应独立 spike 代码与脚本已从当前主流程中移除。

## 1. 试验背景与目标

- 日期：2026-04-11
- 目标：单独做一个 `ESP32-S3` 音频链路 spike，验证以下 4 条能力可跑通：
  - `ESP-SR AFE` 持续 `feed/fetch`
  - `WakeNet` 唤醒词检测（目标唤醒词：`嗨，乐鑫`）
  - 本地端点检测（基于 `vad_state` 的尾静音收口）
  - 播放阶段闭麦（暂停 WakeNet/VAD 与麦克风处理，播放后恢复）

## 2. 实施方案

### 2.1 工程落地

- 当时新建了独立 ESP-IDF 工程，后续已合并进主工程并移除独立目录。
- 关键文件：
  - `main/sr_spike_main.c`
  - `main/idf_component.yml`（`espressif/esp-sr = 2.1.2`）
  - `partitions.csv`（含 `model` 分区）
  - `sdkconfig.defaults`
  - `README.md`

### 2.2 硬件引脚

按 `glass/doc/引脚表.md` 使用：

- PDM Mic：`CLK=GPIO42`，`DATA=GPIO41`
- I2S Speaker：`BCLK=GPIO7`，`LRCK=GPIO8`，`DIN=GPIO9`

### 2.3 运行链路设计

1. 初始化麦克风与喇叭 I2S。
2. 初始化 ESP-SR 模型与 AFE（开启 WakeNet + VAD）。
3. 音频主循环：
   - 读取 mic 数据
   - `afe->feed(...)`
   - `afe->fetch(...)`
4. 当 `wakeup_state == WAKENET_DETECTED` 时进入当前轮段采集。
5. 端点检测规则：
   - 已出现人声后，累计尾部静音时长；
   - 达到阈值判定 endpoint（并有最长时长兜底）。
6. endpoint 后进入“播放阶段”：
   - `disable_wakenet` + `disable_vad` + 本地 gate；
   - 播放测试音；
   - 播放结束恢复 `enable_vad` + `enable_wakenet`。

## 3. 成功关键点

1. 独立工程把问题面收敛到设备侧链路，减少与上层业务耦合。
2. 明确分区与模型打包路径，`srmodels.bin` 可随固件一并烧录。
3. 明确唤醒词模型为 `wn9_hilexin`，日志可直接验证模型是否生效。
4. 通过显式“播放 gate”实现播放期间闭麦，状态切换清晰可观测。
5. 串口日志设计了关键节点打印，可快速判断链路卡在哪一段。
6. 实测已达到“语音唤醒成功”。

## 4. 关键阻塞点与处理

### 4.1 构建环境阻塞

- 现象：
  - `idf.py` 不存在，或 `esp_idf_monitor` 导入失败。
- 根因：
  - ESP-IDF 未安装或 Python 版本不一致（3.9/3.13 混用）。
- 处理：
  - 安装 ESP-IDF v5.3.2；
  - 固定 `ESP_PYTHON=/opt/miniconda3/bin/python3`；
  - 用 `export.sh` 进入 IDF shell 环境。

### 4.2 子模块下载阻塞

- 现象：
  - `micro-ecc` 子模块拉取失败。
- 根因：
  - 全局 git 代理指向不可用地址（`127.0.0.1:7897`）。
- 处理：
  - 取消/绕过 git proxy 后重拉子模块。

### 4.3 启动模式阻塞

- 现象：
  - 设备进入 `DOWNLOAD` 模式，停在 `waiting for download`。
- 根因：
  - 复位时序/BOOT 状态影响。
- 处理：
  - 分步执行 `flash` 与 `monitor`，并调整复位操作。

### 4.4 PSRAM 阻塞（本次最关键）

- 现象：
  - `PSRAM ID read error`；
  - 随后 `SR_RINGBUF: Memory exhausted`，AFE/WakeNet 初始化崩溃。
- 根因：
  - 外部 PSRAM 未被正确初始化，可用内存不足以承载当前模型链路。
- 处理：
  - 配置层：
    - `SPIRAM_MODE_OCT`
    - `SPIRAM_TYPE_ESPPSRAM64`
    - `SPIRAM_SPEED_80M`
    - `SPIRAM_IGNORE_NOTFOUND=y`（用于定位阶段避免直接死循环）
  - 代码层：
    - 增加 PSRAM 检测与日志，未检测到时直接退出，避免 panic 刷屏。

## 5. 本次结论

1. `ESP-SR AFE + WakeNet + 本地端点检测 + 播放闭麦` 的 spike 方案可落地。
2. 唤醒词 `嗨，乐鑫` 路径已验证通过（`wn9_hilexin`）。
3. 后续联调重点不再是算法链路本身，而是：
   - 板级 PSRAM 稳定性配置；
   - 与上层会话/协议状态机的对接。

## 6. 后续建议

1. 把当前 spike 的关键日志点保留为长期调试模板。
2. 在设备启动阶段增加“PSRAM/模型可用性自检”并上报错误码。
3. 逐步把测试音播放替换成真实下行播放数据面，保持闭麦策略不变。
4. 在进入主业务联调前，先固化一份已验证板卡配置清单（PSRAM 模式、频率、分区）。
