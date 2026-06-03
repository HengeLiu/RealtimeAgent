#ifndef IMU_H
#define IMU_H

#include <stdbool.h>
#include "esp_err.h"

typedef struct {
    float temp_c;
    float accel_x;
    float accel_y;
    float accel_z;
    float gyro_x;
    float gyro_y;
    float gyro_z;
} imu_data_t;

esp_err_t imu_init(void);
esp_err_t imu_read(imu_data_t *data);
esp_err_t imu_start_reporting(const char *udp_host, uint16_t udp_port);
esp_err_t imu_stop_reporting(void);

#endif // IMU_H