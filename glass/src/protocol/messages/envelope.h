#ifndef GLASS_PROTOCOL_ENVELOPE_H
#define GLASS_PROTOCOL_ENVELOPE_H

#include <Arduino.h>

struct Endpoint {
  String device_id;
  String module;
};

struct Envelope {
  String message_id;
  String trace_id;
  String message_type;
  String message_name;
  String protocol_version;
  Endpoint source;
  Endpoint target;
  String timestamp;
  String payload_json;
};

#endif
