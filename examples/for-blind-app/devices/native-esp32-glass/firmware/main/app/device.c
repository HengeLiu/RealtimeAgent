#include "app/device.h"

#include <stdio.h>
#include <string.h>

static void copy_text(char *target, size_t target_size, const char *value)
{
    if (target_size == 0) {
        return;
    }
    snprintf(target, target_size, "%s", value ? value : "");
}

void audio_chat_device_init(audio_chat_device_t *device, const char *user_id, const char *device_id)
{
    memset(device, 0, sizeof(*device));
    copy_text(device->user_id, sizeof(device->user_id), user_id);
    copy_text(device->device_id, sizeof(device->device_id), device_id);
    copy_text(device->name, sizeof(device->name), device_id);
}

void audio_chat_device_set_name(audio_chat_device_t *device, const char *name)
{
    copy_text(device->name, sizeof(device->name), name);
}

void audio_chat_device_set_role(audio_chat_device_t *device, const char *role)
{
    copy_text(device->role, sizeof(device->role), role);
}

void audio_chat_device_add_rgb_sensor(audio_chat_device_t *device)
{
    device->has_rgb_sensor = 1;
}

void audio_chat_device_add_imu_sensor(audio_chat_device_t *device)
{
    device->has_imu_sensor = 1;
}

void audio_chat_device_add_vibrator(audio_chat_device_t *device)
{
    device->has_vibrator = 1;
}

int audio_chat_device_registration_json(const audio_chat_device_t *device, char *out, size_t out_size)
{
    char sensors[256] = {0};
    char actuators[128] = {0};
    int sensor_len = 0;
    int actuator_len = 0;
    
    if (device->has_rgb_sensor) {
        sensor_len += snprintf(sensors + sensor_len, sizeof(sensors) - sensor_len,
            "%s{\"type\":\"rgb\",\"modes\":[\"single\"],\"default\":{\"format\":\"jpeg\",\"frequency_hz\":1}}",
            sensor_len > 0 ? "," : "");
    }
    
    if (device->has_imu_sensor) {
        sensor_len += snprintf(sensors + sensor_len, sizeof(sensors) - sensor_len,
            "%s{\"type\":\"imu\",\"modes\":[\"continuous\"],\"default\":{\"frequency_hz\":50}}",
            sensor_len > 0 ? "," : "");
    }
    
    if (device->has_vibrator) {
        actuator_len = snprintf(actuators, sizeof(actuators),
            ",\"actuators\":[{\"type\":\"vibrator\",\"commands\":[\"vibrate\"]}]");
    }
    
    int written = snprintf(
        out,
        out_size,
        "{\"device_id\":\"%s\","
        "\"name\":\"%s\","
        "\"device_name\":\"%s\","
        "\"client_type\":\"esp32-glass\","
        "\"sdk_version\":\"audio-chat-esp32-glass-0.1.0\","
        "\"auth\":{\"mode\":\"disabled\"},"
        "\"runtime\":{\"platform\":\"esp32\",\"language\":\"c\",\"version\":\"IDF v5.0\"},"
        "\"properties\":{"
        "\"device_role\":\"%s\","
        "\"endpoint.role.glass\":true,"
        "\"audio.input.sample_rate\":16000,"
        "\"audio.input.chunk_ms\":20,"
        "\"audio_chat.audio_input\":\"sensor.mic\","
        "\"audio_chat.audio_output\":\"actuator.speaker\""
        "},"
        "\"supports\":{\"sensors\":[%s]%s}}",
        device->device_id,
        device->name,
        device->name,
        device->role,
        sensors,
        actuators
    );
    if (written < 0 || (size_t)written >= out_size) {
        return -1;
    }
    return written;
}