#include <stdio.h>

#include "audio_chat_device/audio_chat_device.h"
#include "esp_log.h"

static const char *TAG = "audio_chat_reference";

/*
 * ESP32-S3 参考固件骨架。
 *
 * 本文件只提供最小可构建的 ESP-IDF app，用于 package-check 和真机工程入口检查。
 * 协议契约已冻结在 audio_chat_esp32_s3.esp32_aec；真实固件应按以下清单补齐：
 *
 * 1. 从 local.env 读取 WiFi、control WebSocket、stream WebSocket、user_id、
 *    device_id 和鉴权 token。
 * 2. 连接 WiFi，打开 /ws/control，发布 control.device.register.requested。
 * 3. 上报心跳，并在网络断开后重连控制 WebSocket。
 * 4. 本地唤醒词命中后发布 control.user.wake.detected，收到
 *    control.audio_session.open.requested 后才上传 sensor.mic。
 * 5. 消费 actuator.speaker chunk，把同一帧 PCM 同步写入播放环形缓冲和
 *    AEC reference 环形缓冲，再上报 started/finished/closed 或 failed。
 * 6. 响应 sensor.rgb 的 stream.control.open.requested，通过 /ws/stream
 *    上传 JPEG 字节，不把媒体大字节放进控制事件 payload。
 * 7. 如果配置了 AUDIO_CHAT_PHONE_CAMERA_SINK_WS_URI，则按 audio-chat 直连帧
 *    格式把同一帧 JPEG 通过 phone 直连 WebSocket 推送给 iOS phone 的
 *    /ws/camera 接收服务。
 */
void app_main(void)
{
    audio_chat_device_t device;
    char registration_json[512];
    audio_chat_device_init(&device, "user-browser-glass-001", "dev-esp32-glass-001");
    audio_chat_device_set_name(&device, "ESP32-S3 Glass");
    audio_chat_device_set_role(&device, "glass");
    audio_chat_device_add_rgb_sensor(&device);
    audio_chat_device_add_vibrator(&device);
    if (audio_chat_device_registration_json(&device, registration_json, sizeof(registration_json)) > 0) {
        ESP_LOGD(TAG, "registration payload template: %s", registration_json);
    }
    ESP_LOGI(TAG, "audio-chat ESP32-S3 reference firmware skeleton started");
}
