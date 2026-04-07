#ifndef GLASS_SERVER_CLIENT_H
#define GLASS_SERVER_CLIENT_H

class ServerClient {
 public:
  bool connect();
  void disconnect();
  bool isConnected() const;
};

#endif
