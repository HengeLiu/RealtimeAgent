#ifndef GLASS_CONNECTION_MANAGER_H
#define GLASS_CONNECTION_MANAGER_H

#include <Arduino.h>

#include "connection.h"

class ConnectionManager {
 public:
  ConnectionManager() : connection_(nullptr) {}

  void attach(Connection* connection) { connection_ = connection; }

  bool connect() {
    if (connection_ == nullptr) {
      return false;
    }
    return connection_->open();
  }

  void disconnect() {
    if (connection_ != nullptr) {
      connection_->close();
    }
  }

  bool send(const String& payload) {
    if (connection_ == nullptr) {
      return false;
    }
    return connection_->send(payload);
  }

 private:
  Connection* connection_;
};

#endif

