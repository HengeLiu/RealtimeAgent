#ifndef GLASS_MESSAGE_ROUTER_H
#define GLASS_MESSAGE_ROUTER_H

#include <Arduino.h>

#include "../../protocol/messages/envelope.h"

using MessageHandler = void (*)(const Envelope&);

class MessageRouter {
 public:
  MessageRouter() : command_handler_(nullptr), event_handler_(nullptr) {}

  void registerCommandHandler(MessageHandler handler) { command_handler_ = handler; }
  void registerEventHandler(MessageHandler handler) { event_handler_ = handler; }

  void route(const Envelope& envelope) const {
    if (envelope.message_type == "command" && command_handler_ != nullptr) {
      command_handler_(envelope);
      return;
    }
    if (event_handler_ != nullptr) {
      event_handler_(envelope);
    }
  }

 private:
  MessageHandler command_handler_;
  MessageHandler event_handler_;
};

#endif

