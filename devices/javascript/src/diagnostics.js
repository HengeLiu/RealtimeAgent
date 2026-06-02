export class DeviceDiagnostics {
  constructor() {
    this.registered = false;
    this.connectionState = "idle";
    this.conversationState = "waiting";
    this.controlState = "idle";
    this.streamState = "idle";
    this.sentEvents = 0;
    this.receivedEvents = 0;
    this.sentStreamChunks = 0;
    this.receivedStreamChunks = 0;
    this.receivedOutputChunks = 0;
    this.lastEventName = "";
    this.lastError = "";
    this.mic = {};
    this.speaker = {};
    this.camera = {};
  }

  snapshot() {
    return JSON.parse(JSON.stringify(this));
  }
}
