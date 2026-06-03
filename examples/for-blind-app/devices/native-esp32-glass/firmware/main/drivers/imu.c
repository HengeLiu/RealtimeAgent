#include "drivers/imu.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/spi_master.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "connectivity/ws_stream.h"
#include <stdio.h>
#include <string.h>

static const char *TAG = "imu";

// IMU SPI pins - configurable via build config
#ifndef IMU_SPI_SCK
#define IMU_SPI_SCK   1
#endif
#ifndef IMU_SPI_MOSI
#define IMU_SPI_MOSI  2
#endif
#ifndef IMU_SPI_MISO
#define IMU_SPI_MISO  3
#endif
#ifndef IMU_SPI_CS
#define IMU_SPI_CS    4
#endif

#define REG_WHO_AM_I     0x75
#define REG_BANK_SEL     0x76
#define REG_PWR_MGMT0    0x4E
#define REG_TEMP_H       0x1D
#define BURST_FIRST      REG_TEMP_H
#define BURST_COUNT      14

static const float ACC_LSB_PER_G   = 2048.0f;
static const float GYR_LSB_PER_DPS = 16.4f;
static const float G               = 9.80665f;
static const float TEMP_SENS       = 132.48f;
static const float TEMP_OFFSET     = 25.0f;

static spi_host_device_t s_spi_host = SPI2_HOST;
static bool s_initialized = false;
static bool s_reporting = false;

static float s_ax_f = 0, s_ay_f = 0, s_az_f = 0;
static bool s_ema_inited = false;
static const float EMA_ALPHA = 0.20f;

static spi_device_handle_t s_spi_dev = NULL;
static TaskHandle_t s_imu_task = NULL;

static inline void imu_cs_low(void)  { gpio_set_level(IMU_SPI_CS, 0); }
static inline void imu_cs_high(void) { gpio_set_level(IMU_SPI_CS, 1); }

static uint8_t imu_read8(uint8_t reg) {
    uint8_t tx_buf[2] = { reg | 0x80, 0x00 };
    uint8_t rx_buf[2] = { 0 };
    spi_transaction_t t = {
        .tx_buffer = tx_buf,
        .rx_buffer = rx_buf,
        .length = 2 * 8,
    };
    imu_cs_low();
    spi_device_transmit(s_spi_dev, &t);
    imu_cs_high();
    return rx_buf[1];
}

static void imu_write8(uint8_t reg, uint8_t val) {
    uint8_t tx_buf[2] = { reg & 0x7F, val };
    spi_transaction_t t = {
        .tx_buffer = tx_buf,
        .length = 2 * 8,
    };
    imu_cs_low();
    spi_device_transmit(s_spi_dev, &t);
    imu_cs_high();
}

static void imu_readn(uint8_t start_reg, uint8_t *dst, size_t n) {
    for (size_t i = 0; i < n; i++) {
        uint8_t tx_buf[2] = { (start_reg + i) | 0x80, 0x00 };
        uint8_t rx_buf[2] = { 0 };
        spi_transaction_t t = {
            .tx_buffer = tx_buf,
            .rx_buffer = rx_buf,
            .length = 2 * 8,
        };
        imu_cs_low();
        spi_device_transmit(s_spi_dev, &t);
        imu_cs_high();
        dst[i] = rx_buf[1];
    }
}

esp_err_t imu_init(void) {
    gpio_config_t io_conf = {
        .pin_bit_mask = (1ULL << IMU_SPI_CS),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
    };
    gpio_config(&io_conf);
    imu_cs_high();

    spi_bus_config_t bus_cfg = {
        .mosi_io_num = IMU_SPI_MOSI,
        .miso_io_num = IMU_SPI_MISO,
        .sclk_io_num = IMU_SPI_SCK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 256,
    };

    esp_err_t ret = spi_bus_initialize(s_spi_host, &bus_cfg, SPI_DMA_CH_AUTO);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "SPI bus init failed: %d", ret);
        return ret;
    }

    spi_device_interface_config_t dev_cfg = {
        .command_bits = 0,
        .address_bits = 0,
        .dummy_bits = 0,
        .cs_ena_pretrans = 0,
        .cs_ena_posttrans = 0,
        .clock_speed_hz = 1000000,
        .mode = 0,
        .spics_io_num = IMU_SPI_CS,
        .queue_size = 1,
    };

    ret = spi_bus_add_device(s_spi_host, &dev_cfg, &s_spi_dev);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "SPI device add failed: %d", ret);
        return ret;
    }

    vTaskDelay(pdMS_TO_TICKS(5));

    uint8_t who = imu_read8(REG_WHO_AM_I);
    ESP_LOGI(TAG, "WHO_AM_I=0x%02X (expect 0x47)", who);
    if (who != 0x47) {
        ESP_LOGE(TAG, "IMU not found, continuing anyway");
        // Don't fail init - IMU is optional
    }

    imu_write8(REG_PWR_MGMT0, 0x0F);
    vTaskDelay(pdMS_TO_TICKS(10));

    s_initialized = true;
    ESP_LOGI(TAG, "IMU init OK (SPI SCK=%d MOSI=%d MISO=%d CS=%d)",
            IMU_SPI_SCK, IMU_SPI_MOSI, IMU_SPI_MISO, IMU_SPI_CS);
    return ESP_OK;
}

esp_err_t imu_read(imu_data_t *data) {
    if (!s_initialized) {
        return ESP_ERR_INVALID_STATE;
    }

    uint8_t raw[BURST_COUNT];
    imu_readn(BURST_FIRST, raw, sizeof(raw));

    int16_t tr  = (int16_t)((raw[0] << 8) | raw[1]);
    int16_t axr = (int16_t)((raw[2] << 8) | raw[3]);
    int16_t ayr = (int16_t)((raw[4] << 8) | raw[5]);
    int16_t azr = (int16_t)((raw[6] << 8) | raw[7]);
    int16_t gxr = (int16_t)((raw[8] << 8) | raw[9]);
    int16_t gyr = (int16_t)((raw[10] << 8) | raw[11]);
    int16_t gzr = (int16_t)((raw[12] << 8) | raw[13]);

    data->temp_c = (float)tr / TEMP_SENS + TEMP_OFFSET;
    data->accel_x = ((float)axr / ACC_LSB_PER_G) * G;
    data->accel_y = ((float)ayr / ACC_LSB_PER_G) * G;
    data->accel_z = ((float)azr / ACC_LSB_PER_G) * G;
    data->gyro_x = (float)gxr / GYR_LSB_PER_DPS;
    data->gyro_y = (float)gyr / GYR_LSB_PER_DPS;
    data->gyro_z = (float)gzr / GYR_LSB_PER_DPS;

    // EMA smoothing for accel
    if (!s_ema_inited) {
        s_ax_f = data->accel_x;
        s_ay_f = data->accel_y;
        s_az_f = data->accel_z;
        s_ema_inited = true;
    } else {
        s_ax_f = EMA_ALPHA * data->accel_x + (1 - EMA_ALPHA) * s_ax_f;
        s_ay_f = EMA_ALPHA * data->accel_y + (1 - EMA_ALPHA) * s_ay_f;
        s_az_f = EMA_ALPHA * data->accel_z + (1 - EMA_ALPHA) * s_az_f;
    }

    data->accel_x = s_ax_f;
    data->accel_y = s_ay_f;
    data->accel_z = s_az_f;

    return ESP_OK;
}

static void imu_report_task(void *pvParameters) {
    (void)pvParameters;
    imu_data_t data;
    char buf[256];

    for (;;) {
        if (!s_reporting) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }

        if (imu_read(&data) == ESP_OK) {
            int n = snprintf(buf, sizeof(buf),
                "{\"ts\":%lu,\"temp_c\":%.2f,"
                "\"accel\":{\"x\":%.3f,\"y\":%.3f,\"z\":%.3f},"
                "\"gyro\":{\"x\":%.3f,\"y\":%.3f,\"z\":%.3f}}",
                (unsigned long)(esp_timer_get_time() / 1000),
                data.temp_c,
                data.accel_x, data.accel_y, data.accel_z,
                data.gyro_x, data.gyro_y, data.gyro_z);

            if (n > 0) {
                ws_stream_send_imu(buf, n);
            }
        }

        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

esp_err_t imu_start_reporting(const char *udp_host, uint16_t udp_port) {
    (void)udp_host;
    (void)udp_port;
    if (!s_initialized) return ESP_ERR_INVALID_STATE;
    if (s_reporting) return ESP_OK;

    s_reporting = true;

    xTaskCreatePinnedToCore(&imu_report_task, "imu_loop", 2048, NULL, 2, &s_imu_task, 0);

    ESP_LOGI(TAG, "IMU reporting started via WebSocket");
    return ESP_OK;
}

esp_err_t imu_stop_reporting(void) {
    s_reporting = false;
    if (s_imu_task) {
        vTaskDelete(s_imu_task);
        s_imu_task = NULL;
    }
    return ESP_OK;
}