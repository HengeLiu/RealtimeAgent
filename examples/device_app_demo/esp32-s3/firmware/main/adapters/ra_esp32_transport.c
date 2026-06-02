#include "adapters/ra_esp32_transport.h"

#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "esp_heap_caps.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"

#define RA_ESP32_TRANSPORT_MAX_MESSAGE 8192
#define RA_ESP32_TRANSPORT_TEXT_QUEUE_DEPTH 8
#define RA_ESP32_TRANSPORT_BINARY_QUEUE_DEPTH 8
#define RA_ESP32_TRANSPORT_AUDIO_OUTPUT_QUEUE_DEPTH 64
#define RA_ESP32_WEBSOCKET_TASK_STACK 8192
#define RA_ESP32_TRANSPORT_CONNECTED_BIT BIT0
#define RA_ESP32_TRANSPORT_DISCONNECTED_BIT BIT1

typedef struct {
    size_t size;
    uint8_t *data;
} ra_esp32_transport_message_t;

typedef struct {
    uint8_t *data;
    size_t payload_len;
    size_t received;
    int op_code;
} ra_esp32_transport_fragment_t;

struct ra_esp32_transport {
    char server_url[256];
    char device_id[96];
    esp_websocket_client_handle_t clients[4];
    QueueHandle_t text_queues[4];
    QueueHandle_t binary_queues[4];
    EventGroupHandle_t event_groups[4];
    uint32_t binary_received[4];
    ra_esp32_transport_fragment_t fragments[4];
};

static const char *TAG = "ra_esp32_transport";

static void *payload_alloc(size_t size) {
    void *ptr = heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (ptr == NULL) {
        ptr = heap_caps_malloc(size, MALLOC_CAP_8BIT);
    }
    return ptr;
}

static UBaseType_t binary_queue_depth_for_channel(ra_transport_channel_t channel) {
    if (channel == RA_TRANSPORT_AUDIO_OUTPUT) {
        return RA_ESP32_TRANSPORT_AUDIO_OUTPUT_QUEUE_DEPTH;
    }
    return RA_ESP32_TRANSPORT_BINARY_QUEUE_DEPTH;
}

static ra_transport_channel_t channel_from_client(ra_esp32_transport_t *transport, esp_websocket_client_handle_t client) {
    for (int i = 0; i < 4; ++i) {
        if (transport->clients[i] == client) {
            return (ra_transport_channel_t)i;
        }
    }
    return RA_TRANSPORT_CONTROL;
}

static void clear_fragment(ra_esp32_transport_t *transport, ra_transport_channel_t channel) {
    free(transport->fragments[channel].data);
    memset(&transport->fragments[channel], 0, sizeof(transport->fragments[channel]));
}

static void clear_queue(QueueHandle_t queue) {
    if (queue == NULL) {
        return;
    }
    ra_esp32_transport_message_t message;
    while (xQueueReceive(queue, &message, 0) == pdTRUE) {
        free(message.data);
    }
}

static void clear_channel_queues(ra_esp32_transport_t *transport, ra_transport_channel_t channel) {
    clear_queue(transport->text_queues[channel]);
    clear_queue(transport->binary_queues[channel]);
    clear_fragment(transport, channel);
}

static void enqueue_message(
    ra_esp32_transport_t *transport,
    ra_transport_channel_t channel,
    int op_code,
    uint8_t *message_data,
    size_t message_size
) {
    ra_esp32_transport_message_t message = {
        .size = message_size,
        .data = message_data,
    };
    QueueHandle_t queue = op_code == 0x2 ? transport->binary_queues[channel] : transport->text_queues[channel];
    if (op_code == 0x2) {
        transport->binary_received[channel]++;
        uint32_t count = transport->binary_received[channel];
        if (channel == RA_TRANSPORT_AUDIO_OUTPUT && (count <= 3 || count % 20 == 0)) {
            ESP_LOGI(TAG, "%s.binary received len=%u count=%u",
                     ra_transport_channel_name(channel), (unsigned)message_size, (unsigned)count);
        }
    }
    if (queue != NULL && xQueueSend(queue, &message, 0) != pdTRUE) {
        ESP_LOGW(TAG, "%s.drop queue full", ra_transport_channel_name(channel));
        free(message.data);
    }
}

static void enqueue_disconnect(ra_esp32_transport_t *transport, ra_transport_channel_t channel) {
    ra_esp32_transport_message_t message = {
        .size = 0,
        .data = NULL,
    };
    if (transport->text_queues[channel] != NULL) {
        (void)xQueueSend(transport->text_queues[channel], &message, 0);
    }
    if (transport->binary_queues[channel] != NULL) {
        (void)xQueueSend(transport->binary_queues[channel], &message, 0);
    }
}

static void websocket_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
    (void)base;
    ra_esp32_transport_t *transport = (ra_esp32_transport_t *)handler_args;
    esp_websocket_event_data_t *data = (esp_websocket_event_data_t *)event_data;
    ra_transport_channel_t channel = channel_from_client(transport, data->client);
    if (event_id == WEBSOCKET_EVENT_CONNECTED) {
        ESP_LOGI(TAG, "%s.connected", ra_transport_channel_name(channel));
        if (transport->event_groups[channel] != NULL) {
            xEventGroupClearBits(transport->event_groups[channel], RA_ESP32_TRANSPORT_DISCONNECTED_BIT);
            xEventGroupSetBits(transport->event_groups[channel], RA_ESP32_TRANSPORT_CONNECTED_BIT);
        }
        return;
    }
    if (event_id == WEBSOCKET_EVENT_DISCONNECTED) {
        ESP_LOGW(TAG, "%s.disconnected", ra_transport_channel_name(channel));
        enqueue_disconnect(transport, channel);
        if (transport->event_groups[channel] != NULL) {
            xEventGroupClearBits(transport->event_groups[channel], RA_ESP32_TRANSPORT_CONNECTED_BIT);
            xEventGroupSetBits(transport->event_groups[channel], RA_ESP32_TRANSPORT_DISCONNECTED_BIT);
        }
        return;
    }
    if (event_id != WEBSOCKET_EVENT_DATA || data->data_len <= 0) {
        return;
    }
    if (data->payload_len > RA_ESP32_TRANSPORT_MAX_MESSAGE) {
        ESP_LOGW(TAG, "%s.drop oversized payload_len=%d limit=%d",
                 ra_transport_channel_name(channel), data->payload_len, RA_ESP32_TRANSPORT_MAX_MESSAGE);
        clear_fragment(transport, channel);
        return;
    }
    if (data->payload_offset != 0 || data->data_len != data->payload_len) {
        ra_esp32_transport_fragment_t *fragment = &transport->fragments[channel];
        if (data->payload_offset == 0) {
            clear_fragment(transport, channel);
            fragment->data = payload_alloc((size_t)data->payload_len);
            if (fragment->data == NULL) {
                ESP_LOGW(TAG, "%s.drop fragment alloc failed payload=%d", ra_transport_channel_name(channel), data->payload_len);
                return;
            }
            fragment->payload_len = (size_t)data->payload_len;
            fragment->op_code = data->op_code;
        }
        if (fragment->data == NULL || fragment->payload_len != (size_t)data->payload_len ||
            (size_t)data->payload_offset + (size_t)data->data_len > fragment->payload_len) {
            ESP_LOGW(TAG, "%s.drop fragmented message offset=%d len=%d payload=%d",
                     ra_transport_channel_name(channel), data->payload_offset, data->data_len, data->payload_len);
            clear_fragment(transport, channel);
            return;
        }
        memcpy(fragment->data + data->payload_offset, data->data_ptr, (size_t)data->data_len);
        fragment->received += (size_t)data->data_len;
        if (fragment->received >= fragment->payload_len) {
            uint8_t *complete = fragment->data;
            size_t complete_size = fragment->payload_len;
            int complete_op_code = fragment->op_code;
            memset(fragment, 0, sizeof(*fragment));
            enqueue_message(transport, channel, complete_op_code, complete, complete_size);
        }
        return;
    }
    uint8_t *message_data = payload_alloc((size_t)data->data_len);
    if (message_data == NULL) {
        ESP_LOGW(TAG, "%s.drop alloc failed len=%d", ra_transport_channel_name(channel), data->data_len);
        return;
    }
    memcpy(message_data, data->data_ptr, (size_t)data->data_len);
    enqueue_message(transport, channel, data->op_code, message_data, (size_t)data->data_len);
}

static void build_channel_url(ra_esp32_transport_t *transport, ra_transport_channel_t channel, char *out, size_t capacity) {
    const char *path = "/ws/control";
    switch (channel) {
        case RA_TRANSPORT_CONTROL:
            path = "/ws/control";
            break;
        case RA_TRANSPORT_AUDIO_INPUT:
            path = "/ws/stream/audio/input";
            break;
        case RA_TRANSPORT_AUDIO_OUTPUT:
            path = "/ws/stream/audio/output";
            break;
        case RA_TRANSPORT_VISUAL_INPUT:
            path = "/ws/stream/visual/input";
            break;
    }
    char base[256];
    strlcpy(base, transport->server_url, sizeof(base));
    if (strncmp(base, "http://", 7) == 0) {
        memmove(base + 5, base + 7, strlen(base + 7) + 1);
        memcpy(base, "ws://", 5);
    } else if (strncmp(base, "https://", 8) == 0) {
        memmove(base + 6, base + 8, strlen(base + 8) + 1);
        memcpy(base, "wss://", 6);
    }
    size_t len = strlen(base);
    while (len > 0 && base[len - 1] == '/') {
        base[--len] = '\0';
    }
    if (channel == RA_TRANSPORT_CONTROL) {
        snprintf(out, capacity, "%s%s", base, path);
    } else {
        snprintf(out, capacity, "%s%s?device_id=%s", base, path, transport->device_id);
    }
}

static int transport_connect(void *ctx, ra_transport_channel_t channel, const char *url) {
    ra_esp32_transport_t *transport = (ra_esp32_transport_t *)ctx;
    char computed_url[320];
    if (url == NULL || url[0] == '\0') {
        build_channel_url(transport, channel, computed_url, sizeof(computed_url));
        url = computed_url;
    }
    if (transport->clients[channel] != NULL) {
        esp_websocket_client_stop(transport->clients[channel]);
        esp_websocket_client_destroy(transport->clients[channel]);
        transport->clients[channel] = NULL;
    }
    clear_channel_queues(transport, channel);
    esp_websocket_client_config_t config = {
        .uri = url,
        .buffer_size = RA_ESP32_TRANSPORT_MAX_MESSAGE,
        .task_stack = RA_ESP32_WEBSOCKET_TASK_STACK,
        .network_timeout_ms = 10000,
    };
    esp_websocket_client_handle_t client = esp_websocket_client_init(&config);
    if (client == NULL) {
        return -1;
    }
    transport->clients[channel] = client;
    esp_websocket_register_events(client, WEBSOCKET_EVENT_ANY, websocket_event_handler, transport);
    ESP_LOGI(TAG, "%s.connect url=%s", ra_transport_channel_name(channel), url);
    if (transport->event_groups[channel] != NULL) {
        xEventGroupClearBits(
            transport->event_groups[channel],
            RA_ESP32_TRANSPORT_CONNECTED_BIT | RA_ESP32_TRANSPORT_DISCONNECTED_BIT
        );
    }
    if (esp_websocket_client_start(client) != ESP_OK) {
        return -1;
    }
    if (transport->event_groups[channel] == NULL) {
        return 0;
    }
    EventBits_t bits = xEventGroupWaitBits(
        transport->event_groups[channel],
        RA_ESP32_TRANSPORT_CONNECTED_BIT | RA_ESP32_TRANSPORT_DISCONNECTED_BIT,
        pdFALSE,
        pdFALSE,
        pdMS_TO_TICKS(10000)
    );
    if ((bits & RA_ESP32_TRANSPORT_CONNECTED_BIT) != 0) {
        return 0;
    }
    ESP_LOGE(TAG, "%s.connect timeout_or_disconnected bits=0x%x", ra_transport_channel_name(channel), (unsigned)bits);
    return -1;
}

static int transport_send_text(void *ctx, ra_transport_channel_t channel, const char *text, size_t size) {
    ra_esp32_transport_t *transport = (ra_esp32_transport_t *)ctx;
    if (transport->clients[channel] == NULL) {
        return -1;
    }
    int sent = esp_websocket_client_send_text(transport->clients[channel], text, (int)size, portMAX_DELAY);
    return sent == (int)size ? 0 : -1;
}

static int transport_send_binary(void *ctx, ra_transport_channel_t channel, const uint8_t *data, size_t size) {
    ra_esp32_transport_t *transport = (ra_esp32_transport_t *)ctx;
    if (transport->clients[channel] == NULL) {
        return -1;
    }
    int sent = esp_websocket_client_send_bin(transport->clients[channel], (const char *)data, (int)size, portMAX_DELAY);
    return sent == (int)size ? 0 : -1;
}

static int recv_from_queue(QueueHandle_t queue, uint8_t *out, size_t capacity, size_t *size) {
    if (queue == NULL || out == NULL || size == NULL) {
        return -1;
    }
    ra_esp32_transport_message_t message;
    if (xQueueReceive(queue, &message, portMAX_DELAY) != pdTRUE) {
        return -1;
    }
    if (message.size > capacity || message.data == NULL) {
        free(message.data);
        return -1;
    }
    memcpy(out, message.data, message.size);
    *size = message.size;
    free(message.data);
    return 0;
}

static int transport_recv_text(void *ctx, ra_transport_channel_t channel, char *out, size_t capacity, size_t *size) {
    ra_esp32_transport_t *transport = (ra_esp32_transport_t *)ctx;
    int rc = recv_from_queue(transport->text_queues[channel], (uint8_t *)out, capacity - 1, size);
    if (rc == 0) {
        out[*size] = '\0';
    }
    return rc;
}

static int transport_recv_binary(void *ctx, ra_transport_channel_t channel, uint8_t *out, size_t capacity, size_t *size) {
    ra_esp32_transport_t *transport = (ra_esp32_transport_t *)ctx;
    return recv_from_queue(transport->binary_queues[channel], out, capacity, size);
}

static int transport_close(void *ctx, ra_transport_channel_t channel) {
    ra_esp32_transport_t *transport = (ra_esp32_transport_t *)ctx;
    if (transport->clients[channel] != NULL) {
        esp_websocket_client_stop(transport->clients[channel]);
        esp_websocket_client_destroy(transport->clients[channel]);
        transport->clients[channel] = NULL;
    }
    return 0;
}

ra_esp32_transport_t *ra_esp32_transport_create(const char *server_url, const char *device_id) {
    ra_esp32_transport_t *transport = calloc(1, sizeof(*transport));
    if (transport == NULL) {
        return NULL;
    }
    strlcpy(transport->server_url, server_url == NULL ? "" : server_url, sizeof(transport->server_url));
    strlcpy(transport->device_id, device_id == NULL ? "" : device_id, sizeof(transport->device_id));
    for (int i = 0; i < 4; ++i) {
        transport->text_queues[i] = xQueueCreate(RA_ESP32_TRANSPORT_TEXT_QUEUE_DEPTH, sizeof(ra_esp32_transport_message_t));
        transport->binary_queues[i] = xQueueCreate(
            binary_queue_depth_for_channel((ra_transport_channel_t)i),
            sizeof(ra_esp32_transport_message_t)
        );
        transport->event_groups[i] = xEventGroupCreate();
    }
    return transport;
}

void ra_esp32_transport_destroy(ra_esp32_transport_t *transport) {
    if (transport == NULL) {
        return;
    }
    for (int i = 0; i < 4; ++i) {
        transport_close(transport, (ra_transport_channel_t)i);
        clear_fragment(transport, (ra_transport_channel_t)i);
        if (transport->text_queues[i] != NULL) {
            vQueueDelete(transport->text_queues[i]);
        }
        if (transport->binary_queues[i] != NULL) {
            vQueueDelete(transport->binary_queues[i]);
        }
        if (transport->event_groups[i] != NULL) {
            vEventGroupDelete(transport->event_groups[i]);
        }
    }
    free(transport);
}

ra_transport_t ra_esp32_transport_as_sdk_transport(ra_esp32_transport_t *transport) {
    ra_transport_t sdk_transport = {
        .ctx = transport,
        .connect = transport_connect,
        .send_text = transport_send_text,
        .send_binary = transport_send_binary,
        .recv_text = transport_recv_text,
        .recv_binary = transport_recv_binary,
        .close = transport_close,
    };
    return sdk_transport;
}
