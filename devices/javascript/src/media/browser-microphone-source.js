import {concatBytes, floatToPcm16, resampleLinear} from "../pcm.js";

export class BrowserMicrophoneSource {
  constructor({audioContext = null, constraints = null} = {}) {
    this.audioContext = audioContext;
    this.constraints = constraints ?? {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    };
    this.stream = null;
    this.trackSettings = null;
    this.sourceNode = null;
    this.processorNode = null;
    this.workletReady = null;
    this.pcmBuffer = new Uint8Array(0);
    this.stopRequested = false;
  }

  async requestPermission() {
    await this.ensureMediaStream();
    return {state: "granted", settings: this.trackSettings};
  }

  async start({configuration, onChunk, diagnostics}) {
    this.stopRequested = false;
    this.pcmBuffer = new Uint8Array(0);
    const context = await this.ensureAudioContext();
    await this.ensureMediaStream();
    diagnostics.mic = {
      ...(diagnostics.mic ?? {}),
      trackSettings: this.trackSettings,
      audioContextSampleRate: context.sampleRate,
    };
    const chunkBytes = Math.floor(configuration.sampleRate * configuration.chunkMs / 1000) * 2 * configuration.channels;
    const handleSamples = (samples) => {
      if (this.stopRequested) return;
      const pcm16 = floatToPcm16(resampleLinear(samples, context.sampleRate, configuration.sampleRate));
      this.pcmBuffer = concatBytes(this.pcmBuffer, pcm16);
      while (this.pcmBuffer.byteLength >= chunkBytes) {
        const payload = this.pcmBuffer.slice(0, chunkBytes);
        this.pcmBuffer = this.pcmBuffer.slice(chunkBytes);
        onChunk(payload);
      }
    };

    this.sourceNode = context.createMediaStreamSource(this.stream);
    if (context.audioWorklet) {
      await this.ensureInputWorklet(context);
      this.processorNode = new AudioWorkletNode(context, "realtime-agent-mic-capture", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      });
      this.processorNode.port.onmessage = (event) => {
        if (event.data?.type === "samples") {
          handleSamples(new Float32Array(event.data.samples));
        }
      };
    } else {
      this.processorNode = context.createScriptProcessor(2048, 1, 1);
      this.processorNode.onaudioprocess = (event) => {
        handleSamples(event.inputBuffer.getChannelData(0));
      };
    }
    this.sourceNode.connect(this.processorNode);
    this.processorNode.connect(context.destination);
  }

  async stop() {
    this.stopRequested = true;
    try { this.processorNode?.disconnect(); } catch {}
    try { this.sourceNode?.disconnect(); } catch {}
    this.processorNode = null;
    this.sourceNode = null;
    this.pcmBuffer = new Uint8Array(0);
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
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

  async ensureMediaStream() {
    if (this.stream) return this.stream;
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("当前浏览器不支持 getUserMedia");
    }
    this.stream = await navigator.mediaDevices.getUserMedia({audio: this.constraints});
    const track = this.stream.getAudioTracks()[0];
    this.trackSettings = track?.getSettings?.() ?? {};
    return this.stream;
  }

  async ensureInputWorklet(context) {
    if (!this.workletReady) {
      const code = `
        class RealtimeAgentMicCapture extends AudioWorkletProcessor {
          process(inputs, outputs) {
            const input = inputs[0] && inputs[0][0];
            const output = outputs[0] && outputs[0][0];
            if (output) output.fill(0);
            if (input && input.length) {
              const copy = new Float32Array(input.length);
              copy.set(input);
              this.port.postMessage({type: "samples", samples: copy}, [copy.buffer]);
            }
            return true;
          }
        }
        registerProcessor("realtime-agent-mic-capture", RealtimeAgentMicCapture);
      `;
      const url = URL.createObjectURL(new Blob([code], {type: "application/javascript"}));
      this.workletReady = context.audioWorklet.addModule(url).finally(() => URL.revokeObjectURL(url));
    }
    await this.workletReady;
  }
}
