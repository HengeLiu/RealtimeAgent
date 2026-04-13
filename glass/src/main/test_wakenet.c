#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "driver/i2s_pdm.h"
#include "esp_afe_sr_iface.h"
#include "esp_afe_sr_models.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "nvs_flash.h"

#define MIC_PDM_CLK_GPIO GPIO_NUM_42
#define MIC_PDM_DATA_GPIO GPIO_NUM_41
#define SR_SAMPLE_RATE_HZ 16000
#define AFE_INPUT_FORMAT "M"
#define WAKE_DEBOUNCE_MS 1500

typedef struct {
    esp_afe_sr_iface_t *afe_handle;
    esp_afe_sr_data_t *afe_data;
    int16_t *feed_buffer;
    size_t feed_buffer_size_bytes;
    int feed_chunksize;
    int feed_nch;
    int feed_chunk_ms;
    uint32_t wake_success_count;
    int debounce_left_ms;
    bool initialized;
    char wake_model_name[64];
} wakenet_test_ctx_t;

static const char *TAG = "test-wakenet";
static i2s_chan_handle_t s_mic_rx_chan = NULL;
static wakenet_test_ctx_t s_ctx = {0};

/**
 * 初始化麦克风 PDM 输入通道。
 *
 * 主要逻辑：
 * 1. 创建 I2S RX 通道。
 * 2. 按 16k 单声道参数初始化 PDM 模式。
 * 3. 使能通道，供 WakeNet 持续读取麦克风数据。
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

    ESP_RETURN_ON_ERROR(
        i2s_channel_init_pdm_rx_mode(s_mic_rx_chan, &pdm_rx_cfg),
        TAG,
        "init mic pdm mode failed"
    );
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_mic_rx_chan), TAG, "enable mic channel failed");

    ESP_LOGI(
        TAG,
        "MIC ready: PDM RX, sr=%d, clk=%d, data=%d",
        SR_SAMPLE_RATE_HZ,
        MIC_PDM_CLK_GPIO,
        MIC_PDM_DATA_GPIO
    );
    return ESP_OK;
}

/**
 * 初始化 WakeNet 运行时。
 *
 * 主要逻辑：
 * 1. 检查 PSRAM，WakeNet 依赖模型与运行缓冲。
 * 2. 初始化麦克风输入。
 * 3. 从模型分区加载语音模型。
 * 4. 创建 AFE 和 WakeNet 运行时上下文。
 * 5. 分配 feed 缓冲，准备主循环读取麦克风。
 *
 * 参数：
 * 1. `ctx`：唤醒测试上下文，成功后会写入运行时句柄和缓冲参数。
 *
 * 返回值：
 * 1. `ESP_OK` 表示初始化成功。
 * 2. 其他错误码表示模型、I2S 或缓冲初始化失败。
 */
static esp_err_t init_wakenet_runtime(wakenet_test_ctx_t *ctx)
{
    size_t psram_size;
    srmodel_list_t *models = NULL;
    afe_config_t *afe_cfg = NULL;
    char *wn_name = NULL;

    psram_size = esp_psram_get_size();
    ESP_RETURN_ON_FALSE(psram_size > 0, ESP_ERR_NOT_FOUND, TAG, "No PSRAM detected");
    ESP_LOGI(TAG, "Detected PSRAM size: %u bytes", (unsigned)psram_size);

    ESP_RETURN_ON_ERROR(init_mic_i2s(), TAG, "init_mic_i2s failed");

    models = esp_srmodel_init("model");
    ESP_RETURN_ON_FALSE(models != NULL, ESP_FAIL, TAG, "esp_srmodel_init(\"model\") failed");

    afe_cfg = afe_config_init(AFE_INPUT_FORMAT, models, AFE_TYPE_SR, AFE_MODE_LOW_COST);
    ESP_RETURN_ON_FALSE(afe_cfg != NULL, ESP_FAIL, TAG, "afe_config_init failed");

    afe_cfg->wakenet_init = true;
    afe_cfg->vad_init = true;
    afe_cfg->aec_init = false;

    wn_name = esp_srmodel_filter(models, ESP_WN_PREFIX, NULL);
    ESP_RETURN_ON_FALSE(wn_name != NULL, ESP_ERR_NOT_FOUND, TAG, "No WakeNet model found");

    afe_cfg->wakenet_model_name = wn_name;
    strlcpy(ctx->wake_model_name, wn_name, sizeof(ctx->wake_model_name));
    ESP_LOGI(TAG, "WakeNet model selected: %s", ctx->wake_model_name);

    ctx->afe_handle = esp_afe_handle_from_config(afe_cfg);
    ESP_RETURN_ON_FALSE(ctx->afe_handle != NULL, ESP_FAIL, TAG, "esp_afe_handle_from_config failed");

    ctx->afe_data = ctx->afe_handle->create_from_config(afe_cfg);
    ESP_RETURN_ON_FALSE(ctx->afe_data != NULL, ESP_FAIL, TAG, "afe create_from_config failed");

    ctx->feed_chunksize = ctx->afe_handle->get_feed_chunksize(ctx->afe_data);
    ctx->feed_nch = ctx->afe_handle->get_feed_channel_num(ctx->afe_data);
    ctx->feed_chunk_ms = (ctx->feed_chunksize * 1000) / SR_SAMPLE_RATE_HZ;
    if (ctx->feed_chunk_ms <= 0) {
        ctx->feed_chunk_ms = 1;
    }

    ctx->feed_buffer_size_bytes = ctx->feed_chunksize * ctx->feed_nch * sizeof(int16_t);
    ctx->feed_buffer = heap_caps_calloc(
        1,
        ctx->feed_buffer_size_bytes,
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT
    );
    if (ctx->feed_buffer == NULL) {
        ctx->feed_buffer = heap_caps_calloc(
            1,
            ctx->feed_buffer_size_bytes,
            MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT
        );
    }
    ESP_RETURN_ON_FALSE(ctx->feed_buffer != NULL, ESP_ERR_NO_MEM, TAG, "feed buffer alloc failed");

    ctx->wake_success_count = 0;
    ctx->debounce_left_ms = 0;
    ctx->initialized = true;
    ESP_LOGI(
        TAG,
        "WakeNet test runtime ready: feed_chunksize=%d feed_nch=%d chunk_ms=%d",
        ctx->feed_chunksize,
        ctx->feed_nch,
        ctx->feed_chunk_ms
    );
    return ESP_OK;
}

/**
 * 运行最小 WakeNet 唤醒测试循环。
 *
 * 主要逻辑：
 * 1. 持续从麦克风读取 PCM 数据。
 * 2. 将数据喂给 AFE/WakeNet。
 * 3. 检查是否触发唤醒。
 * 4. 每次触发后打印“第几次唤醒成功”。
 * 5. 通过短暂防抖时间，避免一次说话被重复计数。
 *
 * 参数：
 * 1. `ctx`：唤醒测试上下文。
 *
 * 异常情况：
 * 1. 麦克风读取失败时打印告警并继续下一轮。
 * 2. AFE fetch 返回空时直接继续，不终止程序。
 */
static void run_wakenet_test_loop(wakenet_test_ctx_t *ctx)
{
    int last_vad_state = -999;
    int last_wakeup_state = -999;

    while (true) {
        size_t bytes_read = 0;
        esp_err_t ret = i2s_channel_read(
            s_mic_rx_chan,
            ctx->feed_buffer,
            ctx->feed_buffer_size_bytes,
            &bytes_read,
            pdMS_TO_TICKS(1000)
        );
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "mic read failed: %s", esp_err_to_name(ret));
            continue;
        }
        if (bytes_read < ctx->feed_buffer_size_bytes) {
            memset((uint8_t *)ctx->feed_buffer + bytes_read, 0, ctx->feed_buffer_size_bytes - bytes_read);
        }

        ctx->afe_handle->feed(ctx->afe_data, ctx->feed_buffer);
        afe_fetch_result_t *res = ctx->afe_handle->fetch(ctx->afe_data);
        if (res == NULL) {
            continue;
        }

        if (res->wakeup_state != last_wakeup_state || res->vad_state != last_vad_state) {
            ESP_LOGI(
                TAG,
                "状态变化: wakeup_state=%d vad_state=%d data_size=%d debounce_left_ms=%d",
                res->wakeup_state,
                res->vad_state,
                res->data_size,
                ctx->debounce_left_ms
            );
            last_wakeup_state = res->wakeup_state;
            last_vad_state = res->vad_state;
        }

        if (ctx->debounce_left_ms > 0) {
            ctx->debounce_left_ms -= ctx->feed_chunk_ms;
            if (ctx->debounce_left_ms < 0) {
                ctx->debounce_left_ms = 0;
            }
        }

        if (res->wakeup_state == WAKENET_DETECTED && ctx->debounce_left_ms == 0) {
            ctx->wake_success_count += 1U;
            ctx->debounce_left_ms = WAKE_DEBOUNCE_MS;
            ESP_LOGI(TAG, "WakeNet 唤醒成功，第 %" PRIu32 " 次", ctx->wake_success_count);
        }
    }
}

/**
 * 独立的 WakeNet 成功率测试入口。
 *
 * 主要逻辑：
 * 1. 初始化 NVS，满足 ESP-IDF 基础运行要求。
 * 2. 初始化 WakeNet 测试运行时。
 * 3. 进入无限循环，持续监听唤醒词。
 *
 * 异常情况：
 * 1. 初始化失败时通过 `ESP_ERROR_CHECK` 直接停止，便于串口定位问题。
 */
void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_LOGI(TAG, "WakeNet success-rate test bootstrapping");
    ESP_ERROR_CHECK(init_wakenet_runtime(&s_ctx));
    ESP_LOGI(TAG, "请持续说唤醒词，串口会打印第几次唤醒成功");
    run_wakenet_test_loop(&s_ctx);
}
