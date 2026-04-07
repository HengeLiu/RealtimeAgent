#ifndef GLASS_MESSAGE_CODEC_H
#define GLASS_MESSAGE_CODEC_H

#include <Arduino.h>

#include "../messages/envelope.h"

class MessageCodec {
 public:
  String encode(const Envelope& envelope) const { return envelope.payload_json; }

  // 第一阶段骨架：后续接入 JSON 反序列化。
  bool decode(const String& raw_payload, Envelope* out) const {
    if (out == nullptr) {
      return false;
    }
    out->payload_json = raw_payload;
    return true;
  }
};

#endif

