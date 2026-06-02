#ifndef REALTIME_AGENT_DEVICE_RA_SPEAKER_BUFFER_H
#define REALTIME_AGENT_DEVICE_RA_SPEAKER_BUFFER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "ra_error.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef void *(*ra_speaker_buffer_alloc_fn)(void *ctx, size_t size);
typedef void (*ra_speaker_buffer_free_fn)(void *ctx, void *ptr);
typedef void (*ra_speaker_buffer_lock_fn)(void *ctx);
typedef void (*ra_speaker_buffer_unlock_fn)(void *ctx);

typedef struct {
    int start_watermark_ms;
    int low_watermark_ms;
    int high_watermark_ms;
    int max_buffer_ms;
    size_t max_payload_bytes;
    int max_chunks;
    void *allocator_ctx;
    ra_speaker_buffer_alloc_fn alloc;
    ra_speaker_buffer_free_fn free;
    void *lock_ctx;
    ra_speaker_buffer_lock_fn lock;
    ra_speaker_buffer_unlock_fn unlock;
} ra_speaker_buffer_config_t;

typedef struct {
    int seq;
    int duration_ms;
    size_t size;
    uint8_t *payload;
    void *allocator_ctx;
    ra_speaker_buffer_free_fn free;
} ra_speaker_buffer_chunk_t;

typedef struct {
    ra_speaker_buffer_config_t config;
    ra_speaker_buffer_chunk_t *chunks;
    int next_seq;
    int chunk_count;
    int buffered_ms;
    size_t buffered_bytes;
    int duplicate_chunks;
    int out_of_order_chunks;
    bool paused;
} ra_speaker_buffer_t;

typedef struct {
    int chunk_count;
    int buffered_ms;
    size_t buffered_bytes;
    int duplicate_chunks;
    int out_of_order_chunks;
    bool paused;
} ra_speaker_buffer_snapshot_t;

ra_speaker_buffer_config_t ra_speaker_buffer_default_config(void);
int ra_speaker_buffer_init(ra_speaker_buffer_t *buffer, const ra_speaker_buffer_config_t *config);
void ra_speaker_buffer_deinit(ra_speaker_buffer_t *buffer);
void ra_speaker_buffer_reset(ra_speaker_buffer_t *buffer, int first_seq);
int ra_speaker_buffer_append(ra_speaker_buffer_t *buffer, int seq, const uint8_t *payload, size_t size, int duration_ms);
bool ra_speaker_buffer_can_start(const ra_speaker_buffer_t *buffer);
bool ra_speaker_buffer_should_pause(ra_speaker_buffer_t *buffer);
bool ra_speaker_buffer_should_resume(ra_speaker_buffer_t *buffer);
int ra_speaker_buffer_pop_next(ra_speaker_buffer_t *buffer, ra_speaker_buffer_chunk_t *out);
void ra_speaker_buffer_release_chunk(ra_speaker_buffer_chunk_t *chunk);
bool ra_speaker_buffer_has_seq(const ra_speaker_buffer_t *buffer, int seq);
int ra_speaker_buffer_snapshot(const ra_speaker_buffer_t *buffer, ra_speaker_buffer_snapshot_t *out);

#ifdef __cplusplus
}
#endif

#endif
