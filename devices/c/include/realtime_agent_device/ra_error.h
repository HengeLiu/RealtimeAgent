#ifndef REALTIME_AGENT_DEVICE_RA_ERROR_H
#define REALTIME_AGENT_DEVICE_RA_ERROR_H

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    RA_OK = 0,
    RA_ERROR_INVALID_ARGUMENT = -1,
    RA_ERROR_BUFFER_TOO_SMALL = -2,
    RA_ERROR_PARSE_FAILED = -3,
    RA_ERROR_NOT_FOUND = -4,
    RA_ERROR_TRANSPORT = -5,
    RA_ERROR_HARDWARE = -6,
    RA_ERROR_STATE = -7,
    RA_ERROR_NO_MEMORY = -8,
} ra_result_t;

#ifdef __cplusplus
}
#endif

#endif
