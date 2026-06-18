import {
  AudioInput,
  Camera,
  PlaybackBuffer,
  Speaker,
} from "/devices/javascript/src/options.js";
import {DeviceClient} from "/devices/javascript/src/device-client.js?v=interrupt-finish-async-20260602";
import {BrowserCameraFrameSource} from "/devices/javascript/src/media/browser-camera-frame-source.js?v=webchat-preview-20260602";

const STORAGE_KEY = "device_app_demo.web_chat";
const SDK_BUILD_ID = "webchat-preview-20260602";
const DEFAULTS = {
  serverUrl: defaultServerUrl(),
  deviceId: "dev-device-demo-web-001",
  userId: "user-device-demo",
};

function defaultServerUrl() {
  const protocol = window.location.protocol === "https:" ? "https:" : "http:";
  const hostname = window.location.hostname || "127.0.0.1";
  return `${protocol}//${hostname}:8765`;
}

const elements = {
  serverUrl: document.getElementById("serverUrl"),
  saveUrlButton: document.getElementById("saveUrlButton"),
  primaryButton: document.getElementById("primaryButton"),
  primaryButtonText: document.getElementById("primaryButtonText"),
  shortStatus: document.getElementById("shortStatus"),
  startView: document.getElementById("startView"),
  conversationView: document.getElementById("conversationView"),
  cameraPreview: document.getElementById("cameraPreview"),
  cameraPlaceholder: document.getElementById("cameraPlaceholder"),
  conversationStatus: document.getElementById("conversationStatus"),
  endButton: document.getElementById("endButton"),
  debugButton: document.getElementById("debugButton"),
  debugPanel: document.getElementById("debugPanel"),
  closeDebugButton: document.getElementById("closeDebugButton"),
  diagnosticsText: document.getElementById("diagnosticsText"),
  logList: document.getElementById("logList"),
  copyDiagnosticsButton: document.getElementById("copyDiagnosticsButton"),
  copyLogsButton: document.getElementById("copyLogsButton"),
  clearLogsButton: document.getElementById("clearLogsButton"),
  fetchLocationButton: document.getElementById("fetchLocationButton"),
};

class WebChatRuntime {
  constructor() {
    this.phase = "idle";
    this.failureStage = null;
    this.client = null;
    this.logs = [];
    this.diagnosticsTimer = null;
    this.settings = this.loadSettings();
    elements.serverUrl.value = this.settings.serverUrl;
    this.render();
  }

  loadSettings() {
    try {
      return {...DEFAULTS, ...JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}")};
    } catch {
      return {...DEFAULTS};
    }
  }

  saveSettings() {
    this.settings.serverUrl = elements.serverUrl.value.trim() || DEFAULTS.serverUrl;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(this.settings));
    this.appendLog(`server url saved ${this.settings.serverUrl}`);
    this.render();
  }

  makeClient() {
    const cameraSource = new BrowserCameraFrameSource({videoElement: elements.cameraPreview});
    const client = new DeviceClient({
      serverUrl: this.settings.serverUrl,
      deviceId: this.settings.deviceId,
      userId: this.settings.userId,
      name: "Device Demo Web Chat",
      clientType: "web-chat",
      audioInput: AudioInput.enabled(),
      camera: Camera.enabled({source: cameraSource}),
      speaker: Speaker.enabled({
        buffer: PlaybackBuffer.default(),
        duplexMode: "full_duplex_server_barge_in",
      }),
      auth: {mode: "disabled"},
      properties: {
        "demo.name": "device_app_demo",
        "demo.interaction": "audio_video_conversation",
        "realtime_agent.location": true,
        "realtime_agent.location_commands": ["device.location.get_current"],
      },
      logLevel: "debug",
    });
    this.bindSDKCallbacks(client);
    return client;
  }

  bindSDKCallbacks(client) {
    client.onDebugLog((message) => this.appendLog(`sdk ${message}`));
    client.onConnectionStateChange((state) => this.handleConnectionState(state));
    client.onConversationStateChange((state) => this.handleConversationState(state));
    client.onCustomCommand("demo.ping", async (context) => {
      this.appendLog("custom command <- demo.ping");
      await context.emit("custom.demo.pong", {ok: true});
    });
    client.onEvent("custom.demo.message", async (event) => {
      this.appendLog(`custom event <- ${event.event_name}`);
    });
  }

  async handlePrimaryButtonTap() {
    if (this.phase === "waiting") {
      await this.startConversation();
      return;
    }
    if (this.phase === "failed") {
      await this.retry();
      return;
    }
    if (this.phase === "idle") {
      await this.bootstrap({startAfterRegister: true});
    }
  }

  async bootstrap({startAfterRegister = false} = {}) {
    this.failureStage = null;
    this.phase = "requestingPermissions";
    this.render();
    this.appendLog("bootstrap web chat");
    this.appendLog(`sdk build ${SDK_BUILD_ID}`);
    try {
      await this.client?.close({force: true});
      this.client = this.makeClient();
      const permissions = await this.client.requestPermissions();
      if (!permissions.isAuthorized) {
        throw new Error(`硬件权限未授权 mic=${permissions.microphone} camera=${permissions.camera}`);
      }
      this.phase = "registering";
      this.render();
      await this.client.register();
      this.phase = "waiting";
      this.startDiagnosticsLoop();
      this.appendLog("device registered and permissions granted");
      this.render();
      if (startAfterRegister) {
        await this.startConversation();
      }
    } catch (error) {
      this.fail("registration", error, "bootstrap failed");
    }
  }

  async startConversation() {
    if (!this.client) {
      await this.bootstrap({startAfterRegister: true});
      return;
    }
    this.phase = "conversationStarting";
    this.failureStage = null;
    this.render();
    try {
      await this.client.startConversation({reason: "web_chat_start_button"});
      this.appendLog("sdk startConversation sent");
    } catch (error) {
      this.fail("startConversation", error, "start failed");
    }
  }

  async stopConversation() {
    if (!this.client) return;
    this.phase = "closing";
    this.render();
    try {
      await this.client.requestConversationClose({reason: "user_tapped_end"});
      this.appendLog("sdk requestConversationClose sent");
    } catch (error) {
      this.fail("closeConversation", error, "close request failed");
    }
  }

  async retry() {
    if (this.failureStage === "startConversation" && this.client) {
      this.phase = "waiting";
      this.render();
      await this.startConversation();
      return;
    }
    await this.bootstrap({startAfterRegister: true});
  }

  handleConnectionState(state) {
    this.refreshDiagnostics();
    if (state.state === "registered") {
      if (!["conversationStarting", "conversation", "closing"].includes(this.phase)) {
        this.phase = "waiting";
      }
      this.appendLog("sdk connection registered");
    } else if (state.state === "disconnected") {
      this.failureStage = "disconnected";
      this.phase = "failed";
      this.appendLog(`sdk connection disconnected ${state.reason?.type ?? ""} ${state.reason?.message ?? ""}`);
    } else {
      this.appendLog(`sdk connection ${state.state}`);
    }
    this.render();
  }

  handleConversationState(state) {
    if (this.failureStage === "disconnected") return;
    if (state === "waiting") {
      this.phase = "waiting";
    } else if (state === "starting") {
      this.phase = "conversationStarting";
    } else if (state === "active") {
      this.phase = "conversation";
    } else if (state === "closing") {
      this.phase = "closing";
    }
    this.appendLog(`sdk conversation ${state}`);
    this.render();
  }

  fail(stage, error, prefix) {
    this.failureStage = stage;
    this.phase = "failed";
    this.appendLog(`${prefix}: ${error.message}`);
    this.render();
  }

  primaryTitle() {
    if (this.phase === "idle" || this.phase === "waiting") return "开始<br>音视频对话";
    if (this.phase === "requestingPermissions") return "授权中";
    if (this.phase === "registering") return "注册中";
    if (this.phase === "conversationStarting") return "连接中";
    if (this.phase === "closing") return "结束中";
    if (this.phase === "failed") {
      if (this.failureStage === "disconnected") return "连接断开<br>重连";
      if (this.failureStage === "startConversation") return "开始失败<br>重试";
      return "注册失败<br>重试";
    }
    return this.phase;
  }

  render() {
    elements.primaryButtonText.innerHTML = this.primaryTitle();
    elements.primaryButton.disabled = !["idle", "waiting", "failed"].includes(this.phase);
    elements.shortStatus.textContent = this.statusText();
    const inConversation = ["conversation", "closing"].includes(this.phase);
    elements.startView.hidden = inConversation;
    elements.conversationView.hidden = !inConversation;
    elements.conversationStatus.textContent = this.conversationText();
    elements.endButton.disabled = this.phase === "closing";
    elements.cameraPlaceholder.hidden = Boolean(elements.cameraPreview.srcObject);
    this.refreshDiagnostics();
  }

  statusText() {
    if (this.phase === "idle") return "等待开始";
    if (this.phase === "requestingPermissions") return "正在申请浏览器麦克风、相机和播放权限";
    if (this.phase === "registering") return "正在注册设备";
    if (this.phase === "waiting") return "设备已就绪";
    if (this.phase === "conversationStarting") return "正在请求实时对话";
    if (this.phase === "failed") return "出现错误，请打开调试信息查看";
    return this.phase;
  }

  conversationText() {
    if (this.phase === "closing") return "正在结束";
    if (this.client?.diagnosticsSnapshot().receivedOutputChunks > 0) return "助手回复中";
    return "对话中";
  }

  startDiagnosticsLoop() {
    if (this.diagnosticsTimer) clearInterval(this.diagnosticsTimer);
    this.diagnosticsTimer = setInterval(() => this.refreshDiagnostics(), 500);
  }

  refreshDiagnostics() {
    const diagnostics = this.client?.diagnosticsSnapshot() ?? {};
    elements.diagnosticsText.textContent = JSON.stringify({
      phase: this.phase,
      serverUrl: this.settings.serverUrl,
      deviceId: this.settings.deviceId,
      userId: this.settings.userId,
      diagnostics,
    }, null, 2);
  }

  appendLog(message) {
    const line = `${new Date().toLocaleTimeString("zh-CN", {hour12: false})}.${String(Date.now() % 1000).padStart(3, "0")} ${message}`;
    this.logs.unshift(line);
    this.logs = this.logs.slice(0, 200);
    elements.logList.innerHTML = "";
    for (const item of this.logs) {
      const li = document.createElement("li");
      li.textContent = item;
      elements.logList.appendChild(li);
    }
  }

  clearLogs() {
    this.logs = [];
    elements.logList.innerHTML = "";
    this.appendLog("logs cleared");
  }

  async logDeviceLocation() {
    if (this.locationBusy) {
      this.appendLog("location read ignored (in progress)");
      return;
    }
    if (!navigator.geolocation) {
      this.appendLog("GPS read failed: 浏览器不支持 geolocation");
      return;
    }
    if (!window.isSecureContext) {
      this.appendLog("GPS read warning: 非安全上下文(需 https 或 localhost)，浏览器可能拒绝定位");
    }
    this.locationBusy = true;
    this.appendLog("read gps location ...");
    try {
      const position = await new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: true,
          timeout: 10000,
          maximumAge: 30000,
        });
      });
      const c = position.coords;
      const alt = c.altitude == null ? "-" : `${c.altitude.toFixed(1)}m`;
      const speed = c.speed == null ? "-" : `${c.speed.toFixed(1)}m/s`;
      const heading = c.heading == null ? "-" : `${Math.round(c.heading)}°`;
      this.appendLog(
        `GPS lat=${c.latitude.toFixed(6)} lng=${c.longitude.toFixed(6)} ` +
          `acc=${c.accuracy.toFixed(1)}m alt=${alt} speed=${speed} heading=${heading} (WGS-84)`,
      );
    } catch (error) {
      const code = error && typeof error.code === "number" ? ` code=${error.code}` : "";
      this.appendLog(`GPS read failed: ${error?.message || error}${code}`);
    } finally {
      this.locationBusy = false;
    }
  }

  async copyLogs() {
    await navigator.clipboard.writeText(this.logs.slice().reverse().join("\n"));
    this.appendLog("logs copied");
  }
}

const runtime = new WebChatRuntime();

elements.primaryButton.addEventListener("click", () => {
  void runtime.handlePrimaryButtonTap();
});
elements.endButton.addEventListener("click", () => {
  void runtime.stopConversation();
});
elements.saveUrlButton.addEventListener("click", () => runtime.saveSettings());
elements.debugButton.addEventListener("click", () => {
  elements.debugPanel.classList.add("is-open");
  runtime.refreshDiagnostics();
});
elements.closeDebugButton.addEventListener("click", () => {
  elements.debugPanel.classList.remove("is-open");
});
elements.copyDiagnosticsButton.addEventListener("click", async () => {
  await navigator.clipboard.writeText(elements.diagnosticsText.textContent);
  runtime.appendLog("diagnostics copied");
});
elements.copyLogsButton.addEventListener("click", () => {
  void runtime.copyLogs();
});
elements.clearLogsButton.addEventListener("click", () => runtime.clearLogs());
elements.fetchLocationButton.addEventListener("click", () => {
  void runtime.logDeviceLocation();
});
window.addEventListener("beforeunload", () => {
  void runtime.client?.close({force: true});
});
