#include "audio_chat_device/audio_chat_device.h"
#include "audio_chat_device/audio_chat_stream.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static void test_stream_codec(void)
{
    const char *header = "{\"stream_id\":\"s1\",\"payload_size\":3}";
    const uint8_t payload[] = {'a', 'b', 'c'};
    uint8_t raw[128];
    size_t written = 0;
    assert(audio_chat_stream_encode(header, payload, sizeof(payload), raw, sizeof(raw), &written) == 0);

    char decoded_header[128];
    const uint8_t *decoded_payload = NULL;
    size_t decoded_payload_size = 0;
    assert(audio_chat_stream_decode(raw, written, decoded_header, sizeof(decoded_header), &decoded_payload, &decoded_payload_size) == 0);
    assert(strcmp(decoded_header, header) == 0);
    assert(decoded_payload_size == 3);
    assert(memcmp(decoded_payload, payload, 3) == 0);

    assert(audio_chat_stream_decode(raw, written - 1, decoded_header, sizeof(decoded_header), &decoded_payload, &decoded_payload_size) != 0);
}

static void test_device_payload(void)
{
    audio_chat_device_t device;
    char json[1024];
    audio_chat_device_init(&device, "user-001", "dev-esp32-001");
    audio_chat_device_set_name(&device, "ESP32");
    audio_chat_device_set_role(&device, "glass");
    audio_chat_device_add_rgb_sensor(&device);
    audio_chat_device_add_vibrator(&device);
    assert(audio_chat_device_registration_json(&device, json, sizeof(json)) > 0);
    assert(strstr(json, "\"device_id\":\"dev-esp32-001\"") != NULL);
    assert(strstr(json, "\"type\":\"rgb\"") != NULL);
    assert(strstr(json, "\"type\":\"vibrator\"") != NULL);
}

int main(void)
{
    test_stream_codec();
    test_device_payload();
    return 0;
}
