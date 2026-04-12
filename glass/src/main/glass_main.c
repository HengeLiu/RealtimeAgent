#include <inttypes.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/**
 * @brief 眼镜端运行时配置。
 *
 * 主要功能：
 * 1. 统一管理眼镜端主循环的基础参数。
 *
 * 主要属性：
 * 1. idle_interval_ms：待机心跳日志输出间隔，单位毫秒。
 */
typedef struct {
    uint32_t idle_interval_ms;
} glass_runtime_config_t;

static const char *TAG = "glass-main";
static glass_runtime_config_t s_runtime_config = {
    .idle_interval_ms = 3000,
};

/**
 * @brief 打印当前运行配置。
 *
 * 主要功能：
 * 1. 在系统启动阶段输出关键配置，便于串口联调快速确认。
 *
 * 主要逻辑：
 * 1. 读取全局配置对象。
 * 2. 输出待机心跳间隔。
 *
 * 参数：
 * 1. 无。
 *
 * 返回值：
 * 1. 无。
 *
 * 异常情况：
 * 1. 无显式异常；若串口不可用，日志可能不可见但不影响流程。
 */
static void log_runtime_config(void)
{
    ESP_LOGI(TAG, "runtime config: idle_interval_ms=%" PRIu32,
             s_runtime_config.idle_interval_ms);
}

/**
 * @brief 眼镜端待机任务。
 *
 * 主要功能：
 * 1. 提供 Phase A 阶段最小可运行主循环。
 * 2. 通过周期性心跳日志验证任务调度与主循环可用。
 *
 * 主要逻辑：
 * 1. 进入无限循环。
 * 2. 周期输出待机心跳日志。
 * 3. 使用 FreeRTOS 延时让出 CPU。
 *
 * 参数：
 * 1. arg：任务参数，当前未使用。
 *
 * 返回值：
 * 1. 无。
 *
 * 异常情况：
 * 1. 无显式异常；若任务被系统删除，函数会被动结束。
 */
static void glass_idle_task(void *arg)
{
    (void)arg;
    uint32_t beat = 0;

    for (;;) {
        beat += 1;
        ESP_LOGI(TAG, "idle heartbeat: beat=%" PRIu32, beat);
        vTaskDelay(pdMS_TO_TICKS(s_runtime_config.idle_interval_ms));
    }
}

/**
 * @brief ESP-IDF 应用主入口。
 *
 * 主要功能：
 * 1. 初始化 Phase A 眼镜端最小运行态。
 * 2. 创建待机任务并进入持续运行。
 *
 * 主要逻辑：
 * 1. 输出启动日志与配置摘要。
 * 2. 创建 `glass_idle_task` 任务。
 *
 * 参数：
 * 1. 无。
 *
 * 返回值：
 * 1. 无。
 *
 * 异常情况：
 * 1. 任务创建失败时输出错误日志并直接返回，便于上层快速定位问题。
 */
void app_main(void)
{
    ESP_LOGI(TAG, "glass runtime bootstrapping (Phase A)");
    log_runtime_config();

    BaseType_t ret = xTaskCreate(
        glass_idle_task,
        "glass_idle_task",
        4096,
        NULL,
        5,
        NULL
    );

    if (ret != pdPASS) {
        ESP_LOGE(TAG, "failed to create glass_idle_task");
        return;
    }

    ESP_LOGI(TAG, "glass runtime entered idle loop");
}
