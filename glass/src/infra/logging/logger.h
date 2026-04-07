#ifndef GLASS_LOGGER_H
#define GLASS_LOGGER_H

#include <Arduino.h>

inline void log_event(const String& event_name) {
  Serial.println(event_name);
}

#endif
