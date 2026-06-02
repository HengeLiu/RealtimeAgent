#ifndef REALTIME_AGENT_ESP32_BOARD_CONFIG_H
#define REALTIME_AGENT_ESP32_BOARD_CONFIG_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int pdm_clk;
    int pdm_data;
    int sample_rate;
    int channels;
    int chunk_ms;
} esp32s3_mic_board_config_t;

typedef struct {
    int bclk;
    int lrck;
    int dout;
    int sample_rate;
    bool stereo_32bit_output;
} esp32s3_speaker_board_config_t;

typedef struct {
    int xclk;
    int sccb_sda;
    int sccb_scl;
    int d0;
    int d1;
    int d2;
    int d3;
    int d4;
    int d5;
    int d6;
    int d7;
    int vsync;
    int href;
    int pclk;
    int pwdn;
    int reset;
} esp32s3_camera_board_config_t;

typedef struct {
    esp32s3_mic_board_config_t mic;
    esp32s3_speaker_board_config_t speaker;
    esp32s3_camera_board_config_t camera;
    bool enable_wakenet;
    bool enable_aec;
} esp32s3_board_config_t;

const esp32s3_board_config_t *esp32s3_board_default_config(void);

#ifdef __cplusplus
}
#endif

#endif
