#ifndef GLASS_RESULT_H
#define GLASS_RESULT_H

#include <Arduino.h>

template <typename T>
struct Result {
  bool ok;
  T value;
  String error;
};

#endif

