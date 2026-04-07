#ifndef GLASS_SESSION_MODEL_H
#define GLASS_SESSION_MODEL_H

#include <Arduino.h>

struct SessionModel {
  String session_id;
  String session_type;
  String status;
  String started_at;
  String ended_at;
  String context_json;
};

#endif

