#ifndef REALTIME_AGENT_DEVICE_RA_LOG_H
#define REALTIME_AGENT_DEVICE_RA_LOG_H

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    RA_LOG_DEBUG = 0,
    RA_LOG_INFO = 1,
    RA_LOG_WARNING = 2,
    RA_LOG_ERROR = 3,
    RA_LOG_DISABLED = 4,
} ra_log_level_t;

typedef void (*ra_log_handler_t)(ra_log_level_t level, const char *message, void *user_data);

void ra_log_set_handler(ra_log_handler_t handler, void *user_data);
void ra_log_set_level(ra_log_level_t level);
void ra_log(ra_log_level_t level, const char *message);
const char *ra_log_level_name(ra_log_level_t level);

#ifdef __cplusplus
}
#endif

#endif
