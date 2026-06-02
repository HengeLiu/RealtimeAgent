#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "realtime_agent_device/ra_speaker_buffer.h"

/*
 * 测试目标：验证 speaker buffer 能按 seq 顺序输出，并在乱序时等待缺失 chunk。
 * 测试方法：先写入 seq=1，再写入 seq=0，随后连续 pop。
 * 预期结果：第一次无法 pop；补齐 seq=0 后按 0、1 顺序输出。
 */
static void test_out_of_order_waits_for_missing_seq(void) {
    ra_speaker_buffer_t buffer;
    ra_speaker_buffer_config_t config = ra_speaker_buffer_default_config();
    config.start_watermark_ms = 40;
    assert(ra_speaker_buffer_init(&buffer, &config) == RA_OK);

    uint8_t one[2] = {1, 1};
    uint8_t zero[2] = {0, 0};
    assert(ra_speaker_buffer_append(&buffer, 1, one, sizeof(one), 20) == RA_OK);
    assert(buffer.out_of_order_chunks == 1);

    ra_speaker_buffer_chunk_t out;
    assert(ra_speaker_buffer_pop_next(&buffer, &out) == RA_ERROR_NOT_FOUND);

    assert(ra_speaker_buffer_append(&buffer, 0, zero, sizeof(zero), 20) == RA_OK);
    assert(ra_speaker_buffer_can_start(&buffer));
    assert(ra_speaker_buffer_pop_next(&buffer, &out) == RA_OK);
    assert(out.seq == 0);
    ra_speaker_buffer_release_chunk(&out);
    assert(ra_speaker_buffer_pop_next(&buffer, &out) == RA_OK);
    assert(out.seq == 1);
    ra_speaker_buffer_release_chunk(&out);

    ra_speaker_buffer_deinit(&buffer);
}

/*
 * 测试目标：验证重复 chunk 不会重复进入播放队列。
 * 测试方法：连续写入两个 seq=0 的 chunk。
 * 预期结果：buffer 只保留一个 chunk，并记录 duplicate 计数。
 */
static void test_duplicate_is_ignored(void) {
    ra_speaker_buffer_t buffer;
    assert(ra_speaker_buffer_init(&buffer, NULL) == RA_OK);
    uint8_t payload[2] = {1, 2};
    assert(ra_speaker_buffer_append(&buffer, 0, payload, sizeof(payload), 20) == RA_OK);
    assert(ra_speaker_buffer_append(&buffer, 0, payload, sizeof(payload), 20) == RA_OK);
    assert(buffer.chunk_count == 1);
    assert(buffer.duplicate_chunks == 1);
    ra_speaker_buffer_deinit(&buffer);
}

/*
 * 测试目标：验证 cancel/reset 会清空 speaker buffer。
 * 测试方法：写入 chunk 后调用 reset。
 * 预期结果：buffer 时长、字节数和 chunk 数都归零，后续从指定 seq 重新开始。
 */
static void test_reset_clears_buffer(void) {
    ra_speaker_buffer_t buffer;
    assert(ra_speaker_buffer_init(&buffer, NULL) == RA_OK);
    uint8_t payload[2] = {1, 2};
    assert(ra_speaker_buffer_append(&buffer, 0, payload, sizeof(payload), 20) == RA_OK);
    ra_speaker_buffer_reset(&buffer, 5);
    assert(buffer.chunk_count == 0);
    assert(buffer.buffered_ms == 0);
    assert(buffer.buffered_bytes == 0);
    assert(buffer.next_seq == 5);
    ra_speaker_buffer_deinit(&buffer);
}

int main(void) {
    test_out_of_order_waits_for_missing_seq();
    test_duplicate_is_ignored();
    test_reset_clears_buffer();
    puts("test_speaker_buffer passed");
    return 0;
}
