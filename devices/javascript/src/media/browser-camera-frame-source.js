export class BrowserCameraFrameSource {
  constructor({videoElement = null, width = 640, jpegQuality = 0.82} = {}) {
    this.videoElement = videoElement;
    this.width = width;
    this.jpegQuality = jpegQuality;
    this.stream = null;
  }

  async requestPermission() {
    await this.ensureStream();
    return {state: "granted"};
  }

  async readFrame() {
    const stream = await this.ensureStream();
    const video = this.videoElement ?? document.createElement("video");
    if (!video.srcObject) {
      video.srcObject = stream;
      video.muted = true;
      video.playsInline = true;
      await video.play();
    }
    if (!video.videoWidth || !video.videoHeight) {
      await new Promise((resolve) => {
        video.onloadedmetadata = resolve;
      });
    }
    const aspect = video.videoHeight / video.videoWidth || 0.75;
    const canvas = document.createElement("canvas");
    canvas.width = this.width;
    canvas.height = Math.round(this.width * aspect);
    const context = canvas.getContext("2d");
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", this.jpegQuality));
    if (!blob) throw new Error("camera frame encode failed");
    return {
      payload: new Uint8Array(await blob.arrayBuffer()),
      codec: "jpeg",
      sampleRate: 1,
      channels: 1,
      durationMs: 0,
      metadata: {
        width: canvas.width,
        height: canvas.height,
      },
    };
  }

  stop() {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }

  async ensureStream() {
    if (this.stream) return this.stream;
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("当前浏览器不支持摄像头 getUserMedia");
    }
    this.stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: "user"}, audio: false});
    if (this.videoElement) {
      this.videoElement.srcObject = this.stream;
      this.videoElement.muted = true;
      this.videoElement.playsInline = true;
      await this.videoElement.play();
    }
    return this.stream;
  }
}
