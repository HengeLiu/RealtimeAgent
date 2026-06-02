#ifndef REALTIME_AGENT_DEVICE_RA_HARDWARE_H
#define REALTIME_AGENT_DEVICE_RA_HARDWARE_H

#include <stddef.h>
#include <stdint.h>

#include "ra_error.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    const char *codec;
    int sample_rate;
    int channels;
    int chunk_ms;
} ra_audio_format_t;

typedef struct {
    void *ctx;
    ra_audio_format_t format;
    int (*start)(void *ctx);
    int (*read)(void *ctx, uint8_t *out, size_t capacity, size_t *written);
    int (*stop)(void *ctx);
} ra_mic_source_t;

typedef struct {
    void *ctx;
    const char *codec;
    int (*capture_jpeg)(void *ctx, const uint8_t **data, size_t *size);
    void (*release_jpeg)(void *ctx, const uint8_t *data);
} ra_camera_source_t;

typedef struct {
    void *ctx;
    int (*prepare)(void *ctx, const ra_audio_format_t *format);
    int (*write)(void *ctx, const uint8_t *pcm, size_t size, int duration_ms);
    int (*drain)(void *ctx);
    int (*cancel)(void *ctx);
} ra_speaker_sink_t;

ra_audio_format_t ra_audio_format_default(void);
size_t ra_audio_format_bytes_per_chunk(const ra_audio_format_t *format);

#ifdef __cplusplus
}
#endif

#endif
