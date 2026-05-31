#ifndef AUDIO_CHAT_DEVICE_H
#define AUDIO_CHAT_DEVICE_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define AUDIO_CHAT_PROTOCOL_VERSION "audio-chat.v1"

typedef struct audio_chat_device {
    char user_id[64];
    char device_id[64];
    char name[96];
    char role[32];
    int has_rgb_sensor;
    int has_imu_sensor;
    int has_vibrator;
} audio_chat_device_t;

void audio_chat_device_init(audio_chat_device_t *device, const char *user_id, const char *device_id);
void audio_chat_device_set_name(audio_chat_device_t *device, const char *name);
void audio_chat_device_set_role(audio_chat_device_t *device, const char *role);
void audio_chat_device_add_rgb_sensor(audio_chat_device_t *device);
void audio_chat_device_add_imu_sensor(audio_chat_device_t *device);
void audio_chat_device_add_vibrator(audio_chat_device_t *device);
int audio_chat_device_registration_json(const audio_chat_device_t *device, char *out, size_t out_size);

#ifdef __cplusplus
}
#endif

#endif