export const StreamChannel = Object.freeze({
  audioInput: "audio_input",
  audioOutput: "audio_output",
  visualInput: "visual_input",
});

export class BrowserWebSocketTransport {
  constructor({WebSocketImpl = globalThis.WebSocket, connectTimeoutMs = 5000} = {}) {
    this.WebSocketImpl = WebSocketImpl;
    this.connectTimeoutMs = connectTimeoutMs;
    this.controlSocket = null;
    this.streamSockets = new Map();
    this.controlQueue = [];
    this.controlWaiters = [];
    this.streamQueues = new Map();
    this.streamWaiters = new Map();
  }

  async connectControl(url) {
    this.controlSocket?.close();
    this.controlSocket = await this.openSocket(url, "control");
  }

  async connectStream(channel, url) {
    this.streamSockets.get(channel)?.close();
    const socket = await this.openSocket(url, channel);
    this.streamSockets.set(channel, socket);
  }

  async sendControl(text) {
    if (!this.controlSocket || this.controlSocket.readyState !== this.WebSocketImpl.OPEN) {
      throw new Error("control websocket not connected");
    }
    this.controlSocket.send(text);
  }

  async receiveControl() {
    if (this.controlQueue.length > 0) return this.controlQueue.shift();
    return new Promise((resolve, reject) => {
      this.controlWaiters.push({resolve, reject});
    });
  }

  async sendStream(data, channel) {
    const socket = this.streamSockets.get(channel);
    if (!socket || socket.readyState !== this.WebSocketImpl.OPEN) {
      throw new Error(`${channel} websocket not connected`);
    }
    socket.send(data);
  }

  async receiveStream(channel) {
    const queue = this.streamQueues.get(channel) ?? [];
    if (queue.length > 0) return queue.shift();
    this.streamQueues.set(channel, queue);
    return new Promise((resolve, reject) => {
      const waiters = this.streamWaiters.get(channel) ?? [];
      waiters.push({resolve, reject});
      this.streamWaiters.set(channel, waiters);
    });
  }

  async close() {
    this.controlSocket?.close();
    for (const socket of this.streamSockets.values()) socket.close();
    this.controlSocket = null;
    this.streamSockets.clear();
  }

  openSocket(url, channel) {
    return new Promise((resolve, reject) => {
      if (!this.WebSocketImpl) {
        reject(new Error("WebSocket is not available in this runtime"));
        return;
      }
      const socket = new this.WebSocketImpl(url);
      let settled = false;
      const finish = (callback, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        callback(value);
      };
      const timeout = setTimeout(() => {
        try { socket.close(); } catch {}
        finish(reject, new Error(`websocket connect timeout after ${this.connectTimeoutMs}ms: ${url}`));
      }, this.connectTimeoutMs);
      if (channel !== "control") socket.binaryType = "arraybuffer";
      socket.onopen = () => finish(resolve, socket);
      socket.onerror = () => finish(reject, new Error(`websocket open failed: ${url}`));
      socket.onmessage = (event) => {
        const value = event.data;
        if (channel === "control") {
          const waiter = this.controlWaiters.shift();
          if (waiter) waiter.resolve(value);
          else this.controlQueue.push(value);
          return;
        }
        const bytesPromise = value instanceof Blob ? value.arrayBuffer() : Promise.resolve(value);
        bytesPromise.then((data) => {
          const waiters = this.streamWaiters.get(channel) ?? [];
          const waiter = waiters.shift();
          if (waiter) waiter.resolve(data);
          else {
            const queue = this.streamQueues.get(channel) ?? [];
            queue.push(data);
            this.streamQueues.set(channel, queue);
          }
        });
      };
      socket.onclose = () => {
        const error = new Error(`${channel} websocket closed`);
        if (!settled) {
          finish(reject, error);
          return;
        }
        if (channel === "control") {
          for (const waiter of this.controlWaiters.splice(0)) waiter.reject(error);
        } else {
          for (const waiter of this.streamWaiters.get(channel) ?? []) waiter.reject(error);
          this.streamWaiters.set(channel, []);
        }
      };
    });
  }
}
