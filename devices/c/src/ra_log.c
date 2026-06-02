#include "realtime_agent_device/ra_log.h"

#include <stdio.h>

static ra_log_handler_t g_handler = NULL;
static void *g_user_data = NULL;
static ra_log_level_t g_level = RA_LOG_INFO;

void ra_log_set_handler(ra_log_handler_t handler, void *user_data) {
    g_handler = handler;
    g_user_data = user_data;
}

void ra_log_set_level(ra_log_level_t level) {
    g_level = level;
}

const char *ra_log_level_name(ra_log_level_t level) {
    switch (level) {
        case RA_LOG_DEBUG:
            return "DEBUG";
        case RA_LOG_INFO:
            return "INFO";
        case RA_LOG_WARNING:
            return "WARNING";
        case RA_LOG_ERROR:
            return "ERROR";
        case RA_LOG_DISABLED:
            return "DISABLED";
        default:
            return "UNKNOWN";
    }
}

void ra_log(ra_log_level_t level, const char *message) {
    if (level < g_level || level == RA_LOG_DISABLED) {
        return;
    }
    if (g_handler != NULL) {
        g_handler(level, message, g_user_data);
        return;
    }
    fprintf(stderr, "[realtime-agent-device-c][%s] %s\n", ra_log_level_name(level), message == NULL ? "" : message);
}
