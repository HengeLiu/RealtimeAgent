#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"

/**
 * Encode audio/video stream data with header prefix.
 * Format: [4-byte header length][header JSON][payload binary]
 * Header follows audio-chat-stream.schema.json
 *
 * @param stream_id stream identifier ("audio" or "camera")
 * @param stream_type stream type per schema (e.g., "sensor.mic", "sensor.rgb")
 * @param payload raw binary data
 * @param payload_size size of payload in bytes
 * @param user_id user identifier
 * @param session_id session identifier
 * @param out output buffer
 * @param out_size size of output buffer
 * @param written output: bytes written to out
 * @return 0 on success, -1 on failure
 */
int audio_chat_stream_encode(const char *stream_id, const char *stream_type,
                              const uint8_t *payload, size_t payload_size,
                              const char *user_id, const char *session_id,
                              uint8_t *out, size_t out_size, size_t *written);

/**
 * Extended encode with final flag and optional metadata.
 * Used to send final StreamChunk before closing a stream.
 */
int audio_chat_stream_encode_ex(const char *stream_id, const char *stream_type,
                                 const uint8_t *payload, size_t payload_size,
                                 const char *user_id, const char *session_id,
                                 bool final, const char *metadata_json,
                                 uint8_t *out, size_t out_size, size_t *written);

/**
 * Encode IMU sensor data as text JSON.
 * IMU data is sent as plain text JSON via WebSocket, not binary encoded.
 */
int audio_chat_stream_encode_imu(const char *user_id, const char *session_id,
                                  const char *imu_json, size_t imu_json_len,
                                  uint8_t *out, size_t out_size, size_t *written);

/**
 * Decode audio/video stream data.
 *
 * @param raw raw encoded data
 * @param raw_size size of raw data
 * @param header_json output: null-terminated header JSON string
 * @param header_size size of header_json buffer
 * @param payload output: pointer to payload data within raw
 * @param payload_size output: size of payload
 * @return 0 on success, -1 on failure
 */
int audio_chat_stream_decode(const uint8_t *raw, size_t raw_size,
                              char *header_json, size_t header_size,
                              const uint8_t **payload, size_t *payload_size);

/**
 * Reset stream sequence counters.
 */
void audio_chat_stream_reset_seq(void);