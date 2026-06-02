export class AudioInput {
  constructor({enabled, configuration, source} = {}) {
    this.enabled = Boolean(enabled);
    this.configuration = {
      streamType: "sensor.mic",
      codec: "pcm16le",
      sampleRate: 16000,
      channels: 1,
      chunkMs: 20,
      ...(configuration ?? {}),
    };
    this.source = source ?? null;
  }

  static disabled() {
    return new AudioInput({enabled: false});
  }

  static enabled(options = {}) {
    return new AudioInput({enabled: true, ...options});
  }
}

export class Camera {
  constructor({
    enabled,
    modes = ["single"],
    format = "jpeg",
    frequencyHz = 1,
    sampleCount = 1,
    source = null,
    previewVideoElement = null,
  } = {}) {
    this.enabled = Boolean(enabled);
    this.modes = modes;
    this.format = format;
    this.frequencyHz = frequencyHz;
    this.sampleCount = sampleCount;
    this.source = source;
    this.previewVideoElement = previewVideoElement;
  }

  static disabled() {
    return new Camera({enabled: false});
  }

  static enabled(options = {}) {
    return new Camera({enabled: true, ...options});
  }
}

export class PlaybackBuffer {
  constructor({
    startWatermarkMs = 120,
    lowWatermarkMs = 300,
    highWatermarkMs = 800,
    maxBufferMs = 1200,
  } = {}) {
    this.startWatermarkMs = startWatermarkMs;
    this.lowWatermarkMs = lowWatermarkMs;
    this.highWatermarkMs = highWatermarkMs;
    this.maxBufferMs = maxBufferMs;
  }

  static default() {
    return new PlaybackBuffer();
  }
}

export class Speaker {
  constructor({
    enabled,
    buffer = PlaybackBuffer.default(),
    duplexMode = "full_duplex_server_barge_in",
    sink = null,
  } = {}) {
    this.enabled = Boolean(enabled);
    this.buffer = buffer;
    this.duplexMode = duplexMode;
    this.sink = sink;
  }

  static disabled() {
    return new Speaker({enabled: false});
  }

  static enabled(options = {}) {
    return new Speaker({enabled: true, ...options});
  }
}
