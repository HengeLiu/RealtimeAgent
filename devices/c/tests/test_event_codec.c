#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "realtime_agent_device/ra_event.h"

/*
 * 测试目标：验证 Event JSON 编码和基础解码能保留标准事件字段。
 * 测试方法：构造注册事件，编码为 JSON，再解码回 ra_event_t 并检查关键字段和 payload。
 * 预期结果：事件名、用户、生产者和 payload 均可正确读取。
 */
static void test_event_round_trip(void) {
    ra_event_t event;
    ra_event_init(&event, "control.device.register.requested", "user-001", "dev-001", "{\"device_id\":\"dev-001\"}");

    char json[1024];
    size_t written = 0;
    assert(ra_event_encode_json(&event, json, sizeof(json), &written) == RA_OK);
    assert(written > 0);
    assert(strstr(json, "\"routes\"") == NULL);
    assert(strstr(json, "\"capabilities\"") == NULL);

    ra_event_t decoded;
    assert(ra_event_decode_json(json, &decoded) == RA_OK);
    assert(strcmp(decoded.event_name, "control.device.register.requested") == 0);
    assert(strcmp(decoded.user_id, "user-001") == 0);
    assert(strcmp(decoded.producer_id, "dev-001") == 0);
    assert(ra_event_payload_contains(&decoded, "dev-001"));
}

/*
 * 测试目标：验证 payload 中的字符串和数字字段可被 handler 辅助函数读取。
 * 测试方法：解码 custom.command.requested 事件，并读取 command 与 duration_ms。
 * 预期结果：字符串和数字字段均能返回预期值。
 */
static void test_payload_helpers(void) {
    const char *json =
        "{\"event_name\":\"custom.command.requested\",\"user_id\":\"user-001\",\"producer_id\":\"server\","
        "\"payload\":{\"command\":\"haptic.vibrate\",\"duration_ms\":120}}";
    ra_event_t event;
    assert(ra_event_decode_json(json, &event) == RA_OK);
    char command[64];
    assert(ra_event_extract_payload_string(&event, "command", command, sizeof(command)) == RA_OK);
    assert(strcmp(command, "haptic.vibrate") == 0);
    assert(ra_event_extract_payload_int(&event, "duration_ms", 0) == 120);
}

int main(void) {
    test_event_round_trip();
    test_payload_helpers();
    puts("test_event_codec passed");
    return 0;
}
