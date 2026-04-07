#ifndef GLASS_MESSAGE_TYPE_H
#define GLASS_MESSAGE_TYPE_H

enum class MessageType {
  kCommand,
  kEvent,
  kState,
  kStream,
  kAck,
  kError,
};

#endif

