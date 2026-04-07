#ifndef GLASS_SERVER_CLIENT_H
#define GLASS_SERVER_CLIENT_H

#include <Arduino.h>

enum class ServerConnectionState {
  kOffline,
  kConnecting,
  kRegistering,
  kOnline,
  kDegraded,
  kReconnecting,
  kError,
};

class ServerClient {
 public:
  ServerClient();

  bool connect();
  void disconnect();
  bool isConnected() const;

  void tick(unsigned long now_ms);

  void registerDevice(const String& device_id, const String& auth_token);
  void sendHeartbeat(int battery_level);

  void markHeartbeatAck();
  void handleInboundMessage(const String& message_name);

  ServerConnectionState state() const;

 private:
  void setState(ServerConnectionState next);

  ServerConnectionState state_;
  String device_id_;
  String auth_token_;
  unsigned long last_heartbeat_ms_;
  unsigned long last_heartbeat_ack_ms_;
  unsigned long heartbeat_interval_ms_;
  unsigned long heartbeat_timeout_ms_;
};

#endif
