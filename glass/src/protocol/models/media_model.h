#ifndef GLASS_MEDIA_MODEL_H
#define GLASS_MEDIA_MODEL_H

#include <Arduino.h>

struct MediaModel {
  String media_id;
  String media_type;
  String codec;
  String format;
  int sample_rate = 0;
  int channels = 0;
  int width = 0;
  int height = 0;
  int duration_ms = 0;
  int frame_index = 0;
  int chunk_index = 0;
  bool is_final = false;
  String captured_at;
  String payload_ref;
  String metadata_json;
};

#endif

