#include "audio_chat_device/audio_chat_device.h"

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

void audio_chat_device_add_vibrator(audio_chat_device_t *device)
{
    device->has_vibrator = 1;
}

int audio_chat_device_registration_json(const audio_chat_device_t *device, char *out, size_t out_size)
{
    const char *sensor = device->has_rgb_sensor
        ? "\"sensors\":[{\"type\":\"rgb\",\"modes\":[\"single\"],\"default\":{\"format\":\"jpeg\",\"frequency_hz\":1}}]"
        : "";
    const char *comma = (device->has_rgb_sensor && device->has_vibrator) ? "," : "";
    const char *actuator = device->has_vibrator
        ? "\"actuators\":[{\"type\":\"vibrator\",\"commands\":[\"vibrate\"]}]"
        : "";
    int written = snprintf(
        out,
        out_size,
        "{\"device_id\":\"%s\",\"name\":\"%s\",\"device_name\":\"%s\",\"client_type\":\"esp32\",\"sdk_version\":\"0.1.0\",\"runtime\":{\"platform\":\"esp32\",\"language\":\"c\"},\"properties\":{\"device_role\":\"%s\"},\"supports\":{%s%s%s}}",
        device->device_id,
        device->name,
        device->name,
        device->role,
        sensor,
        comma,
        actuator
    );
    if (written < 0 || (size_t)written >= out_size) {
        return -1;
    }
    return written;
}
