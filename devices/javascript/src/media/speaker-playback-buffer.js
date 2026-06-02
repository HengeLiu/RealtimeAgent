export class SpeakerPlaybackBuffer {
  constructor({configuration, sink}) {
    this.configuration = configuration;
    this.sink = sink;
    this.bufferedMs = 0;
    this.bufferedBytes = 0;
    this.isPaused = false;
    this.hasStarted = false;
    this.outOfOrderChunks = 0;
    this.duplicateChunks = 0;
    this.pendingChunks = new Map();
    this.nextDrainSeq = null;
    this.previousAppendSeq = null;
    this.hasDrainedAnyChunk = false;
  }

  snapshot() {
    const keys = [...this.pendingChunks.keys()];
    return {
      bufferedMs: this.bufferedMs,
      bufferedBytes: this.bufferedBytes,
      queuedChunks: this.pendingChunks.size,
      nextDrainSeq: this.nextDrainSeq,
      pendingMinSeq: keys.length ? Math.min(...keys) : null,
      pendingMaxSeq: keys.length ? Math.max(...keys) : null,
      outOfOrderChunks: this.outOfOrderChunks,
      duplicateChunks: this.duplicateChunks,
      isPaused: this.isPaused,
      hasStarted: this.hasStarted,
    };
  }

  async append(chunk) {
    if (this.previousAppendSeq !== null && chunk.seq < this.previousAppendSeq) {
      this.outOfOrderChunks += 1;
    }
    this.previousAppendSeq = chunk.seq;

    if (this.nextDrainSeq !== null && chunk.seq < this.nextDrainSeq) {
      this.duplicateChunks += 1;
      return [];
    }
    if (this.pendingChunks.has(chunk.seq)) {
      this.duplicateChunks += 1;
      return [];
    }

    this.pendingChunks.set(chunk.seq, chunk);
    if (this.nextDrainSeq === null) {
      this.nextDrainSeq = chunk.seq;
    } else if (!this.hasDrainedAnyChunk && chunk.seq < this.nextDrainSeq) {
      this.nextDrainSeq = chunk.seq;
    }

    this.bufferedMs += Math.max(chunk.durationMs, 0);
    this.bufferedBytes += chunk.payload.byteLength;
    const actions = [];
    if (!this.hasStarted && this.bufferedMs >= this.configuration.startWatermarkMs) {
      this.hasStarted = true;
      actions.push({type: "started", bufferedMs: this.bufferedMs});
    }
    if (!this.isPaused && this.bufferedMs >= this.configuration.highWatermarkMs) {
      this.isPaused = true;
      actions.push({
        type: "pause",
        bufferedMs: this.bufferedMs,
        highWatermarkMs: this.configuration.highWatermarkMs,
      });
    }
    if (this.bufferedMs > this.configuration.maxBufferMs) {
      actions.push({
        type: "overflow",
        bufferedMs: this.bufferedMs,
        overflowMs: this.bufferedMs - this.configuration.maxBufferMs,
      });
    }
    return actions;
  }

  hasDrainableChunk() {
    if (!this.hasStarted) return false;
    const seq = this.nextDrainSeq ?? Math.min(...this.pendingChunks.keys());
    return Number.isFinite(seq) && this.pendingChunks.has(seq);
  }

  async drainNext() {
    if (!this.hasDrainableChunk()) return [];
    const seq = this.nextDrainSeq ?? Math.min(...this.pendingChunks.keys());
    const chunk = this.pendingChunks.get(seq);
    this.pendingChunks.delete(seq);
    this.nextDrainSeq = seq + 1;
    this.hasDrainedAnyChunk = true;

    await this.sink.write(chunk);

    this.bufferedMs = Math.max(0, this.bufferedMs - Math.max(chunk.durationMs, 0));
    this.bufferedBytes = Math.max(0, this.bufferedBytes - chunk.payload.byteLength);
    if (this.isPaused && this.bufferedMs <= this.configuration.lowWatermarkMs) {
      this.isPaused = false;
      return [{
        type: "resume",
        bufferedMs: this.bufferedMs,
        lowWatermarkMs: this.configuration.lowWatermarkMs,
      }];
    }
    return [];
  }

  async drainAvailable() {
    const actions = [];
    while (this.hasDrainableChunk()) {
      actions.push(...await this.drainNext());
    }
    return actions;
  }

  async drainSink() {
    await this.drainAvailable();
    await this.sink.drain();
  }

  async cancel() {
    this.pendingChunks.clear();
    this.nextDrainSeq = null;
    this.previousAppendSeq = null;
    this.hasDrainedAnyChunk = false;
    this.bufferedMs = 0;
    this.bufferedBytes = 0;
    this.isPaused = false;
    this.hasStarted = false;
    this.outOfOrderChunks = 0;
    this.duplicateChunks = 0;
    await this.sink.cancel();
  }

  hasSeq(seq) {
    if (this.pendingChunks.has(seq)) return true;
    return this.nextDrainSeq !== null && seq < this.nextDrainSeq;
  }
}
