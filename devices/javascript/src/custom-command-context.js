export class CustomCommandContext {
  constructor({client, event}) {
    this.client = client;
    this.event = event;
    this.payload = event.payload?.payload ?? {};
    this.command = event.payload?.command;
  }

  /**
   * 发送自定义业务事件。
   *
   * 主要逻辑：复用当前 SDK client 的控制通道，避免 App 直接操作 WebSocket。
   * 参数：`eventName` 必须是 `custom.*`，`payload` 为业务结果。
   * 返回值：无。
   * 异常情况：事件名不是 custom 命名空间时抛出错误。
   */
  async emit(eventName, payload = {}) {
    if (!eventName.startsWith("custom.")) {
      throw new Error("custom context only emits custom.* events");
    }
    await this.client.sendEvent(eventName, payload, {
      sessionId: this.event.session_id,
      streamId: this.event.stream_id,
      streamType: this.event.stream_type,
    });
  }
}
