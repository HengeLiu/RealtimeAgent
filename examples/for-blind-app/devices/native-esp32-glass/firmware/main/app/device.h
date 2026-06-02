#ifndef AUDIO_CHAT_DEVICE_H
#define AUDIO_CHAT_DEVICE_H

#include <stddef.h>

typedef struct {
    char user_id[64];
    char device_id[64];
    char name[96];
    char role[32];
    int has_rgb_sensor;
    int has_imu_sensor;
    int has_vibrator;
    char auth_mode[16];
    char auth_token[256];
} audio_chat_device_t;

void audio_chat_device_init(audio_chat_device_t *dev, const char *user_id, const char *device_id);
void audio_chat_device_set_name(audio_chat_device_t *dev, const char *name);
void audio_chat_device_set_role(audio_chat_device_t *dev, const char *role);
void audio_chat_device_add_rgb_sensor(audio_chat_device_t *dev);
void audio_chat_device_add_imu_sensor(audio_chat_device_t *dev);
void audio_chat_device_add_vibrator(audio_chat_device_t *dev);
void audio_chat_device_set_auth(audio_chat_device_t *dev, const char *mode, const char *token);
int audio_chat_device_to_json(const audio_chat_device_t *dev, char *out, size_t size);
int audio_chat_device_registration_json(const audio_chat_device_t *dev, char *out, size_t size);

#endif
