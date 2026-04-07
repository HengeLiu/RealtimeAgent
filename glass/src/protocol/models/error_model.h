#ifndef GLASS_ERROR_MODEL_H
#define GLASS_ERROR_MODEL_H

#include <Arduino.h>

struct ErrorModel {
  String error_code;
  String error_message;
  String error_type;
  String source;
  bool retryable = false;
  String details_json;
};

#endif

