#ifndef ACTUATOR_ADAPTER_H
#define ACTUATOR_ADAPTER_H

class ActuatorAdapter {
 public:
  virtual ~ActuatorAdapter() = default;
  virtual void execute() = 0;
};

#endif
