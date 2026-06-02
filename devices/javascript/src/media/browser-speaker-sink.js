import {pcm16ToFloat, resampleLinear} from "../pcm.js";

export class BrowserSpeakerSink {
  constructor({audioContext = null} = {}) {
    this.audioContext = audioContext;
    this.node = null;
    this.workletReady = null;
    this.currentStreamId = null;
    this.drainResolvers = new Map();
    this.underrunEvents = 0;
  }

  async prepare(format) {
    this.format = format;
    await this.ensurePlaybackWorklet();
  }

  async write(chunk) {
    const context = await this.ensureAudioContext();
    const node = await this.ensurePlaybackWorklet();
    this.currentStreamId = chunk.streamId;
    const samples = resampleLinear(pcm16ToFloat(chunk.payload), chunk.sampleRate, context.sampleRate);
    node.port.postMessage({type: "push", streamId: chunk.streamId, samples}, [samples.buffer]);
  }

  async drain() {
    if (!this.currentStreamId || !this.node) return;
    const streamId = this.currentStreamId;
    const promise = new Promise((resolve) => {
      this.drainResolvers.set(streamId, resolve);
    });
    this.node.port.postMessage({type: "finish", streamId});
    await promise;
  }

  async cancel() {
    this.node?.port.postMessage({type: "stopAll"});
    for (const resolve of this.drainResolvers.values()) resolve();
    this.drainResolvers.clear();
    this.currentStreamId = null;
  }

  async ensureAudioContext() {
    if (!this.audioContext) {
      this.audioContext = new AudioContext();
    }
    if (this.audioContext.state !== "running") {
      await this.audioContext.resume();
    }
    return this.audioContext;
  }

  async ensurePlaybackWorklet() {
    const context = await this.ensureAudioContext();
    if (this.node) return this.node;
    if (!context.audioWorklet) {
      throw new Error("当前浏览器不支持 AudioWorklet");
    }
    if (!this.workletReady) {
      const code = playbackWorkletCode();
      const url = URL.createObjectURL(new Blob([code], {type: "application/javascript"}));
      this.workletReady = context.audioWorklet.addModule(url).finally(() => URL.revokeObjectURL(url));
    }
    await this.workletReady;
    this.node = new AudioWorkletNode(context, "realtime-agent-pcm-playback", {
      numberOfInputs: 0,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    this.node.port.onmessage = (event) => {
      const message = event.data ?? {};
      if (message.type === "drained") {
        this.drainResolvers.get(message.streamId)?.();
        this.drainResolvers.delete(message.streamId);
      }
      if (message.type === "underrun") {
        this.underrunEvents = message.events;
      }
    };
    this.node.connect(context.destination);
    return this.node;
  }
}

function playbackWorkletCode() {
  return `
    class RealtimeAgentPcmPlayback extends AudioWorkletProcessor {
      constructor() {
        super();
        this.queue = [];
        this.current = null;
        this.finished = new Set();
        this.openStreams = new Set();
        this.underrunFrames = 0;
        this.underrunEvents = 0;
        this.wasUnderrun = false;
        this.port.onmessage = (event) => this.handleMessage(event.data || {});
      }
      handleMessage(message) {
        if (message.type === "push") {
          const samples = message.samples instanceof Float32Array ? message.samples : new Float32Array(message.samples || 0);
          this.queue.push({streamId: message.streamId, samples, offset: 0});
          this.openStreams.add(message.streamId);
          return;
        }
        if (message.type === "finish") {
          this.finished.add(message.streamId);
          this.maybeNotifyDrained(message.streamId);
          return;
        }
        if (message.type === "stopAll") {
          this.queue = [];
          this.current = null;
          this.finished.clear();
          this.openStreams.clear();
          this.port.postMessage({type: "drained", streamId: "*"});
        }
      }
      hasQueuedStream(streamId) {
        if (this.current && this.current.streamId === streamId) return true;
        return this.queue.some((item) => item.streamId === streamId);
      }
      maybeNotifyDrained(streamId) {
        if (!this.finished.has(streamId) || this.hasQueuedStream(streamId)) return;
        this.finished.delete(streamId);
        this.openStreams.delete(streamId);
        this.port.postMessage({type: "drained", streamId});
      }
      nextSample() {
        while (!this.current || this.current.offset >= this.current.samples.length) {
          const finishedStreamId = this.current ? this.current.streamId : null;
          this.current = this.queue.shift() || null;
          if (finishedStreamId) this.maybeNotifyDrained(finishedStreamId);
          if (!this.current) return 0;
        }
        return this.current.samples[this.current.offset++] || 0;
      }
      process(_inputs, outputs) {
        const output = outputs[0] && outputs[0][0];
        if (!output) return true;
        for (let index = 0; index < output.length; index += 1) {
          const hadData = this.current || this.queue.length > 0;
          output[index] = this.nextSample();
          if (!hadData && this.openStreams.size > 0) {
            this.underrunFrames += 1;
            if (!this.wasUnderrun) {
              this.underrunEvents += 1;
              this.wasUnderrun = true;
              this.port.postMessage({type: "underrun", frames: this.underrunFrames, events: this.underrunEvents});
            }
          } else if (hadData) {
            this.wasUnderrun = false;
          }
        }
        if (this.current && this.current.offset >= this.current.samples.length) {
          const streamId = this.current.streamId;
          this.current = null;
          this.maybeNotifyDrained(streamId);
        }
        return true;
      }
    }
    registerProcessor("realtime-agent-pcm-playback", RealtimeAgentPcmPlayback);
  `;
}
