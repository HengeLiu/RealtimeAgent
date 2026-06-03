#ifndef FEEDBACK_TONE_H
#define FEEDBACK_TONE_H

#include "esp_err.h"

void tone_play_provisioning_start(void);
void tone_play_pairing_success(void);
void tone_play_pairing_error(void);
void tone_play_wifi_connected(void);
void tone_play_startup(void);
void tone_play_beep(int freq_hz, int duration_ms);

#endif // FEEDBACK_TONE_H
