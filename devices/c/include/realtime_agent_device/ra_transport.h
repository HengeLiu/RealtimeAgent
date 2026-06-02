#ifndef REALTIME_AGENT_DEVICE_RA_TRANSPORT_H
#define REALTIME_AGENT_DEVICE_RA_TRANSPORT_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    RA_TRANSPORT_CONTROL = 0,
    RA_TRANSPORT_AUDIO_INPUT = 1,
    RA_TRANSPORT_AUDIO_OUTPUT = 2,
    RA_TRANSPORT_VISUAL_INPUT = 3,
} ra_transport_channel_t;

typedef struct {
    void *ctx;
    int (*connect)(void *ctx, ra_transport_channel_t channel, const char *url);
    int (*send_text)(void *ctx, ra_transport_channel_t channel, const char *text, size_t size);
    int (*send_binary)(void *ctx, ra_transport_channel_t channel, const uint8_t *data, size_t size);
    int (*recv_text)(void *ctx, ra_transport_channel_t channel, char *out, size_t capacity, size_t *size);
    int (*recv_binary)(void *ctx, ra_transport_channel_t channel, uint8_t *out, size_t capacity, size_t *size);
    int (*close)(void *ctx, ra_transport_channel_t channel);
} ra_transport_t;

const char *ra_transport_channel_name(ra_transport_channel_t channel);

#ifdef __cplusplus
}
#endif

#endif
