#ifndef GLASS_CONNECTION_H
#define GLASS_CONNECTION_H

#include <Arduino.h>

class Connection {
 public:
  virtual ~Connection() = default;
  virtual bool open() = 0;
  virtual void close() = 0;
  virtual bool send(const String& payload) = 0;
};

#endif

