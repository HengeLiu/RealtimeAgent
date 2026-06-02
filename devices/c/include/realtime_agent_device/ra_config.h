#ifndef REALTIME_AGENT_DEVICE_RA_CONFIG_H
#define REALTIME_AGENT_DEVICE_RA_CONFIG_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define RA_PROTOCOL_VERSION "realtime-agent.v1"
#define RA_DEFAULT_AUDIO_CODEC "pcm16le"
#define RA_DEFAULT_AUDIO_SAMPLE_RATE 16000
#define RA_DEFAULT_AUDIO_CHANNELS 1
#define RA_DEFAULT_AUDIO_CHUNK_MS 20

#define RA_MAX_ID_LEN 96
#define RA_MAX_NAME_LEN 128
#define RA_MAX_URL_LEN 256
#define RA_MAX_EVENT_NAME_LEN 96
#define RA_MAX_STREAM_TYPE_LEN 64
#define RA_MAX_CODEC_LEN 32
#define RA_MAX_ERROR_LEN 160

#ifdef __cplusplus
}
#endif

#endif
