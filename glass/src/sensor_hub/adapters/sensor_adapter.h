#ifndef SENSOR_ADAPTER_H
#define SENSOR_ADAPTER_H

class SensorAdapter {
 public:
  virtual ~SensorAdapter() = default;
  virtual void sample() = 0;
};

#endif
