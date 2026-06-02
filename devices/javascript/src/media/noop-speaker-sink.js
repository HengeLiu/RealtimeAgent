export class NoopSpeakerSink {
  constructor() {
    this.chunks = [];
    this.preparedFormats = [];
    this.cancelCalled = false;
  }

  async prepare(format) {
    this.preparedFormats.push(format);
  }

  async write(chunk) {
    this.chunks.push(chunk);
  }

  async drain() {}

  async cancel() {
    this.cancelCalled = true;
  }
}
