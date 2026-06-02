#include "board/board_config.h"

#include "board/boards/esp32_s3_glass_default.h"

const esp32s3_board_config_t *esp32s3_board_default_config(void) {
    static const esp32s3_board_config_t config = {
        .mic = {
            .pdm_clk = RA_ESP32_S3_MIC_PDM_CLK,
            .pdm_data = RA_ESP32_S3_MIC_PDM_DATA,
            .sample_rate = 16000,
            .channels = 1,
            .chunk_ms = 20,
        },
        .speaker = {
            .bclk = RA_ESP32_S3_SPK_BCLK,
            .lrck = RA_ESP32_S3_SPK_LRCK,
            .dout = RA_ESP32_S3_SPK_DOUT,
            .sample_rate = 16000,
            .stereo_32bit_output = true,
        },
        .camera = {
            .xclk = RA_ESP32_S3_CAM_XCLK,
            .sccb_sda = RA_ESP32_S3_CAM_SIOD,
            .sccb_scl = RA_ESP32_S3_CAM_SIOC,
            .d0 = RA_ESP32_S3_CAM_D0,
            .d1 = RA_ESP32_S3_CAM_D1,
            .d2 = RA_ESP32_S3_CAM_D2,
            .d3 = RA_ESP32_S3_CAM_D3,
            .d4 = RA_ESP32_S3_CAM_D4,
            .d5 = RA_ESP32_S3_CAM_D5,
            .d6 = RA_ESP32_S3_CAM_D6,
            .d7 = RA_ESP32_S3_CAM_D7,
            .vsync = RA_ESP32_S3_CAM_VSYNC,
            .href = RA_ESP32_S3_CAM_HREF,
            .pclk = RA_ESP32_S3_CAM_PCLK,
            .pwdn = RA_ESP32_S3_CAM_PWDN,
            .reset = RA_ESP32_S3_CAM_RESET,
        },
        .enable_wakenet = false,
        .enable_aec = false,
    };
    return &config;
}
