#include <inttypes.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "driver/i2s_pdm.h"
#include "driver/i2s_std.h"
#include "esp_aec.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#define MIC_PDM_CLK_GPIO GPIO_NUM_42
#define MIC_PDM_DATA_GPIO GPIO_NUM_41
#define SPK_I2S_BCLK_GPIO GPIO_NUM_7
#define SPK_I2S_LRCK_GPIO GPIO_NUM_8
#define SPK_I2S_DOUT_GPIO GPIO_NUM_9

#define SR_SAMPLE_RATE_HZ 16000
#define AUDIO_FRAME_SAMPLES 320
#define WS_SEND_TIMEOUT_MS 50
#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAILED_BIT BIT1
#define RING_SECONDS 4
#define RING_CAPACITY_SAMPLES (SR_SAMPLE_RATE_HZ * RING_SECONDS)
#define WS_BINARY_RX_MAX_BYTES (256 * 1024)
#define MIC_SEND_QUEUE_LENGTH 16
#define MIC_SEND_TIMEOUT_MS 200
#define LOCAL_TONE_AMPLITUDE 10000.0f
#define LOCAL_TONE_ON_MS 1000
#define LOCAL_TONE_OFF_MS 1000
#define LOCAL_TONE_FADE_MS 150
#define SPEAKER_STARTUP_SILENCE_MS 500
#define PLAYBACK_FADE_IN_MS 80
#ifndef CONFIG_GLASS_AEC_TEST_LOCAL_TONE_ONLY
#define CONFIG_GLASS_AEC_TEST_LOCAL_TONE_ONLY 0
#endif
#ifndef CONFIG_GLASS_AEC_TEST_LOCAL_TONE_HZ
#define CONFIG_GLASS_AEC_TEST_LOCAL_TONE_HZ 440
#endif
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

typedef struct {
    const char *ssid;
    const char *password;
} wifi_profile_t;

typedef struct {
    int16_t *data;
    size_t capacity;
    size_t read_index;
    size_t write_index;
    size_t count;
    uint32_t dropped_samples;
    SemaphoreHandle_t mutex;
} pcm_ring_t;

typedef struct {
    uint8_t *data;
    int data_len;
    size_t payload_bytes;
} mic_media_packet_t;

static const char *TAG = "test-official-aec";

static i2s_chan_handle_t s_mic_rx_chan = NULL;
static i2s_chan_handle_t s_spk_tx_chan = NULL;
static esp_websocket_client_handle_t s_ws_mic_client = NULL;
static esp_websocket_client_handle_t s_ws_playback_client = NULL;
static EventGroupHandle_t s_wifi_event_group = NULL;
static QueueHandle_t s_mic_send_queue = NULL;

static pcm_ring_t s_playback_ring = {0};
static pcm_ring_t s_ref_ring = {0};
static volatile bool s_ws_mic_connected = false;
static volatile bool s_ws_playback_connected = false;
static volatile bool s_playback_active = false;
static uint32_t s_seq = 0;
static uint64_t s_sent_mic_bytes = 0;
static uint64_t s_received_playback_bytes = 0;
static uint8_t *s_ws_binary_rx_buffer = NULL;
static int s_ws_binary_rx_expected_len = 0;

static const wifi_profile_t s_wifi_profiles[] = {
    {CONFIG_GLASS_WIFI_PRIMARY_SSID, CONFIG_GLASS_WIFI_PRIMARY_PASSWORD},
    {CONFIG_GLASS_WIFI_FALLBACK_SSID, CONFIG_GLASS_WIFI_FALLBACK_PASSWORD},
};

/**
 * 返回当前测试选择的 ESP-SR AEC 模式。
 *
 * 主要逻辑：
 * 1. 根据 Kconfig 中的 choice 映射到官方 `aec_mode_t`。
 * 2. 默认选择官方推荐的语音识别低资源模式。
 *
 * 返回值：
 * 1. 可直接传给 `aec_create` 的 AEC 模式。
 */
static aec_mode_t selected_aec_mode(void)
{
#if CONFIG_GLASS_AEC_TEST_MODE_SR_HIGH_PERF
    return AEC_MODE_SR_HIGH_PERF;
#elif CONFIG_GLASS_AEC_TEST_MODE_VOIP_LOW_COST
    return AEC_MODE_VOIP_LOW_COST;
#elif CONFIG_GLASS_AEC_TEST_MODE_VOIP_HIGH_PERF
    return AEC_MODE_VOIP_HIGH_PERF;
#else
    return AEC_MODE_SR_LOW_COST;
#endif
}

/**
 * 获取当前毫秒时间。
 *
 * 主要逻辑：
 * 1. 读取 ESP 定时器微秒时间。
 * 2. 转换为毫秒，作为媒体帧时间戳。
 *
 * 返回值：
 * 1. 当前启动后的毫秒数。
 */
static uint64_t now_ms(void)
{
    return (uint64_t)(esp_timer_get_time() / 1000);
}

/**
 * 创建一个短延迟 PCM 环形缓冲。
 *
 * 主要逻辑：
 * 1. 分配内部 RAM，失败时回退到普通可访问内存。
 * 2. 创建互斥锁，保护播放任务和 AEC 任务并发读写。
 *
 * 参数：
 * 1. `ring`：待初始化的环形缓冲对象。
 * 2. `capacity`：缓冲容量，单位为 16 位采样点。
 *
 * 返回值：
 * 1. `ESP_OK` 表示初始化成功。
 * 2. 其他错误码表示内存或互斥锁创建失败。
 */
static esp_err_t pcm_ring_init(pcm_ring_t *ring, size_t capacity)
{
    memset(ring, 0, sizeof(*ring));
    ring->data = heap_caps_calloc(capacity, sizeof(int16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (ring->data == NULL) {
        ring->data = heap_caps_calloc(capacity, sizeof(int16_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    }
    ESP_RETURN_ON_FALSE(ring->data != NULL, ESP_ERR_NO_MEM, TAG, "ring buffer alloc failed");

    ring->mutex = xSemaphoreCreateMutex();
    ESP_RETURN_ON_FALSE(ring->mutex != NULL, ESP_ERR_NO_MEM, TAG, "ring mutex alloc failed");
    ring->capacity = capacity;
    return ESP_OK;
}

/**
 * 创建 FreeRTOS 任务并检查结果。
 *
 * 主要逻辑：
 * 1. 统一检查任务是否创建成功，避免内部 RAM 不足时静默失败。
 * 2. 支持按需固定到指定 CPU，方便音频采集和播放分核运行。
 *
 * 参数：
 * 1. `task`：任务入口函数。
 * 2. `name`：任务名称。
 * 3. `stack_depth`：任务栈大小，单位为 FreeRTOS word。
 * 4. `arg`：传给任务入口的参数。
 * 5. `priority`：任务优先级。
 * 6. `core_id`：固定 CPU 编号；传 `tskNO_AFFINITY` 表示不固定。
 *
 * 返回值：
 * 1. `ESP_OK` 表示任务创建成功。
 * 2. `ESP_ERR_NO_MEM` 表示任务创建失败。
 */
static esp_err_t create_checked_task(
    TaskFunction_t task,
    const char *name,
    uint32_t stack_depth,
    void *arg,
    UBaseType_t priority,
    BaseType_t core_id
)
{
    BaseType_t ok = pdFALSE;
    if (core_id == tskNO_AFFINITY) {
        ok = xTaskCreate(task, name, stack_depth, arg, priority, NULL);
    } else {
        ok = xTaskCreatePinnedToCore(task, name, stack_depth, arg, priority, NULL, core_id);
    }
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "create task failed: name=%s stack=%" PRIu32 " priority=%u core=%d", name, stack_depth, priority, core_id);
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

/**
 * 向 PCM 环形缓冲写入采样点。
 *
 * 主要逻辑：
 * 1. 如果缓冲已满，丢弃最旧数据，保持低延迟。
 * 2. 写入新采样并更新写指针。
 *
 * 参数：
 * 1. `ring`：目标缓冲。
 * 2. `samples`：待写入采样。
 * 3. `sample_count`：采样点数量。
 *
 * 返回值：
 * 1. 实际写入的采样点数量。
 */
static size_t pcm_ring_write(pcm_ring_t *ring, const int16_t *samples, size_t sample_count)
{
    size_t written = 0;

    if (ring->mutex == NULL || ring->data == NULL) {
        return 0;
    }
    xSemaphoreTake(ring->mutex, portMAX_DELAY);
    for (size_t index = 0; index < sample_count; index += 1) {
        if (ring->count == ring->capacity) {
            ring->read_index = (ring->read_index + 1) % ring->capacity;
            ring->count -= 1;
            ring->dropped_samples += 1;
        }
        ring->data[ring->write_index] = samples[index];
        ring->write_index = (ring->write_index + 1) % ring->capacity;
        ring->count += 1;
        written += 1;
    }
    xSemaphoreGive(ring->mutex);
    return written;
}

/**
 * 从 PCM 环形缓冲读取采样点。
 *
 * 主要逻辑：
 * 1. 尽量读取指定数量的采样。
 * 2. 不足部分按需要填充静音，保证 AEC 每帧长度稳定。
 *
 * 参数：
 * 1. `ring`：源缓冲。
 * 2. `output`：输出缓冲。
 * 3. `sample_count`：期望读取的采样点数量。
 * 4. `zero_fill`：不足时是否补零。
 *
 * 返回值：
 * 1. 从缓冲中真实读取的采样点数量。
 */
static size_t pcm_ring_read(pcm_ring_t *ring, int16_t *output, size_t sample_count, bool zero_fill)
{
    size_t read_count = 0;

    if (ring->mutex == NULL || ring->data == NULL) {
        if (zero_fill) {
            memset(output, 0, sample_count * sizeof(int16_t));
        }
        return 0;
    }

    xSemaphoreTake(ring->mutex, portMAX_DELAY);
    while (read_count < sample_count && ring->count > 0) {
        output[read_count] = ring->data[ring->read_index];
        ring->read_index = (ring->read_index + 1) % ring->capacity;
        ring->count -= 1;
        read_count += 1;
    }
    xSemaphoreGive(ring->mutex);

    if (zero_fill && read_count < sample_count) {
        memset(output + read_count, 0, (sample_count - read_count) * sizeof(int16_t));
    }
    return read_count;
}

/**
 * 查询 PCM 环形缓冲当前积压量。
 *
 * 主要逻辑：
 * 1. 加锁读取 `count`。
 * 2. 返回采样点数量，用于 DEBUG 日志。
 *
 * 参数：
 * 1. `ring`：待查询的缓冲。
 *
 * 返回值：
 * 1. 当前缓冲内采样点数量。
 */
static size_t pcm_ring_count(pcm_ring_t *ring)
{
    size_t count = 0;
    if (ring->mutex == NULL) {
        return 0;
    }
    xSemaphoreTake(ring->mutex, portMAX_DELAY);
    count = ring->count;
    xSemaphoreGive(ring->mutex);
    return count;
}

/**
 * 清空 PCM 环形缓冲。
 *
 * 主要逻辑：
 * 1. 在收到打断取消时丢弃尚未播放的旧音频。
 * 2. 同步重置读写指针，避免取消后继续播本地缓存。
 *
 * 参数：
 * 1. `ring`：待清空的缓冲。
 *
 * 返回值：
 * 1. 无返回值。
 */
static void pcm_ring_clear(pcm_ring_t *ring)
{
    if (ring->mutex == NULL || ring->data == NULL) {
        return;
    }
    xSemaphoreTake(ring->mutex, portMAX_DELAY);
    ring->read_index = 0;
    ring->write_index = 0;
    ring->count = 0;
    xSemaphoreGive(ring->mutex);
}

/**
 * 分配 ESP-SR AEC 要求的 16 字节对齐 int16 缓冲。
 *
 * 主要逻辑：
 * 1. 优先使用内部 RAM，降低 AEC 处理抖动。
 * 2. 如果内部 RAM 不足，回退到普通可访问内存。
 *
 * 参数：
 * 1. `sample_count`：采样点数量。
 *
 * 返回值：
 * 1. 成功时返回清零后的缓冲指针。
 * 2. 失败时返回 NULL。
 */
static int16_t *alloc_aligned_i16(size_t sample_count)
{
    size_t bytes = sample_count * sizeof(int16_t);
    int16_t *buffer = heap_caps_aligned_alloc(16, bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (buffer == NULL) {
        buffer = heap_caps_aligned_alloc(16, bytes, MALLOC_CAP_8BIT);
    }
    if (buffer != NULL) {
        memset(buffer, 0, bytes);
    }
    return buffer;
}

/**
 * 初始化麦克风 PDM 输入通道。
 *
 * 主要逻辑：
 * 1. 创建 I2S0 RX 通道。
 * 2. 按 16k 单声道 PDM 参数绑定麦克风 GPIO。
 * 3. 使能通道，供 AEC 任务持续读取。
 *
 * 返回值：
 * 1. `ESP_OK` 表示初始化成功。
 * 2. 其他错误码表示 I2S 创建、配置或使能失败。
 */
static esp_err_t init_mic_i2s(void)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, NULL, &s_mic_rx_chan), TAG, "new mic channel failed");

    i2s_pdm_rx_config_t pdm_rx_cfg = {
        .clk_cfg = I2S_PDM_RX_CLK_DEFAULT_CONFIG(SR_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_PDM_RX_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .clk = MIC_PDM_CLK_GPIO,
            .din = MIC_PDM_DATA_GPIO,
            .invert_flags = {
                .clk_inv = false,
            },
        },
    };
    ESP_RETURN_ON_ERROR(i2s_channel_init_pdm_rx_mode(s_mic_rx_chan, &pdm_rx_cfg), TAG, "init mic pdm failed");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_mic_rx_chan), TAG, "enable mic failed");
    ESP_LOGI(TAG, "MIC ready: sr=%d clk=%d data=%d", SR_SAMPLE_RATE_HZ, MIC_PDM_CLK_GPIO, MIC_PDM_DATA_GPIO);
    return ESP_OK;
}

/**
 * 初始化扬声器 I2S 输出通道。
 *
 * 主要逻辑：
 * 1. 创建 I2S1 TX 通道。
 * 2. 按现有眼镜硬件的 16k 立体声 32 位 MSB 参数初始化。
 * 3. 使能通道，供播放任务写入 Omni 下行音频。
 *
 * 返回值：
 * 1. `ESP_OK` 表示初始化成功。
 * 2. 其他错误码表示 I2S 创建、配置或使能失败。
 */
static esp_err_t init_speaker_i2s(void)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SR_SAMPLE_RATE_HZ),
#if CONFIG_GLASS_AEC_TEST_SPK_FORMAT_16BIT_STEREO
        .slot_cfg = I2S_STD_MSB_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
#else
        .slot_cfg = I2S_STD_MSB_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
#endif
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = SPK_I2S_BCLK_GPIO,
            .ws = SPK_I2S_LRCK_GPIO,
            .dout = SPK_I2S_DOUT_GPIO,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    chan_cfg.auto_clear_after_cb = true;

    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, &s_spk_tx_chan, NULL), TAG, "new speaker channel failed");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_spk_tx_chan, &std_cfg), TAG, "init speaker std failed");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_spk_tx_chan), TAG, "enable speaker failed");

    /*
     * 部分 I2S 数字功放在 BCLK/LRCK 刚开始输出时会有很短的偏置建立过程。
     * 先送一小段静音，让 DMA、时钟和功放输入稳定后再进入真实播放，避免启动爆破音。
     */
    for (int index = 0; index < SPEAKER_STARTUP_SILENCE_MS / ((AUDIO_FRAME_SAMPLES * 1000) / SR_SAMPLE_RATE_HZ); index += 1) {
#if CONFIG_GLASS_AEC_TEST_SPK_FORMAT_16BIT_STEREO
        int16_t silence[AUDIO_FRAME_SAMPLES * 2] = {0};
        const uint8_t *silence_data = (const uint8_t *)silence;
        size_t silence_size = sizeof(silence);
#else
        int32_t silence[AUDIO_FRAME_SAMPLES * 2] = {0};
        const uint8_t *silence_data = (const uint8_t *)silence;
        size_t silence_size = sizeof(silence);
#endif
        size_t written_total = 0;
        while (written_total < silence_size) {
            size_t bytes_written = 0;
            esp_err_t ret = i2s_channel_write(
                s_spk_tx_chan,
                silence_data + written_total,
                silence_size - written_total,
                &bytes_written,
                pdMS_TO_TICKS(1000)
            );
            if (ret != ESP_OK || bytes_written == 0) {
                ESP_LOGD(TAG, "speaker startup silence write failed: %s", esp_err_to_name(ret));
                break;
            }
            written_total += bytes_written;
        }
    }
    ESP_LOGI(
        TAG,
        "Speaker ready: sr=%d bclk=%d lrck=%d dout=%d format=%s",
        SR_SAMPLE_RATE_HZ,
        SPK_I2S_BCLK_GPIO,
        SPK_I2S_LRCK_GPIO,
        SPK_I2S_DOUT_GPIO,
#if CONFIG_GLASS_AEC_TEST_SPK_FORMAT_16BIT_STEREO
        "16bit_stereo"
#elif CONFIG_GLASS_AEC_TEST_SPK_FORMAT_32BIT_MSB_RAW
        "32bit_msb_raw"
#else
        "32bit_msb_shifted"
#endif
    );
    return ESP_OK;
}

/**
 * 将 16 位单声道 PCM 转为扬声器需要的 32 位立体声 MSB PCM。
 *
 * 主要逻辑：
 * 1. 对每个输入采样应用固定增益。
 * 2. 左右声道写入相同采样。
 * 3. 左移 16 位适配当前 I2S STD 32 位 MSB 输出格式。
 *
 * 参数：
 * 1. `input`：16 位单声道 PCM。
 * 2. `sample_count`：输入采样点数量。
 * 3. `output`：32 位立体声输出缓冲。
 */
static void mono16_to_stereo32_msb(const int16_t *input, size_t sample_count, int32_t *output)
{
    float gain = (float)CONFIG_GLASS_AEC_TEST_PLAYBACK_GAIN_PERMILLE / 1000.0f;
    for (size_t index = 0; index < sample_count; index += 1) {
        int32_t sample = (int32_t)((float)input[index] * gain);
        if (sample > INT16_MAX) {
            sample = INT16_MAX;
        } else if (sample < INT16_MIN) {
            sample = INT16_MIN;
        }
        int32_t stereo_value = sample << 16;
        output[index * 2] = stereo_value;
        output[index * 2 + 1] = stereo_value;
    }
}

/**
 * 将 16 位单声道 PCM 转为 16 位立体声 PCM。
 *
 * 主要逻辑：
 * 1. 对每个输入采样应用固定增益。
 * 2. 左右声道写入相同的 16 位采样。
 *
 * 参数：
 * 1. `input`：16 位单声道 PCM。
 * 2. `sample_count`：输入采样点数量。
 * 3. `output`：16 位立体声输出缓冲。
 */
static void mono16_to_stereo16(const int16_t *input, size_t sample_count, int16_t *output)
{
    float gain = (float)CONFIG_GLASS_AEC_TEST_PLAYBACK_GAIN_PERMILLE / 1000.0f;
    for (size_t index = 0; index < sample_count; index += 1) {
        int32_t sample = (int32_t)((float)input[index] * gain);
        if (sample > INT16_MAX) {
            sample = INT16_MAX;
        } else if (sample < INT16_MIN) {
            sample = INT16_MIN;
        }
        output[index * 2] = (int16_t)sample;
        output[index * 2 + 1] = (int16_t)sample;
    }
}

/**
 * 将 16 位单声道 PCM 转为 32 位立体声但不左移。
 *
 * 主要逻辑：
 * 1. 保留 16 位采样在 32 位槽的低位，用于验证外设是否实际按低位读取。
 * 2. 仅用于扬声器格式诊断，不作为默认播放路径。
 *
 * 参数：
 * 1. `input`：16 位单声道 PCM。
 * 2. `sample_count`：输入采样点数量。
 * 3. `output`：32 位立体声输出缓冲。
 */
static void mono16_to_stereo32_raw(const int16_t *input, size_t sample_count, int32_t *output)
{
    float gain = (float)CONFIG_GLASS_AEC_TEST_PLAYBACK_GAIN_PERMILLE / 1000.0f;
    for (size_t index = 0; index < sample_count; index += 1) {
        int32_t sample = (int32_t)((float)input[index] * gain);
        if (sample > INT16_MAX) {
            sample = INT16_MAX;
        } else if (sample < INT16_MIN) {
            sample = INT16_MIN;
        }
        output[index * 2] = sample;
        output[index * 2 + 1] = sample;
    }
}

/**
 * 对播放起始帧做短淡入。
 *
 * 主要逻辑：
 * 1. 当播放从静音进入有效音频时，调用方传入剩余淡入采样数。
 * 2. 函数按线性包络缩小当前帧前部采样，避免从 0 突然跳到大振幅导致爆破音。
 * 3. 淡入计数用完后不再修改后续音频。
 *
 * 参数：
 * 1. `samples`：待处理的 PCM16 单声道采样，会被原地修改。
 * 2. `sample_count`：采样数量。
 * 3. `fade_remaining`：剩余淡入采样数，函数会递减它。
 */
static void apply_playback_fade_in(int16_t *samples, size_t sample_count, int *fade_remaining)
{
    const int fade_total = (SR_SAMPLE_RATE_HZ * PLAYBACK_FADE_IN_MS) / 1000;
    if (samples == NULL || fade_remaining == NULL || *fade_remaining <= 0 || fade_total <= 0) {
        return;
    }

    for (size_t index = 0; index < sample_count && *fade_remaining > 0; index += 1) {
        int elapsed = fade_total - *fade_remaining;
        float envelope = (float)elapsed / (float)fade_total;
        samples[index] = (int16_t)((float)samples[index] * envelope);
        *fade_remaining -= 1;
    }
}

/**
 * 去除 AEC 输出中的直流偏置。
 *
 * 主要逻辑：
 * 1. 计算当前帧平均值，把它视为直流分量。
 * 2. 每个采样减去平均值，并做 16 位范围保护。
 *
 * 参数：
 * 1. `samples`：待处理的 PCM16 采样，会被原地修改。
 * 2. `sample_count`：采样点数量。
 */
static void remove_dc_offset_i16(int16_t *samples, size_t sample_count)
{
    int64_t sum = 0;
    if (samples == NULL || sample_count == 0) {
        return;
    }
    for (size_t index = 0; index < sample_count; index += 1) {
        sum += samples[index];
    }
    int32_t mean = (int32_t)(sum / (int64_t)sample_count);
    for (size_t index = 0; index < sample_count; index += 1) {
        int32_t value = (int32_t)samples[index] - mean;
        if (value > INT16_MAX) {
            value = INT16_MAX;
        } else if (value < INT16_MIN) {
            value = INT16_MIN;
        }
        samples[index] = (int16_t)value;
    }
}

/**
 * 选择首个可用 WiFi 配置。
 *
 * 主要逻辑：
 * 1. 依次检查主 WiFi 和兜底 WiFi。
 * 2. 返回 SSID 非空的配置下标。
 *
 * 返回值：
 * 1. 成功时返回 WiFi 配置下标。
 * 2. 不存在可用配置时返回 -1。
 */
static int first_available_wifi_profile(void)
{
    for (size_t index = 0; index < sizeof(s_wifi_profiles) / sizeof(s_wifi_profiles[0]); index += 1) {
        if (s_wifi_profiles[index].ssid != NULL && strlen(s_wifi_profiles[index].ssid) > 0) {
            return (int)index;
        }
    }
    return -1;
}

/**
 * WiFi 和 IP 事件处理函数。
 *
 * 主要逻辑：
 * 1. STA 启动后写入 WiFi 配置并连接。
 * 2. 获取 IP 后设置已连接事件位。
 * 3. 断开时设置失败事件位，保持测试入口简单直接。
 *
 * 参数：
 * 1. `arg`：调用方上下文，本测试未使用。
 * 2. `event_base`：事件类型。
 * 3. `event_id`：事件编号。
 * 4. `event_data`：事件数据。
 */
static void wifi_event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
    (void)arg;
    (void)event_data;

    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        int profile_index = first_available_wifi_profile();
        if (profile_index < 0) {
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAILED_BIT);
            return;
        }
        wifi_config_t wifi_config = {0};
        strlcpy((char *)wifi_config.sta.ssid, s_wifi_profiles[profile_index].ssid, sizeof(wifi_config.sta.ssid));
        strlcpy((char *)wifi_config.sta.password, s_wifi_profiles[profile_index].password, sizeof(wifi_config.sta.password));
        ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
        ESP_LOGI(TAG, "连接 WiFi: ssid=%s", s_wifi_profiles[profile_index].ssid);
        ESP_ERROR_CHECK(esp_wifi_connect());
        return;
    }

    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "WiFi disconnected");
        xEventGroupSetBits(s_wifi_event_group, WIFI_FAILED_BIT);
        return;
    }

    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "WiFi got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

/**
 * 初始化 WiFi。
 *
 * 主要逻辑：
 * 1. 初始化 netif、事件循环和 WiFi STA。
 * 2. 使用 Kconfig 中配置的 WiFi 账号连接网络。
 * 3. 等待获取 IP 或失败。
 *
 * 返回值：
 * 1. `true` 表示 WiFi 已连接。
 * 2. `false` 表示配置缺失或连接失败。
 */
static bool init_wifi(void)
{
    if (first_available_wifi_profile() < 0) {
        ESP_LOGE(TAG, "WiFi SSID 为空，请配置 GLASS_WIFI_PRIMARY_SSID 或 GLASS_WIFI_FALLBACK_SSID");
        return false;
    }

    s_wifi_event_group = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    /*
     * 这条链路同时上行 AEC 后麦克风音频、下行 Omni 播放音频。STA 省电模式会让 TCP 可写窗口
     * 出现明显抖动，播放期间更容易触发 `transport_poll_write`。测试全双工能力时明确关闭省电。
     */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    EventBits_t bits = xEventGroupWaitBits(
        s_wifi_event_group,
        WIFI_CONNECTED_BIT | WIFI_FAILED_BIT,
        pdFALSE,
        pdFALSE,
        pdMS_TO_TICKS(20000)
    );
    return (bits & WIFI_CONNECTED_BIT) != 0;
}

/**
 * 发送测试 hello 消息。
 *
 * 主要逻辑：
 * 1. WebSocket 建立后向 relay 声明当前音频格式。
 * 2. 标记 AEC 来源为 ESP-SR 官方 `esp_aec.h`。
 */
static void send_hello_message(esp_websocket_client_handle_t client, const char *role)
{
    char text[384];
    int len = snprintf(
        text,
        sizeof(text),
        "{\"type\":\"hello\",\"role\":\"%s\",\"device_id\":\"%s\",\"sample_rate\":%d,\"channels\":1,"
        "\"input\":\"esp_sr_aec_output\",\"playback_ref\":\"server_pcm16\",\"aec\":\"esp_sr\","
        "\"aec_mode\":\"%s\",\"filter_length\":%d}",
        role,
        CONFIG_GLASS_DEVICE_ID,
        SR_SAMPLE_RATE_HZ,
        aec_get_mode_string(selected_aec_mode()),
        CONFIG_GLASS_AEC_TEST_FILTER_LENGTH
    );
    if (len > 0 && client != NULL) {
        esp_websocket_client_send_text(client, text, len, pdMS_TO_TICKS(WS_SEND_TIMEOUT_MS));
    }
}

/**
 * 将 AEC 输出编码成 SDK MediaFrame 并投递到发送队列。
 *
 * 主要逻辑：
 * 1. 生成符合 server-python `MediaFrame` 的 4 字节头长度 + JSON 头 + PCM 负载。
 * 2. 使用 `frame_type=mic_audio` 表示这是 ESP-SR AEC 后的麦克风音频。
 * 3. AEC 任务只负责投递，不直接阻塞在 WebSocket 发送锁上。
 *
 * 参数：
 * 1. `pcm`：AEC 输出的 16k 单声道 PCM16。
 * 2. `payload_bytes`：PCM 字节数。
 */
static void send_mic_audio_frame(const int16_t *pcm, size_t payload_bytes)
{
    if (!s_ws_mic_connected || s_ws_mic_client == NULL || s_mic_send_queue == NULL || payload_bytes == 0) {
        return;
    }

    char header[384];
    uint32_t seq = s_seq++;
    int header_len = snprintf(
        header,
        sizeof(header),
        "{\"version\":\"v1\",\"stream_id\":\"esp32_official_aec\",\"frame_type\":\"mic_audio\","
        "\"seq\":%" PRIu32 ",\"ts_ms\":%" PRIu64 ",\"codec\":\"pcm16le\","
        "\"sample_rate\":%d,\"channels\":1,\"payload_size\":%u,\"final\":false,"
        "\"aec\":\"esp_sr\",\"aec_mode\":\"%s\"}",
        seq,
        now_ms(),
        SR_SAMPLE_RATE_HZ,
        (unsigned)payload_bytes,
        aec_get_mode_string(selected_aec_mode())
    );
    if (header_len <= 0 || header_len >= (int)sizeof(header)) {
        ESP_LOGW(TAG, "media frame header too long");
        return;
    }

    size_t frame_size = 4 + (size_t)header_len + payload_bytes;
    uint8_t *frame = heap_caps_malloc(frame_size, MALLOC_CAP_8BIT);
    if (frame == NULL) {
        ESP_LOGW(TAG, "alloc media frame failed: %u bytes", (unsigned)frame_size);
        return;
    }

    frame[0] = (uint8_t)(((uint32_t)header_len >> 24) & 0xff);
    frame[1] = (uint8_t)(((uint32_t)header_len >> 16) & 0xff);
    frame[2] = (uint8_t)(((uint32_t)header_len >> 8) & 0xff);
    frame[3] = (uint8_t)((uint32_t)header_len & 0xff);
    memcpy(frame + 4, header, (size_t)header_len);
    memcpy(frame + 4 + (size_t)header_len, pcm, payload_bytes);

    mic_media_packet_t packet = {
        .data = frame,
        .data_len = (int)frame_size,
        .payload_bytes = payload_bytes,
    };
    if (xQueueSend(s_mic_send_queue, &packet, 0) != pdTRUE) {
        ESP_LOGD(TAG, "mic send queue full, drop one frame");
        free(frame);
    }
}

/**
 * WebSocket 麦克风上行发送任务。
 *
 * 主要逻辑：
 * 1. 从队列中取出 AEC 任务产出的 MediaFrame。
 * 2. 在独立任务中调用 `esp_websocket_client_send_bin`，避免阻塞 AEC 实时处理。
 * 3. 发送失败时释放帧内存并打印 DEBUG 日志。
 *
 * 参数：
 * 1. `arg`：任务上下文，本测试未使用。
 */
static void mic_sender_task(void *arg)
{
    (void)arg;

    mic_media_packet_t packet = {0};
    while (true) {
        if (xQueueReceive(s_mic_send_queue, &packet, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (packet.data == NULL || packet.data_len <= 0) {
            continue;
        }
        if (!s_ws_mic_connected || s_ws_mic_client == NULL || !esp_websocket_client_is_connected(s_ws_mic_client)) {
            s_ws_mic_connected = false;
            free(packet.data);
            continue;
        }
        int ret = esp_websocket_client_send_bin(
            s_ws_mic_client,
            (const char *)packet.data,
            packet.data_len,
            pdMS_TO_TICKS(MIC_SEND_TIMEOUT_MS)
        );
        if (ret > 0) {
            s_sent_mic_bytes += packet.payload_bytes;
        } else {
            ESP_LOGD(TAG, "send mic frame skipped ret=%d", ret);
        }
        free(packet.data);
    }
}

/**
 * 处理 relay 下发的二进制 MediaFrame。
 *
 * 主要逻辑：
 * 1. 解码 4 字节 header_len 和 JSON 帧头。
 * 2. 只接收 `frame_type=playback_audio` 的 16k PCM16 下行音频。
 * 3. 写入扬声器播放缓冲；AEC 参考缓冲由扬声器任务在实际写 I2S 时同步填充。
 *
 * 参数：
 * 1. `data`：WebSocket 二进制数据。
 * 2. `data_len`：数据长度。
 */
static void handle_binary_media_frame(const uint8_t *data, int data_len)
{
    if (data == NULL || data_len < 4) {
        return;
    }

    uint32_t header_len = ((uint32_t)data[0] << 24) | ((uint32_t)data[1] << 16) | ((uint32_t)data[2] << 8) | (uint32_t)data[3];
    if (header_len == 0 || header_len > 1024 || (uint32_t)data_len < 4 + header_len) {
        ESP_LOGW(TAG, "invalid media frame header_len=%" PRIu32 " data_len=%d", header_len, data_len);
        return;
    }

    char *header_text = heap_caps_malloc(header_len + 1, MALLOC_CAP_8BIT);
    if (header_text == NULL) {
        return;
    }
    memcpy(header_text, data + 4, header_len);
    header_text[header_len] = '\0';

    cJSON *header = cJSON_Parse(header_text);
    free(header_text);
    if (header == NULL) {
        ESP_LOGW(TAG, "media frame header json parse failed");
        return;
    }

    cJSON *frame_type = cJSON_GetObjectItemCaseSensitive(header, "frame_type");
    cJSON *codec = cJSON_GetObjectItemCaseSensitive(header, "codec");
    if (!cJSON_IsString(frame_type) || strcmp(frame_type->valuestring, "playback_audio") != 0) {
        cJSON_Delete(header);
        return;
    }
    if (cJSON_IsString(codec) && strcmp(codec->valuestring, "pcm16le") != 0) {
        ESP_LOGW(TAG, "unsupported playback codec=%s", codec->valuestring);
        cJSON_Delete(header);
        return;
    }

    const uint8_t *payload = data + 4 + header_len;
    size_t payload_bytes = (size_t)data_len - 4 - (size_t)header_len;
    size_t sample_count = payload_bytes / sizeof(int16_t);
    if (sample_count == 0) {
        cJSON_Delete(header);
        return;
    }

    pcm_ring_write(&s_playback_ring, (const int16_t *)payload, sample_count);
    s_received_playback_bytes += sample_count * sizeof(int16_t);
    s_playback_active = true;
    cJSON_Delete(header);
}

/**
 * 清理 WebSocket 二进制消息重组缓冲。
 *
 * 主要逻辑：
 * 1. 释放当前未完成的二进制消息缓冲。
 * 2. 重置期望长度，避免断线或异常分片污染下一条消息。
 */
static void reset_ws_binary_rx_buffer(void)
{
    if (s_ws_binary_rx_buffer != NULL) {
        free(s_ws_binary_rx_buffer);
        s_ws_binary_rx_buffer = NULL;
    }
    s_ws_binary_rx_expected_len = 0;
}

/**
 * 处理可能被 ESP-IDF WebSocket 拆分的二进制消息。
 *
 * 主要逻辑：
 * 1. 使用 `payload_len` 和 `payload_offset` 判断当前事件是否为完整消息的一部分。
 * 2. 单片完整消息直接进入 MediaFrame 解码，减少一次内存拷贝。
 * 3. 多片消息按偏移重组，收齐后再交给 `handle_binary_media_frame`。
 *
 * 参数：
 * 1. `data`：ESP-IDF WebSocket 事件数据。
 */
static void handle_websocket_binary_event(const esp_websocket_event_data_t *data)
{
    if (data == NULL || data->data_ptr == NULL || data->data_len <= 0) {
        return;
    }

    int payload_len = data->payload_len > 0 ? data->payload_len : data->data_len;
    int payload_offset = data->payload_offset;
    if (payload_len <= 0 || payload_len > WS_BINARY_RX_MAX_BYTES || payload_offset < 0 ||
        payload_offset + data->data_len > payload_len) {
        ESP_LOGW(
            TAG,
            "invalid websocket binary fragment payload_len=%d offset=%d data_len=%d",
            payload_len,
            payload_offset,
            data->data_len
        );
        reset_ws_binary_rx_buffer();
        return;
    }

    if (payload_offset == 0 && data->data_len == payload_len) {
        handle_binary_media_frame((const uint8_t *)data->data_ptr, data->data_len);
        return;
    }

    if (payload_offset == 0) {
        reset_ws_binary_rx_buffer();
        s_ws_binary_rx_buffer = heap_caps_malloc((size_t)payload_len, MALLOC_CAP_8BIT);
        if (s_ws_binary_rx_buffer == NULL) {
            ESP_LOGW(TAG, "alloc websocket binary rx buffer failed: %d bytes", payload_len);
            return;
        }
        s_ws_binary_rx_expected_len = payload_len;
    }

    if (s_ws_binary_rx_buffer == NULL || s_ws_binary_rx_expected_len != payload_len) {
        ESP_LOGW(
            TAG,
            "websocket binary fragment lost sync expected=%d payload_len=%d offset=%d data_len=%d",
            s_ws_binary_rx_expected_len,
            payload_len,
            payload_offset,
            data->data_len
        );
        reset_ws_binary_rx_buffer();
        return;
    }

    memcpy(s_ws_binary_rx_buffer + payload_offset, data->data_ptr, (size_t)data->data_len);
    if (payload_offset + data->data_len == payload_len) {
        handle_binary_media_frame(s_ws_binary_rx_buffer, payload_len);
        reset_ws_binary_rx_buffer();
    }
}

/**
 * 处理 relay 下发的文本控制消息。
 *
 * 主要逻辑：
 * 1. 识别 `playback_end` 和 `playback_cancelled`。
 * 2. 更新播放状态，便于日志观察。
 *
 * 参数：
 * 1. `text`：文本消息。
 * 2. `text_len`：文本长度。
 */
static void handle_text_message(const char *text, int text_len)
{
    if (text == NULL || text_len <= 0) {
        return;
    }
    char *copy = heap_caps_malloc((size_t)text_len + 1, MALLOC_CAP_8BIT);
    if (copy == NULL) {
        return;
    }
    memcpy(copy, text, (size_t)text_len);
    copy[text_len] = '\0';

    cJSON *root = cJSON_Parse(copy);
    free(copy);
    if (root == NULL) {
        return;
    }

    cJSON *type = cJSON_GetObjectItemCaseSensitive(root, "type");
    if (cJSON_IsString(type)) {
        if (strcmp(type->valuestring, "playback_end") == 0) {
            s_playback_active = false;
        } else if (strcmp(type->valuestring, "playback_cancelled") == 0) {
            s_playback_active = false;
            pcm_ring_clear(&s_playback_ring);
            pcm_ring_clear(&s_ref_ring);
            ESP_LOGI(TAG, "playback cancelled: local playback/ref buffers cleared");
        }
        ESP_LOGD(TAG, "relay text event: %s", type->valuestring);
    }
    cJSON_Delete(root);
}

/**
 * WebSocket 事件处理函数。
 *
 * 主要逻辑：
 * 1. 连接成功后发送 hello。
 * 2. 收到二进制消息时解析下行播放帧。
 * 3. 收到文本消息时更新播放状态。
 *
 * 参数：
 * 1. `handler_args`：调用方上下文，本测试未使用。
 * 2. `base`：事件类型。
 * 3. `event_id`：事件编号。
 * 4. `event_data`：事件数据。
 */
static void websocket_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data)
{
    (void)base;

    const char *role = (const char *)handler_args;
    bool is_playback = role != NULL && strcmp(role, "playback") == 0;
    esp_websocket_event_data_t *data = (esp_websocket_event_data_t *)event_data;
    if (event_id == WEBSOCKET_EVENT_CONNECTED) {
        if (is_playback) {
            s_ws_playback_connected = true;
        } else {
            s_ws_mic_connected = true;
        }
        ESP_LOGI(TAG, "relay websocket connected role=%s", is_playback ? "playback" : "mic");
        send_hello_message(is_playback ? s_ws_playback_client : s_ws_mic_client, is_playback ? "playback" : "mic");
        return;
    }
    if (event_id == WEBSOCKET_EVENT_DISCONNECTED) {
        if (is_playback) {
            s_ws_playback_connected = false;
            s_playback_active = false;
            reset_ws_binary_rx_buffer();
        } else {
            s_ws_mic_connected = false;
        }
        ESP_LOGW(TAG, "relay websocket disconnected role=%s", is_playback ? "playback" : "mic");
        return;
    }
    if (event_id == WEBSOCKET_EVENT_DATA && data != NULL && data->data_ptr != NULL) {
        if (!is_playback) {
            return;
        }
        if (data->op_code == 0x2) {
            handle_websocket_binary_event(data);
        } else if (data->op_code == 0x1) {
            handle_text_message(data->data_ptr, data->data_len);
        }
        return;
    }
    if (event_id == WEBSOCKET_EVENT_ERROR) {
        if (is_playback) {
            s_ws_playback_connected = false;
        } else {
            s_ws_mic_connected = false;
        }
        ESP_LOGE(TAG, "relay websocket error role=%s", is_playback ? "playback" : "mic");
    }
}

/**
 * 拼接带角色参数的 relay WebSocket URI。
 *
 * 主要逻辑：
 * 1. 在基础 URI 后追加 `role` 查询参数。
 * 2. 如果基础 URI 已经包含查询参数，则使用 `&` 继续追加。
 *
 * 参数：
 * 1. `output`：输出缓冲。
 * 2. `output_size`：输出缓冲长度。
 * 3. `role`：连接角色，当前为 `mic` 或 `playback`。
 *
 * 返回值：
 * 1. `ESP_OK` 表示 URI 拼接成功。
 * 2. `ESP_ERR_INVALID_SIZE` 表示输出缓冲不足。
 */
static esp_err_t build_role_uri(char *output, size_t output_size, const char *role)
{
    const char *separator = strchr(CONFIG_GLASS_AEC_TEST_RELAY_WS_URI, '?') == NULL ? "?" : "&";
    int len = snprintf(output, output_size, "%s%srole=%s", CONFIG_GLASS_AEC_TEST_RELAY_WS_URI, separator, role);
    ESP_RETURN_ON_FALSE(len > 0 && (size_t)len < output_size, ESP_ERR_INVALID_SIZE, TAG, "relay role uri too long");
    return ESP_OK;
}

/**
 * 启动连接 Python Omni relay 的 WebSocket。
 *
 * 主要逻辑：
 * 1. 使用 Kconfig 中的 `GLASS_AEC_TEST_RELAY_WS_URI` 创建客户端。
 * 2. 注册事件处理函数。
 * 3. 启动自动重连的 WebSocket 任务。
 *
 * 返回值：
 * 1. `ESP_OK` 表示启动成功。
 * 2. 其他错误码表示客户端创建或启动失败。
 */
static esp_err_t start_relay_websocket(void)
{
    static char mic_uri[256];
    static char playback_uri[256];
    ESP_RETURN_ON_ERROR(build_role_uri(mic_uri, sizeof(mic_uri), "mic"), TAG, "build mic uri failed");
    ESP_RETURN_ON_ERROR(build_role_uri(playback_uri, sizeof(playback_uri), "playback"), TAG, "build playback uri failed");

    esp_websocket_client_config_t mic_config = {
        .uri = mic_uri,
        .buffer_size = 32768,
        .network_timeout_ms = 5000,
        .reconnect_timeout_ms = 3000,
        .task_stack = 8192,
    };
    esp_websocket_client_config_t playback_config = {
        .uri = playback_uri,
        .buffer_size = 32768,
        .network_timeout_ms = 5000,
        .reconnect_timeout_ms = 3000,
        .task_stack = 8192,
    };
    s_ws_mic_client = esp_websocket_client_init(&mic_config);
    ESP_RETURN_ON_FALSE(s_ws_mic_client != NULL, ESP_FAIL, TAG, "create relay mic websocket failed");
    s_ws_playback_client = esp_websocket_client_init(&playback_config);
    ESP_RETURN_ON_FALSE(s_ws_playback_client != NULL, ESP_FAIL, TAG, "create relay playback websocket failed");
    ESP_ERROR_CHECK(esp_websocket_register_events(s_ws_mic_client, WEBSOCKET_EVENT_ANY, websocket_event_handler, "mic"));
    ESP_ERROR_CHECK(esp_websocket_register_events(s_ws_playback_client, WEBSOCKET_EVENT_ANY, websocket_event_handler, "playback"));
    ESP_RETURN_ON_ERROR(esp_websocket_client_start(s_ws_mic_client), TAG, "start relay mic websocket failed");

    /*
     * ESP-IDF 的 WebSocket client 在同一时间启动两个到同一服务端的连接时，偶发出现
     * CONNECTED 事件已经到达、但发送路径仍判定不可写的状态。先让 mic 上行连接完成握手，
     * 再启动 playback 下行连接，避免启动阶段两个 client 的握手和重连状态互相干扰。
     */
    vTaskDelay(pdMS_TO_TICKS(800));
    ESP_RETURN_ON_ERROR(esp_websocket_client_start(s_ws_playback_client), TAG, "start relay playback websocket failed");
    ESP_LOGI(TAG, "relay websocket started: mic=%s playback=%s", mic_uri, playback_uri);
    return ESP_OK;
}

/**
 * 扬声器播放任务。
 *
 * 主要逻辑：
 * 1. 从播放缓冲读取 relay 下发的 PCM。
 * 2. 转换为当前扬声器 I2S 所需格式。
 * 3. 写入 I2S 扬声器，并把同一帧音频写入 AEC 参考缓冲。
 *
 * 参数：
 * 1. `arg`：任务上下文，本测试未使用。
 */
static void speaker_task(void *arg)
{
    (void)arg;

    int16_t *mono = heap_caps_calloc(AUDIO_FRAME_SAMPLES, sizeof(int16_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    int32_t *stereo32 = heap_caps_calloc(AUDIO_FRAME_SAMPLES * 2, sizeof(int32_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    int16_t *stereo16 = heap_caps_calloc(AUDIO_FRAME_SAMPLES * 2, sizeof(int16_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (mono == NULL || stereo32 == NULL || stereo16 == NULL) {
        ESP_LOGE(TAG, "alloc speaker task buffers failed");
        free(mono);
        free(stereo32);
        free(stereo16);
        vTaskDelete(NULL);
        return;
    }

    bool was_playback_active = false;
    int fade_in_remaining = 0;

    while (true) {
        size_t got = pcm_ring_read(&s_playback_ring, mono, AUDIO_FRAME_SAMPLES, false);
        if (got == 0) {
            if (!s_playback_active) {
                was_playback_active = false;
                vTaskDelay(pdMS_TO_TICKS(10));
                continue;
            }
            memset(mono, 0, AUDIO_FRAME_SAMPLES * sizeof(int16_t));
        } else if (got < AUDIO_FRAME_SAMPLES) {
            memset(mono + got, 0, (AUDIO_FRAME_SAMPLES - got) * sizeof(int16_t));
        }
        if (got == 0 && !s_playback_active) {
            was_playback_active = false;
            continue;
        }
        if (got > 0 && !was_playback_active) {
            fade_in_remaining = (SR_SAMPLE_RATE_HZ * PLAYBACK_FADE_IN_MS) / 1000;
            was_playback_active = true;
        }
        if (got > 0) {
            apply_playback_fade_in(mono, AUDIO_FRAME_SAMPLES, &fade_in_remaining);
        }
        const uint8_t *write_data = NULL;
        size_t write_size = 0;
#if CONFIG_GLASS_AEC_TEST_SPK_FORMAT_16BIT_STEREO
        mono16_to_stereo16(mono, AUDIO_FRAME_SAMPLES, stereo16);
        write_data = (const uint8_t *)stereo16;
        write_size = AUDIO_FRAME_SAMPLES * 2 * sizeof(int16_t);
#elif CONFIG_GLASS_AEC_TEST_SPK_FORMAT_32BIT_MSB_RAW
        mono16_to_stereo32_raw(mono, AUDIO_FRAME_SAMPLES, stereo32);
        write_data = (const uint8_t *)stereo32;
        write_size = AUDIO_FRAME_SAMPLES * 2 * sizeof(int32_t);
#else
        mono16_to_stereo32_msb(mono, AUDIO_FRAME_SAMPLES, stereo32);
        write_data = (const uint8_t *)stereo32;
        write_size = AUDIO_FRAME_SAMPLES * 2 * sizeof(int32_t);
#endif
        size_t written_total = 0;
        while (written_total < write_size) {
            size_t bytes_written = 0;
            esp_err_t ret = i2s_channel_write(
                s_spk_tx_chan,
                write_data + written_total,
                write_size - written_total,
                &bytes_written,
                pdMS_TO_TICKS(1000)
            );
            if (ret != ESP_OK) {
                ESP_LOGD(TAG, "speaker write failed: %s", esp_err_to_name(ret));
                break;
            }
            if (bytes_written == 0) {
                break;
            }
            written_total += bytes_written;
        }
        if (written_total > 0 && !CONFIG_GLASS_AEC_TEST_LOCAL_TONE_ONLY) {
            pcm_ring_write(&s_ref_ring, mono, AUDIO_FRAME_SAMPLES);
        }
    }
}

/**
 * 本地扬声器纯音诊断任务。
 *
 * 主要逻辑：
 * 1. 不依赖 Omni 下行音频，按固定频率生成 16k 单声道 PCM16 正弦波。
 * 2. 写入和 Omni 播放完全相同的播放环形缓冲，复用同一个 `speaker_task` 和 I2S 输出格式。
 * 3. 每轮播放 1 秒纯音、停 1 秒，用来判断噪声是否来自 ESP32 扬声器链路本身。
 *
 * 参数：
 * 1. `arg`：任务上下文，本测试未使用。
 */
static void local_tone_task(void *arg)
{
    (void)arg;

    int16_t mono[AUDIO_FRAME_SAMPLES] = {0};
    float phase = 0.0f;
    float step = (float)(2.0 * M_PI * (double)CONFIG_GLASS_AEC_TEST_LOCAL_TONE_HZ / (double)SR_SAMPLE_RATE_HZ);
    const int frames_per_tone = LOCAL_TONE_ON_MS / ((AUDIO_FRAME_SAMPLES * 1000) / SR_SAMPLE_RATE_HZ);
    const int fade_samples = (SR_SAMPLE_RATE_HZ * LOCAL_TONE_FADE_MS) / 1000;
    const TickType_t frame_delay = pdMS_TO_TICKS((AUDIO_FRAME_SAMPLES * 1000) / SR_SAMPLE_RATE_HZ);

    ESP_LOGI(TAG, "local speaker tone started: hz=%d amplitude=%.0f", CONFIG_GLASS_AEC_TEST_LOCAL_TONE_HZ, LOCAL_TONE_AMPLITUDE);
    while (true) {
        s_playback_active = true;
        for (int frame = 0; frame < frames_per_tone; frame += 1) {
            for (size_t index = 0; index < AUDIO_FRAME_SAMPLES; index += 1) {
                int absolute_index = (frame * AUDIO_FRAME_SAMPLES) + (int)index;
                int remain_index = (frames_per_tone * AUDIO_FRAME_SAMPLES) - absolute_index;
                float envelope = 1.0f;
                if (absolute_index < fade_samples) {
                    envelope = (float)absolute_index / (float)fade_samples;
                } else if (remain_index < fade_samples) {
                    envelope = (float)remain_index / (float)fade_samples;
                }
                mono[index] = (int16_t)(sinf(phase) * LOCAL_TONE_AMPLITUDE * envelope);
                phase += step;
                if (phase >= (float)(2.0 * M_PI)) {
                    phase -= (float)(2.0 * M_PI);
                }
            }
            pcm_ring_write(&s_playback_ring, mono, AUDIO_FRAME_SAMPLES);
            s_received_playback_bytes += AUDIO_FRAME_SAMPLES * sizeof(int16_t);
            vTaskDelay(frame_delay);
        }
        s_playback_active = false;
        vTaskDelay(pdMS_TO_TICKS(LOCAL_TONE_OFF_MS));
    }
}

/**
 * ESP-SR 官方 AEC 处理任务。
 *
 * 主要逻辑：
 * 1. 从 PDM 麦克风读取一帧近端音频。
 * 2. 从参考缓冲读取同长度播放参考，不足时补静音。
 * 3. 调用官方 `aec_process(mic, ref, out)`。
 * 4. 把 AEC 输出以 MediaFrame 发送到 relay。
 *
 * 参数：
 * 1. `arg`：任务上下文，实际类型为 `aec_handle_t *`。
 */
static void aec_task(void *arg)
{
    aec_handle_t *aec = (aec_handle_t *)arg;
    int chunk_samples = aec_get_chunksize(aec);
    size_t frame_bytes = (size_t)chunk_samples * sizeof(int16_t);

    int16_t *mic = alloc_aligned_i16((size_t)chunk_samples);
    int16_t *ref = alloc_aligned_i16((size_t)chunk_samples);
    int16_t *out = alloc_aligned_i16((size_t)chunk_samples);
    if (mic == NULL || ref == NULL || out == NULL) {
        ESP_LOGE(TAG, "alloc AEC buffers failed");
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(
        TAG,
        "ESP-SR AEC started: chunk_samples=%d frame_ms=%d mode=%s filter_length=%d",
        chunk_samples,
        (chunk_samples * 1000) / SR_SAMPLE_RATE_HZ,
        aec_get_mode_string(selected_aec_mode()),
        CONFIG_GLASS_AEC_TEST_FILTER_LENGTH
    );

    while (true) {
        size_t bytes_read = 0;
        esp_err_t ret = i2s_channel_read(s_mic_rx_chan, mic, frame_bytes, &bytes_read, pdMS_TO_TICKS(1000));
        if (ret != ESP_OK || bytes_read == 0) {
            ESP_LOGD(TAG, "mic read failed: ret=%s bytes=%u", esp_err_to_name(ret), (unsigned)bytes_read);
            continue;
        }
        if (bytes_read < frame_bytes) {
            memset(((uint8_t *)mic) + bytes_read, 0, frame_bytes - bytes_read);
        }

        pcm_ring_read(&s_ref_ring, ref, (size_t)chunk_samples, true);
        aec_process(aec, mic, ref, out);
        remove_dc_offset_i16(out, (size_t)chunk_samples);
        send_mic_audio_frame(out, frame_bytes);
    }
}

/**
 * 周期性打印测试链路状态。
 *
 * 主要逻辑：
 * 1. 每 3 秒输出 WebSocket、播放缓冲、参考缓冲和字节统计。
 * 2. 用于判断是否出现播放积压、参考丢弃或上行中断。
 *
 * 参数：
 * 1. `arg`：任务上下文，本测试未使用。
 */
static void stats_task(void *arg)
{
    (void)arg;

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(3000));
        ESP_LOGI(
            TAG,
            "stats: mic_ws=%d playback_ws=%d playback_active=%d mic_sent=%" PRIu64 " playback_recv=%" PRIu64
            " playback_ring=%u ref_ring=%u playback_drop=%" PRIu32 " ref_drop=%" PRIu32,
            s_ws_mic_connected,
            s_ws_playback_connected,
            s_playback_active,
            s_sent_mic_bytes,
            s_received_playback_bytes,
            (unsigned)pcm_ring_count(&s_playback_ring),
            (unsigned)pcm_ring_count(&s_ref_ring),
            s_playback_ring.dropped_samples,
            s_ref_ring.dropped_samples
        );
    }
}

/**
 * 官方 ESP-SR AEC 测试入口。
 *
 * 主要逻辑：
 * 1. 初始化 NVS、WiFi、麦克风、扬声器和双环形缓冲。
 * 2. 创建官方 `esp_aec.h` AEC 实例。
 * 3. 连接 Python Omni relay。
 * 4. 启动播放、AEC 上行和统计任务。
 */
void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_LOGI(TAG, "官方 ESP-SR AEC 测试入口启动");
    ESP_LOGI(
        TAG,
        "relay=%s gain=%d filter_length=%d local_tone_only=%d",
        CONFIG_GLASS_AEC_TEST_RELAY_WS_URI,
        CONFIG_GLASS_AEC_TEST_PLAYBACK_GAIN_PERMILLE,
        CONFIG_GLASS_AEC_TEST_FILTER_LENGTH,
        CONFIG_GLASS_AEC_TEST_LOCAL_TONE_ONLY
    );

    ESP_ERROR_CHECK(pcm_ring_init(&s_playback_ring, RING_CAPACITY_SAMPLES));
    ESP_ERROR_CHECK(pcm_ring_init(&s_ref_ring, RING_CAPACITY_SAMPLES));
    s_mic_send_queue = xQueueCreate(MIC_SEND_QUEUE_LENGTH, sizeof(mic_media_packet_t));
    ESP_ERROR_CHECK(s_mic_send_queue != NULL ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(init_speaker_i2s());

    if (CONFIG_GLASS_AEC_TEST_LOCAL_TONE_ONLY) {
        ESP_LOGW(TAG, "本次只运行本地扬声器纯音诊断，不连接 WiFi/Omni relay");
        ESP_ERROR_CHECK(create_checked_task(speaker_task, "aec_speaker", 4096, NULL, 2, tskNO_AFFINITY));
        ESP_ERROR_CHECK(create_checked_task(local_tone_task, "local_tone", 4096, NULL, 3, tskNO_AFFINITY));
        ESP_ERROR_CHECK(create_checked_task(stats_task, "aec_stats", 4096, NULL, 3, tskNO_AFFINITY));
        return;
    }

    ESP_ERROR_CHECK(init_mic_i2s());

    aec_mode_t mode = selected_aec_mode();
    aec_handle_t *aec = aec_create(
        SR_SAMPLE_RATE_HZ,
        CONFIG_GLASS_AEC_TEST_FILTER_LENGTH,
        1,
        mode
    );
    ESP_ERROR_CHECK(aec != NULL ? ESP_OK : ESP_FAIL);

    if (!init_wifi()) {
        ESP_LOGE(TAG, "WiFi 初始化失败，无法连接 relay");
        return;
    }
    ESP_ERROR_CHECK(start_relay_websocket());

    ESP_ERROR_CHECK(create_checked_task(speaker_task, "aec_speaker", 4096, NULL, 2, tskNO_AFFINITY));
    ESP_ERROR_CHECK(create_checked_task(mic_sender_task, "aec_mic_ws", 4096, NULL, 4, tskNO_AFFINITY));
    ESP_ERROR_CHECK(create_checked_task(aec_task, "esp_sr_aec", 8192, aec, 6, 0));
    ESP_ERROR_CHECK(create_checked_task(stats_task, "aec_stats", 4096, NULL, 3, tskNO_AFFINITY));
}
