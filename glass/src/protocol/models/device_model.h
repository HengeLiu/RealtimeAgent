#ifndef GLASS_DEVICE_MODEL_H
#define GLASS_DEVICE_MODEL_H

#include <Arduino.h>

struct DeviceModel {
  String device_id;
  String device_type;
  String protocol_version;
  String status;
};

#endif
