#ifndef GLASS_COMMAND_HANDLER_H
#define GLASS_COMMAND_HANDLER_H

#include "../../protocol/messages/envelope.h"

class CommandHandler {
 public:
  void handle(const Envelope& envelope);
};

#endif
