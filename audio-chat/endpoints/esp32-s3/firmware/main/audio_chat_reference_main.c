#include <stdio.h>

#include "esp_log.h"

static const char *TAG = "audio_chat_reference";

/*
 * ESP32-S3 参考固件骨架。
 *
 * 本文件只提供最小可构建的 ESP-IDF app，用于 package-check 和真机工程入口检查。
 * 协议契约已冻结在 audio_chat.endpoints.esp32_aec；真实固件应按以下清单补齐：
 *
 * 1. 从 local.env 读取 WiFi、control WebSocket、stream WebSocket、user_id、
 *    device_id 和鉴权 token。
 * 2. 连接 WiFi，打开 /ws/control，发布 control.device.register.requested。
 * 3. 上报心跳，并在网络断开后重连控制 WebSocket。
 * 4. 本地唤醒词命中后发布 control.user.wake.detected，收到
 *    control.audio_session.open.requested 后才上传 sensor.mic。
 * 5. 消费 actuator.speaker chunk，把同一帧 PCM 同步写入播放环形缓冲和
 *    AEC reference 环形缓冲，再上报 started/finished/closed 或 failed。
 * 6. 响应 sensor.rgb 的 stream.control.configure.requested，通过 /ws/stream
 *    上传 JPEG 字节，不把媒体大字节放进控制事件 payload。
 */
void app_main(void)
{
    ESP_LOGI(TAG, "audio-chat ESP32-S3 reference firmware skeleton started");
}
