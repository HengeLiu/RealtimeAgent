#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "realtime_agent_device/ra_stream_chunk.h"

/*
 * 测试目标：验证 StreamChunk 二进制格式与 Swift SDK 的 header_length + JSON header + payload 结构一致。
 * 测试方法：编码一段 20ms PCM payload，再解码并检查 header 字段和 payload 字节。
 * 预期结果：解码后的 stream_type、seq、音频格式和 payload 与编码输入一致。
 */
static void test_stream_chunk_round_trip(void) {
    uint8_t pcm[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    ra_stream_chunk_t chunk;
    ra_stream_chunk_init(&chunk);
    strcpy(chunk.user_id, "user-001");
    strcpy(chunk.session_id, "sess-001");
    strcpy(chunk.stream_id, "mic-001");
    strcpy(chunk.stream_type, "sensor.mic");
    chunk.seq = 7;
    chunk.payload = pcm;
    chunk.payload_size = sizeof(pcm);
    chunk.duration_ms = 20;

    uint8_t encoded[2048];
    size_t written = 0;
    assert(ra_stream_chunk_encode(&chunk, encoded, sizeof(encoded), &written) == RA_OK);
    assert(written > sizeof(pcm));

    ra_stream_chunk_t decoded;
    const uint8_t *payload = NULL;
    assert(ra_stream_chunk_decode(encoded, written, &decoded, &payload) == RA_OK);
    assert(strcmp(decoded.user_id, "user-001") == 0);
    assert(strcmp(decoded.session_id, "sess-001") == 0);
    assert(strcmp(decoded.stream_id, "mic-001") == 0);
    assert(strcmp(decoded.stream_type, "sensor.mic") == 0);
    assert(decoded.seq == 7);
    assert(decoded.payload_size == sizeof(pcm));
    assert(memcmp(payload, pcm, sizeof(pcm)) == 0);
}

/*
 * 测试目标：验证 payload_size 不一致时能暴露协议错误。
 * 测试方法：编码后篡改最后一字节长度，制造 payload_size 与实际 payload 不一致。
 * 预期结果：解码失败，避免端侧继续处理破损 chunk。
 */
static void test_payload_size_mismatch(void) {
    uint8_t pcm[4] = {1, 2, 3, 4};
    ra_stream_chunk_t chunk;
    ra_stream_chunk_init(&chunk);
    strcpy(chunk.user_id, "user-001");
    strcpy(chunk.session_id, "sess-001");
    strcpy(chunk.stream_id, "mic-001");
    strcpy(chunk.stream_type, "sensor.mic");
    chunk.payload = pcm;
    chunk.payload_size = sizeof(pcm);

    uint8_t encoded[2048];
    size_t written = 0;
    assert(ra_stream_chunk_encode(&chunk, encoded, sizeof(encoded), &written) == RA_OK);
    ra_stream_chunk_t decoded;
    const uint8_t *payload = NULL;
    assert(ra_stream_chunk_decode(encoded, written - 1, &decoded, &payload) == RA_ERROR_PARSE_FAILED);
}

int main(void) {
    test_stream_chunk_round_trip();
    test_payload_size_mismatch();
    puts("test_stream_chunk_codec passed");
    return 0;
}
