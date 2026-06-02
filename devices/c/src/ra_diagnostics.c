#include "realtime_agent_device/ra_diagnostics.h"

#include <string.h>

static void copy_text(char *dst, size_t capacity, const char *src) {
    if (capacity == 0) {
        return;
    }
    if (src == NULL) {
        src = "";
    }
    strncpy(dst, src, capacity - 1);
    dst[capacity - 1] = '\0';
}

void ra_diagnostics_init(ra_diagnostics_t *diagnostics) {
    if (diagnostics == NULL) {
        return;
    }
    memset(diagnostics, 0, sizeof(*diagnostics));
    copy_text(diagnostics->connection_state, sizeof(diagnostics->connection_state), "idle");
    copy_text(diagnostics->conversation_state, sizeof(diagnostics->conversation_state), "waiting");
}

void ra_diagnostics_set_error(ra_diagnostics_t *diagnostics, const char *error) {
    if (diagnostics == NULL) {
        return;
    }
    copy_text(diagnostics->last_error, sizeof(diagnostics->last_error), error);
}
