#include "realtime_agent_device/ra_speaker_buffer.h"

#include <stdlib.h>
#include <string.h>

#if defined(ESP_PLATFORM)
#include "esp_heap_caps.h"
#define RA_SPEAKER_BUFFER_HAS_ESP_HEAP 1
#endif

static void speaker_buffer_lock(ra_speaker_buffer_t *buffer) {
#if defined(RA_SPEAKER_BUFFER_HAS_ESP_HEAP)
    if (buffer != NULL && buffer->mutex != NULL) {
        xSemaphoreTake(buffer->mutex, portMAX_DELAY);
    }
#else
    (void)buffer;
#endif
}

static void speaker_buffer_unlock(ra_speaker_buffer_t *buffer) {
#if defined(RA_SPEAKER_BUFFER_HAS_ESP_HEAP)
    if (buffer != NULL && buffer->mutex != NULL) {
        xSemaphoreGive(buffer->mutex);
    }
#else
    (void)buffer;
#endif
}

static void *speaker_buffer_malloc(size_t size) {
#if defined(RA_SPEAKER_BUFFER_HAS_ESP_HEAP)
    void *ptr = heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (ptr == NULL) {
        ptr = heap_caps_malloc(size, MALLOC_CAP_8BIT);
    }
    return ptr;
#else
    return malloc(size);
#endif
}

static void *speaker_buffer_calloc(size_t count, size_t size) {
    if (count != 0 && size > ((size_t)-1) / count) {
        return NULL;
    }
    size_t total = count * size;
    void *ptr = speaker_buffer_malloc(total);
    if (ptr != NULL) {
        memset(ptr, 0, total);
    }
    return ptr;
}

ra_speaker_buffer_config_t ra_speaker_buffer_default_config(void) {
    ra_speaker_buffer_config_t config;
    config.start_watermark_ms = 120;
    config.low_watermark_ms = 300;
    config.high_watermark_ms = 800;
    config.max_buffer_ms = 1200;
    config.max_payload_bytes = 4096;
    config.max_chunks = 96;
    return config;
}

int ra_speaker_buffer_init(ra_speaker_buffer_t *buffer, const ra_speaker_buffer_config_t *config) {
    if (buffer == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    memset(buffer, 0, sizeof(*buffer));
#if defined(RA_SPEAKER_BUFFER_HAS_ESP_HEAP)
    buffer->mutex = xSemaphoreCreateMutex();
    if (buffer->mutex == NULL) {
        return RA_ERROR_NO_MEMORY;
    }
#endif
    buffer->config = config == NULL ? ra_speaker_buffer_default_config() : *config;
    if (buffer->config.max_chunks <= 0) {
        buffer->config.max_chunks = 96;
    }
    buffer->chunks = (ra_speaker_buffer_chunk_t *)speaker_buffer_calloc(
        (size_t)buffer->config.max_chunks,
        sizeof(ra_speaker_buffer_chunk_t)
    );
    if (buffer->chunks == NULL) {
#if defined(RA_SPEAKER_BUFFER_HAS_ESP_HEAP)
        vSemaphoreDelete(buffer->mutex);
        buffer->mutex = NULL;
#endif
        return RA_ERROR_NO_MEMORY;
    }
    buffer->next_seq = 0;
    return RA_OK;
}

void ra_speaker_buffer_release_chunk(ra_speaker_buffer_chunk_t *chunk) {
    if (chunk == NULL) {
        return;
    }
    free(chunk->payload);
    memset(chunk, 0, sizeof(*chunk));
}

void ra_speaker_buffer_reset(ra_speaker_buffer_t *buffer, int first_seq) {
    if (buffer == NULL || buffer->chunks == NULL) {
        return;
    }
    speaker_buffer_lock(buffer);
    for (int i = 0; i < buffer->config.max_chunks; ++i) {
        ra_speaker_buffer_release_chunk(&buffer->chunks[i]);
    }
    buffer->next_seq = first_seq;
    buffer->chunk_count = 0;
    buffer->buffered_ms = 0;
    buffer->buffered_bytes = 0;
    buffer->duplicate_chunks = 0;
    buffer->out_of_order_chunks = 0;
    buffer->paused = false;
    speaker_buffer_unlock(buffer);
}

void ra_speaker_buffer_deinit(ra_speaker_buffer_t *buffer) {
    if (buffer == NULL) {
        return;
    }
    ra_speaker_buffer_reset(buffer, 0);
    free(buffer->chunks);
    buffer->chunks = NULL;
#if defined(RA_SPEAKER_BUFFER_HAS_ESP_HEAP)
    if (buffer->mutex != NULL) {
        vSemaphoreDelete(buffer->mutex);
        buffer->mutex = NULL;
    }
#endif
}

static bool speaker_buffer_has_seq_unlocked(const ra_speaker_buffer_t *buffer, int seq) {
    if (buffer == NULL || buffer->chunks == NULL) {
        return false;
    }
    for (int i = 0; i < buffer->config.max_chunks; ++i) {
        if (buffer->chunks[i].payload != NULL && buffer->chunks[i].seq == seq) {
            return true;
        }
    }
    return false;
}

bool ra_speaker_buffer_has_seq(const ra_speaker_buffer_t *buffer, int seq) {
    ra_speaker_buffer_t *mutable_buffer = (ra_speaker_buffer_t *)buffer;
    speaker_buffer_lock(mutable_buffer);
    bool found = speaker_buffer_has_seq_unlocked(buffer, seq);
    speaker_buffer_unlock(mutable_buffer);
    return found;
}

int ra_speaker_buffer_append(ra_speaker_buffer_t *buffer, int seq, const uint8_t *payload, size_t size, int duration_ms) {
    if (buffer == NULL || buffer->chunks == NULL || payload == NULL || size == 0 || duration_ms <= 0) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    uint8_t *copy = (uint8_t *)speaker_buffer_malloc(size);
    if (copy == NULL) {
        return RA_ERROR_NO_MEMORY;
    }
    memcpy(copy, payload, size);
    speaker_buffer_lock(buffer);
    if (seq < buffer->next_seq || speaker_buffer_has_seq_unlocked(buffer, seq)) {
        buffer->duplicate_chunks++;
        speaker_buffer_unlock(buffer);
        free(copy);
        return RA_OK;
    }
    if (seq > buffer->next_seq) {
        buffer->out_of_order_chunks++;
    }
    if (buffer->buffered_ms + duration_ms > buffer->config.max_buffer_ms) {
        speaker_buffer_unlock(buffer);
        free(copy);
        return RA_ERROR_BUFFER_TOO_SMALL;
    }
    if (buffer->config.max_payload_bytes > 0 && size > buffer->config.max_payload_bytes) {
        speaker_buffer_unlock(buffer);
        free(copy);
        return RA_ERROR_BUFFER_TOO_SMALL;
    }
    int slot = -1;
    for (int i = 0; i < buffer->config.max_chunks; ++i) {
        if (buffer->chunks[i].payload == NULL) {
            slot = i;
            break;
        }
    }
    if (slot < 0) {
        speaker_buffer_unlock(buffer);
        free(copy);
        return RA_ERROR_BUFFER_TOO_SMALL;
    }
    buffer->chunks[slot].seq = seq;
    buffer->chunks[slot].duration_ms = duration_ms;
    buffer->chunks[slot].size = size;
    buffer->chunks[slot].payload = copy;
    buffer->chunk_count++;
    buffer->buffered_ms += duration_ms;
    buffer->buffered_bytes += size;
    speaker_buffer_unlock(buffer);
    return RA_OK;
}

bool ra_speaker_buffer_can_start(const ra_speaker_buffer_t *buffer) {
    if (buffer == NULL) {
        return false;
    }
    ra_speaker_buffer_t *mutable_buffer = (ra_speaker_buffer_t *)buffer;
    speaker_buffer_lock(mutable_buffer);
    bool can_start = buffer->buffered_ms >= buffer->config.start_watermark_ms;
    speaker_buffer_unlock(mutable_buffer);
    return can_start;
}

bool ra_speaker_buffer_should_pause(ra_speaker_buffer_t *buffer) {
    if (buffer == NULL || buffer->paused) {
        return false;
    }
    speaker_buffer_lock(buffer);
    if (buffer->paused) {
        speaker_buffer_unlock(buffer);
        return false;
    }
    if (buffer->buffered_ms >= buffer->config.high_watermark_ms) {
        buffer->paused = true;
        speaker_buffer_unlock(buffer);
        return true;
    }
    speaker_buffer_unlock(buffer);
    return false;
}

bool ra_speaker_buffer_should_resume(ra_speaker_buffer_t *buffer) {
    if (buffer == NULL || !buffer->paused) {
        return false;
    }
    speaker_buffer_lock(buffer);
    if (!buffer->paused) {
        speaker_buffer_unlock(buffer);
        return false;
    }
    if (buffer->buffered_ms <= buffer->config.low_watermark_ms) {
        buffer->paused = false;
        speaker_buffer_unlock(buffer);
        return true;
    }
    speaker_buffer_unlock(buffer);
    return false;
}

int ra_speaker_buffer_pop_next(ra_speaker_buffer_t *buffer, ra_speaker_buffer_chunk_t *out) {
    if (buffer == NULL || buffer->chunks == NULL || out == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    speaker_buffer_lock(buffer);
    for (int i = 0; i < buffer->config.max_chunks; ++i) {
        if (buffer->chunks[i].payload != NULL && buffer->chunks[i].seq == buffer->next_seq) {
            *out = buffer->chunks[i];
            memset(&buffer->chunks[i], 0, sizeof(buffer->chunks[i]));
            buffer->next_seq++;
            buffer->chunk_count--;
            buffer->buffered_ms -= out->duration_ms;
            buffer->buffered_bytes -= out->size;
            speaker_buffer_unlock(buffer);
            return RA_OK;
        }
    }
    speaker_buffer_unlock(buffer);
    return RA_ERROR_NOT_FOUND;
}
