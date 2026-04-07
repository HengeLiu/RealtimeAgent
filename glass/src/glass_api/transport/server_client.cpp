#include "server_client.h"

#include "../../infra/logging/logger.h"

ServerClient::ServerClient()
    : state_(ServerConnectionState::kOffline),
      last_heartbeat_ms_(0),
      last_heartbeat_ack_ms_(0),
      heartbeat_interval_ms_(5000),
      heartbeat_timeout_ms_(15000) {}

bool ServerClient::connect() {
  setState(ServerConnectionState::kConnecting);
  // 第一阶段骨架：实际传输层连接在后续替换。
  setState(ServerConnectionState::kRegistering);
  return true;
}

void ServerClient::disconnect() { setState(ServerConnectionState::kOffline); }

bool ServerClient::isConnected() const { return state_ == ServerConnectionState::kOnline; }

void ServerClient::tick(unsigned long now_ms) {
  if (state_ == ServerConnectionState::kOnline) {
    if (now_ms - last_heartbeat_ms_ >= heartbeat_interval_ms_) {
      sendHeartbeat(-1);
      last_heartbeat_ms_ = now_ms;
    }

    if (last_heartbeat_ack_ms_ > 0 && now_ms - last_heartbeat_ack_ms_ > heartbeat_timeout_ms_) {
      setState(ServerConnectionState::kDegraded);
    }
  }

  if (state_ == ServerConnectionState::kDegraded) {
    setState(ServerConnectionState::kReconnecting);
    connect();
  }
}

void ServerClient::registerDevice(const String& device_id, const String& auth_token) {
  device_id_ = device_id;
  auth_token_ = auth_token;
  setState(ServerConnectionState::kRegistering);
  // 第一阶段骨架：由上层发送 system.register。
}

void ServerClient::sendHeartbeat(int battery_level) {
  (void)battery_level;
  // 第一阶段骨架：由上层发送 system.heartbeat。
  log_event("glass.system.heartbeat");
}

void ServerClient::markHeartbeatAck() {
  last_heartbeat_ack_ms_ = millis();
  if (state_ != ServerConnectionState::kOnline) {
    setState(ServerConnectionState::kOnline);
  }
}

void ServerClient::handleInboundMessage(const String& message_name) {
  if (message_name == "system.registered") {
    setState(ServerConnectionState::kOnline);
    last_heartbeat_ack_ms_ = millis();
    return;
  }
  if (message_name == "system.heartbeat_ack") {
    markHeartbeatAck();
    return;
  }
  if (message_name == "system.error") {
    setState(ServerConnectionState::kError);
  }
}

ServerConnectionState ServerClient::state() const { return state_; }

void ServerClient::setState(ServerConnectionState next) {
  state_ = next;
  switch (next) {
    case ServerConnectionState::kOffline:
      log_event("glass.connection.offline");
      break;
    case ServerConnectionState::kConnecting:
      log_event("glass.connection.connecting");
      break;
    case ServerConnectionState::kRegistering:
      log_event("glass.connection.registering");
      break;
    case ServerConnectionState::kOnline:
      log_event("glass.connection.online");
      break;
    case ServerConnectionState::kDegraded:
      log_event("glass.connection.degraded");
      break;
    case ServerConnectionState::kReconnecting:
      log_event("glass.connection.reconnecting");
      break;
    case ServerConnectionState::kError:
      log_event("glass.connection.error");
      break;
  }
}
