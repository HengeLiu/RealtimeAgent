#ifndef REALTIME_AGENT_DEVICE_H
#define REALTIME_AGENT_DEVICE_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define REALTIME_AGENT_PROTOCOL_VERSION "realtime-agent.v1"

typedef struct realtime_agent_device {
    char user_id[64];
    char device_id[64];
    char name[96];
    char role[32];
    int has_rgb_sensor;
    int has_vibrator;
} realtime_agent_device_t;

void realtime_agent_device_init(realtime_agent_device_t *device, const char *user_id, const char *device_id);
void realtime_agent_device_set_name(realtime_agent_device_t *device, const char *name);
void realtime_agent_device_set_role(realtime_agent_device_t *device, const char *role);
void realtime_agent_device_add_rgb_sensor(realtime_agent_device_t *device);
void realtime_agent_device_add_vibrator(realtime_agent_device_t *device);
int realtime_agent_device_registration_json(const realtime_agent_device_t *device, char *out, size_t out_size);

#ifdef __cplusplus
}
#endif

#endif
