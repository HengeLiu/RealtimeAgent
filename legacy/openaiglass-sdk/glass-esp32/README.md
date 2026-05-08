# glass-esp32

本目录放 ESP32 通用眼镜 SDK 运行时。它负责 WiFi、控制连接、音频、摄像头、端侧命令处理和 SDK 协议适配，不放 `find_object`、导航或其他盲人产品业务策略。

当前仍保留为 ESP-IDF 可构建工程，`sdk-v13` 起额外提供 `component-manifest.json` 作为源码包清单。清单声明当前可发布输入，包括 ESP-IDF 工程文件、main 组件文件、托管依赖和公开能力；`openaiglass.sdk.package-check` 会检查这些文件是否齐全。

```bash
openaiglass glass firmware --build-only --repo-root .
```

当前包形态是 `esp-idf-source-project`，用于内部源码集成和版本检查，不是发布到 ESP-IDF component registry 的独立组件。后续如果要成为正式组件，应先拆分 `main/glass_main.c` 中的运行时边界，再把宿主工程配置、硬件型号和业务差异留在业务侧。

盲人产品的眼镜宿主配置和硬件说明位于 [../../openaiglass-for-blind/host/glass](../../openaiglass-for-blind/host/glass)。

## ESP-SR 官方 AEC 试验

如果要验证 ESP32-S3 端侧 AEC，不要走 WebRTC 方案；本目录提供了一个独立入口 `main/test_official_aec.c`，直接使用 Espressif ESP-SR 的 `esp_aec.h`：

1. relay 把 Omni 下行音频以 16 kHz mono PCM16 发给眼镜。
2. 眼镜把同一份 PCM 写入扬声器，并在实际写 I2S 时写入 AEC `refdata` 缓冲。
3. 眼镜读取 PDM 麦克风，调用 `aec_process(mic, ref, out)`。
4. 眼镜把 `out` 作为 `mic_audio` 发回 relay，再由 relay 追加给 Omni。

启动 relay：

```bash
PYTHONPATH=openaiglass-sdk/server-python \
uv run --with websockets --with dashscope \
python openaiglass-sdk/server-python/devtools/omni_esp32_aec_relay.py \
  --host 0.0.0.0 \
  --ws-port 9886 \
  --record-mic-wav runs/esp32_aec_mic.wav \
  --record-playback-wav runs/esp32_aec_playback.wav
```

构建测试固件前，先通过 `menuconfig` 或本地配置填写 WiFi 和 `GLASS_AEC_TEST_RELAY_WS_URI`。如果使用 SDK 统一入口，可以直接指定 AEC defaults：

```bash
uv run openaiglass.glass.start \
  --sdkconfig-defaults openaiglass-sdk/glass-esp32/sdkconfig.defaults.official_aec_test \
  --sdkconfig openaiglass-sdk/glass-esp32/sdkconfig.aec_test.local \
  --build-dir openaiglass-sdk/glass-esp32/build-aec-test \
  --port '/dev/cu.usbmodem*'
```

关键观察点：

- 眼镜日志中应出现 `ESP-SR AEC started`、`relay websocket connected` 和周期性 `stats`。
- relay 日志中应持续收到 `mic_audio`，并能正常看到 Omni 的转写和回复。
- 如果仍然自问自答，优先观察 `ref_ring` 是否持续有数据、`ref_drop` 是否增长、播放音量是否过大、以及扬声器实际播放和 `refdata` 是否存在固定延迟。
