#ifndef GLASS_CONNECTION_SESSION_H
#define GLASS_CONNECTION_SESSION_H

#include <Arduino.h>

struct ConnectionSession {
  String connection_id;
  String device_id;
  unsigned long last_heartbeat_ms;
};

#endif
