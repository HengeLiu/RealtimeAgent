#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "realtime_agent_device/ra_client.h"

typedef struct {
    int connect_count;
    int send_count;
    int binary_count;
    char last_url[256];
    char last_text[4096];
    char last_binary_header[1024];
} mock_transport_t;

static int mock_connect(void *ctx, ra_transport_channel_t channel, const char *url) {
    mock_transport_t *mock = (mock_transport_t *)ctx;
    mock->connect_count++;
    snprintf(mock->last_url, sizeof(mock->last_url), "%s:%s", ra_transport_channel_name(channel), url);
    return RA_OK;
}

static int mock_send_text(void *ctx, ra_transport_channel_t channel, const char *text, size_t size) {
    (void)channel;
    mock_transport_t *mock = (mock_transport_t *)ctx;
    mock->send_count++;
    assert(size < sizeof(mock->last_text));
    memcpy(mock->last_text, text, size);
    mock->last_text[size] = '\0';
    return RA_OK;
}

static int mock_send_binary(void *ctx, ra_transport_channel_t channel, const uint8_t *data, size_t size) {
    (void)channel;
    mock_transport_t *mock = (mock_transport_t *)ctx;
    mock->binary_count++;
    size_t written = 0;
    assert(ra_stream_chunk_decode_header_json(data, size, mock->last_binary_header, sizeof(mock->last_binary_header), &written) == RA_OK);
    mock->last_binary_header[written] = '\0';
    return RA_OK;
}

static int mock_close(void *ctx, ra_transport_channel_t channel) {
    (void)ctx;
    (void)channel;
    return RA_OK;
}

typedef struct {
    int started;
    int stopped;
} mock_mic_t;

static int mock_mic_start(void *ctx) {
    ((mock_mic_t *)ctx)->started++;
    return RA_OK;
}

static int mock_mic_stop(void *ctx) {
    ((mock_mic_t *)ctx)->stopped++;
    return RA_OK;
}

typedef struct {
    int prepared;
    int written;
    int drained;
    int cancelled;
} mock_speaker_t;

static int mock_speaker_prepare(void *ctx, const ra_audio_format_t *format) {
    (void)format;
    ((mock_speaker_t *)ctx)->prepared++;
    return RA_OK;
}

static int mock_speaker_write(void *ctx, const uint8_t *pcm, size_t size, int duration_ms) {
    (void)pcm;
    (void)size;
    (void)duration_ms;
    ((mock_speaker_t *)ctx)->written++;
    return RA_OK;
}

static int mock_speaker_drain(void *ctx) {
    ((mock_speaker_t *)ctx)->drained++;
    return RA_OK;
}

static void encode_output_chunk(int seq, uint8_t *out, size_t capacity, size_t *written) {
    static const uint8_t payload[4] = {0, 1, 2, 3};
    ra_stream_chunk_t chunk;
    ra_stream_chunk_init(&chunk);
    snprintf(chunk.user_id, sizeof(chunk.user_id), "%s", "user-001");
    snprintf(chunk.session_id, sizeof(chunk.session_id), "%s", "dev-001");
    snprintf(chunk.stream_id, sizeof(chunk.stream_id), "%s", "stream-speaker-001");
    snprintf(chunk.stream_type, sizeof(chunk.stream_type), "%s", "actuator.speaker");
    snprintf(chunk.codec, sizeof(chunk.codec), "%s", "pcm16le");
    chunk.sample_rate = 24000;
    chunk.channels = 1;
    chunk.duration_ms = 20;
    chunk.seq = seq;
    chunk.payload = payload;
    chunk.payload_size = sizeof(payload);
    assert(ra_stream_chunk_encode(&chunk, out, capacity, written) == RA_OK);
}

static int mock_speaker_cancel(void *ctx) {
    ((mock_speaker_t *)ctx)->cancelled++;
    return RA_OK;
}

typedef struct {
    int captured;
    int released;
} mock_camera_t;

static int mock_camera_capture_jpeg(void *ctx, const uint8_t **data, size_t *size) {
    static const uint8_t jpeg[] = {0xff, 0xd8, 0x01, 0x02, 0xff, 0xd9};
    ((mock_camera_t *)ctx)->captured++;
    *data = jpeg;
    *size = sizeof(jpeg);
    return RA_OK;
}

static void mock_camera_release_jpeg(void *ctx, const uint8_t *data) {
    (void)data;
    ((mock_camera_t *)ctx)->released++;
}

/*
 * 测试目标：验证 client.start 会连接 control 并发送自动生成的注册事件。
 * 测试方法：使用 mock transport 创建 client，启动后检查 URL、注册 payload 和旧字段。
 * 预期结果：连接 `/ws/control`，注册 payload 包含音频和 RGB 声明，不包含旧 routes/capabilities。
 */
static void test_start_sends_registration(void) {
    mock_transport_t mock = {0};
    ra_transport_t transport = {
        .ctx = &mock,
        .connect = mock_connect,
        .send_text = mock_send_text,
        .close = mock_close,
    };
    mock_mic_t mic_ctx = {0};
    ra_mic_source_t mic = {
        .ctx = &mic_ctx,
        .format = ra_audio_format_default(),
        .start = mock_mic_start,
        .stop = mock_mic_stop,
    };
    ra_camera_source_t camera = {.codec = "jpeg"};
    mock_speaker_t speaker_ctx = {0};
    ra_speaker_sink_t speaker = {
        .ctx = &speaker_ctx,
        .prepare = mock_speaker_prepare,
        .write = mock_speaker_write,
        .drain = mock_speaker_drain,
        .cancel = mock_speaker_cancel,
    };
    ra_device_client_config_t config = {
        .server_url = "http://127.0.0.1:8765",
        .device_id = "dev-001",
        .user_id = "user-001",
        .name = "C Test Device",
        .client_type = "test-c",
        .mic = &mic,
        .camera = &camera,
        .speaker = &speaker,
        .transport = &transport,
        .speaker_buffer = ra_speaker_buffer_default_config(),
        .log_level = RA_LOG_DISABLED,
    };
    ra_device_client_t *client = ra_device_client_create(&config);
    assert(client != NULL);
    assert(ra_device_client_start(client) == RA_OK);
    assert(mock.connect_count == 1);
    assert(strstr(mock.last_url, "control:ws://127.0.0.1:8765/ws/control") != NULL);
    assert(strstr(mock.last_text, "control.device.register.requested") != NULL);
    assert(strstr(mock.last_text, "realtime_agent.audio_input") != NULL);
    assert(strstr(mock.last_text, "\"type\":\"rgb\"") != NULL);
    assert(strstr(mock.last_text, "\"routes\"") == NULL);
    assert(strstr(mock.last_text, "\"capabilities\"") == NULL);
    ra_device_client_destroy(client);
}

/*
 * 测试目标：验证 server 控制事件能驱动基础状态机和 adapter。
 * 测试方法：依次投递 registered、audio_session.open、speaker start/finish、audio_session.close。
 * 预期结果：连接状态、会话状态和 mic/speaker adapter 调用次数符合预期。
 */
static void test_handle_core_events(void) {
    mock_transport_t mock = {0};
    ra_transport_t transport = {
        .ctx = &mock,
        .connect = mock_connect,
        .send_text = mock_send_text,
        .close = mock_close,
    };
    mock_mic_t mic_ctx = {0};
    ra_mic_source_t mic = {
        .ctx = &mic_ctx,
        .format = ra_audio_format_default(),
        .start = mock_mic_start,
        .stop = mock_mic_stop,
    };
    mock_speaker_t speaker_ctx = {0};
    ra_speaker_sink_t speaker = {
        .ctx = &speaker_ctx,
        .prepare = mock_speaker_prepare,
        .write = mock_speaker_write,
        .drain = mock_speaker_drain,
        .cancel = mock_speaker_cancel,
    };
    ra_device_client_config_t config = {
        .server_url = "http://127.0.0.1:8765",
        .device_id = "dev-001",
        .user_id = "user-001",
        .name = "C Test Device",
        .mic = &mic,
        .speaker = &speaker,
        .transport = &transport,
        .log_level = RA_LOG_DISABLED,
    };
    ra_device_client_t *client = ra_device_client_create(&config);
    assert(client != NULL);
    assert(ra_device_client_handle_event(client, "{\"event_name\":\"control.device.registered\",\"user_id\":\"user-001\",\"producer_id\":\"server\",\"payload\":{}}") == RA_OK);
    assert(ra_device_client_connection_state(client) == RA_CLIENT_REGISTERED);
    assert(ra_device_client_handle_event(client, "{\"event_name\":\"control.audio_session.open.requested\",\"user_id\":\"user-001\",\"producer_id\":\"server\",\"payload\":{}}") == RA_OK);
    assert(ra_device_client_conversation_state(client) == RA_CONVERSATION_ACTIVE);
    assert(mic_ctx.started == 1);
    assert(strstr(mock.last_text, "control.audio_session.opened") != NULL);
    assert(ra_device_client_handle_event(client, "{\"event_name\":\"stream.output.start.requested\",\"user_id\":\"user-001\",\"producer_id\":\"server\",\"session_id\":\"dev-001\",\"stream_id\":\"stream-speaker-001\",\"stream_type\":\"actuator.speaker\",\"payload\":{\"format\":{\"codec\":\"pcm16le\",\"sample_rate\":24000,\"channels\":1,\"chunk_ms\":40}}}") == RA_OK);
    assert(speaker_ctx.prepared == 1);
    assert(strstr(mock.last_text, "stream.output.ready") != NULL);
    assert(strstr(mock.last_text, "\"session_id\":\"dev-001\"") != NULL);
    assert(strstr(mock.last_text, "\"stream_id\":\"stream-speaker-001\"") != NULL);
    assert(strstr(mock.last_text, "\"stream_type\":\"actuator.speaker\"") != NULL);
    assert(ra_device_client_handle_event(client, "{\"event_name\":\"stream.output.finish.requested\",\"user_id\":\"user-001\",\"producer_id\":\"server\",\"session_id\":\"dev-001\",\"stream_id\":\"stream-speaker-001\",\"stream_type\":\"actuator.speaker\",\"payload\":{\"output_last_seq\":1}}") == RA_OK);
    assert(speaker_ctx.drained == 0);
    uint8_t encoded[512];
    size_t encoded_size = 0;
    encode_output_chunk(0, encoded, sizeof(encoded), &encoded_size);
    assert(ra_device_client_handle_output_chunk(client, encoded, encoded_size) == RA_OK);
    assert(speaker_ctx.written == 0);
    assert(speaker_ctx.drained == 0);
    encode_output_chunk(1, encoded, sizeof(encoded), &encoded_size);
    assert(ra_device_client_handle_output_chunk(client, encoded, encoded_size) == RA_OK);
    assert(speaker_ctx.written == 0);
    assert(ra_device_client_pump_output(client) == RA_OK);
    assert(speaker_ctx.written == 1);
    assert(speaker_ctx.drained == 0);
    assert(ra_device_client_pump_output(client) == RA_OK);
    assert(speaker_ctx.written == 2);
    assert(speaker_ctx.drained == 1);
    assert(strstr(mock.last_text, "stream.output.finished") != NULL);
    assert(ra_device_client_handle_event(client, "{\"event_name\":\"control.audio_session.close.requested\",\"user_id\":\"user-001\",\"producer_id\":\"server\",\"payload\":{}}") == RA_OK);
    assert(mic_ctx.stopped == 1);
    assert(speaker_ctx.cancelled == 1);
    assert(ra_device_client_conversation_state(client) == RA_CONVERSATION_WAITING);
    ra_device_client_destroy(client);
}

/*
 * 测试目标：验证 C SDK 注册后可以发送控制心跳，避免服务端把设备判定为离线。
 * 测试方法：模拟 control.device.registered 后调用 ra_device_client_send_heartbeat。
 * 预期结果：心跳事件通过 control transport 发出，并携带在线状态。
 */
static void test_send_heartbeat_after_registered(void) {
    mock_transport_t mock = {0};
    ra_transport_t transport = {
        .ctx = &mock,
        .connect = mock_connect,
        .send_text = mock_send_text,
        .close = mock_close,
    };
    ra_device_client_config_t config = {
        .server_url = "http://127.0.0.1:8765",
        .device_id = "dev-001",
        .user_id = "user-001",
        .name = "C Test Device",
        .client_type = "test-c",
        .transport = &transport,
        .log_level = RA_LOG_DISABLED,
    };
    ra_device_client_t *client = ra_device_client_create(&config);
    assert(client != NULL);
    assert(ra_device_client_send_heartbeat(client) == RA_ERROR_STATE);
    assert(ra_device_client_handle_event(client, "{\"event_name\":\"control.device.registered\",\"user_id\":\"user-001\",\"producer_id\":\"server\",\"payload\":{}}") == RA_OK);
    assert(ra_device_client_send_heartbeat(client) == RA_OK);
    assert(strstr(mock.last_text, "control.device.heartbeat.received") != NULL);
    assert(strstr(mock.last_text, "\"connection_state\":\"online\"") != NULL);
    assert(strstr(mock.last_text, "\"client_type\":\"test-c\"") != NULL);
    ra_device_client_destroy(client);
}

/*
 * 测试目标：验证 RGB 请求不会复用服务端下行音频 stream id，并声明 JPEG 图片格式。
 * 测试方法：投递一个顶层 stream_id 为 `stream_out_*` 的 sensor.rgb 控制事件，使用
 * mock camera 和 mock binary transport 捕获端侧发出的 opened 事件与图片 chunk。
 * 预期结果：控制事件 payload 包含 JPEG/1/1 format，图片 chunk 使用新的 `rgb_*` stream_id，
 * metadata 保留 request_id，避免服务端把图片写入扬声器流或按 PCM 校验。
 */
static void test_rgb_request_uses_image_stream_format_and_id(void) {
    mock_transport_t mock = {0};
    ra_transport_t transport = {
        .ctx = &mock,
        .connect = mock_connect,
        .send_text = mock_send_text,
        .send_binary = mock_send_binary,
        .close = mock_close,
    };
    mock_camera_t camera_ctx = {0};
    ra_camera_source_t camera = {
        .ctx = &camera_ctx,
        .codec = "jpeg",
        .capture_jpeg = mock_camera_capture_jpeg,
        .release_jpeg = mock_camera_release_jpeg,
    };
    ra_device_client_config_t config = {
        .server_url = "http://127.0.0.1:8765",
        .device_id = "dev-001",
        .user_id = "user-001",
        .name = "C Test Device",
        .client_type = "test-c",
        .camera = &camera,
        .transport = &transport,
        .log_level = RA_LOG_DISABLED,
    };
    ra_device_client_t *client = ra_device_client_create(&config);
    assert(client != NULL);
    assert(ra_device_client_handle_event(
               client,
               "{\"event_name\":\"stream.control.open.requested\","
               "\"user_id\":\"user-001\",\"producer_id\":\"server-main\","
               "\"session_id\":\"dev-001\",\"stream_id\":\"stream_out_bad\",\"stream_type\":\"sensor.rgb\","
               "\"payload\":{\"stream_type\":\"sensor.rgb\",\"request_id\":\"asset_req_001\"}}"
           ) == RA_OK);
    assert(camera_ctx.captured == 1);
    assert(camera_ctx.released == 1);
    assert(mock.binary_count == 1);
    assert(strstr(mock.last_text, "stream.input.closed") != NULL);
    assert(strstr(mock.last_text, "\"codec\":\"jpeg\"") != NULL);
    assert(strstr(mock.last_text, "\"sample_rate\":1") != NULL);
    assert(strstr(mock.last_binary_header, "\"stream_id\":\"rgb_") != NULL);
    assert(strstr(mock.last_binary_header, "\"stream_id\":\"stream_out_bad\"") == NULL);
    assert(strstr(mock.last_binary_header, "\"stream_type\":\"sensor.rgb\"") != NULL);
    assert(strstr(mock.last_binary_header, "\"codec\":\"jpeg\"") != NULL);
    assert(strstr(mock.last_binary_header, "\"sample_rate\":1") != NULL);
    assert(strstr(mock.last_binary_header, "\"request_id\":\"asset_req_001\"") != NULL);
    ra_device_client_destroy(client);
}

int main(void) {
    test_start_sends_registration();
    test_handle_core_events();
    test_send_heartbeat_after_registered();
    test_rgb_request_uses_image_stream_format_and_id();
    puts("test_client_state_machine passed");
    return 0;
}
