// ===== all_in_one_merged.ino — XIAO ESP32S3 Sense: Camera + Mic (PDM) + IMU (ICM42688 SPI) =====
// ===== 版本: v2.4-SPIIMU - ICM42688 改为 SPI，避开 I2S 干扰；WAV chunked 播放保持 =====

#include <WiFi.h>
#include <esp_wifi.h>
#include <esp_camera.h>
#include <ArduinoWebsockets.h>
#include "ESP_I2S.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
struct WavFmt;
#include <cstring>      // memcmp
#include <WiFiUdp.h>
#include <WiFiClient.h> 
#include <SPI.h>        // <<< 改成 SPI
using namespace websockets;

// ===== WiFi / Server =====
// 运行前只需要修改这一段：选择网络、填写公网 IP/域名。
struct NetworkProfile {
  const char* label;
  const char* ssid;
  const char* password;
};

static const NetworkProfile WIFI_MODE_HOME = {
  "wifi",
  "shaliyun",
  "aiyanjiushi66"
};

static const NetworkProfile WIFI_MODE_HOTSPOT = {
  "hotspot",
  "fan",
  "xu137227"
};

const NetworkProfile* ACTIVE_NETWORK = &WIFI_MODE_HOTSPOT;
const char* DEVICE_ID = "glasses-001";
const char* SERVER_HOST = "139.224.163.108";
const uint16_t SERVER_PORT = 8000;
const uint16_t SERVER_HTTP_PORT = 8000;
const bool ENABLE_AUDIO_TEST = false;
const bool ENABLE_IMU_UDP = false;
const bool ENABLE_DIRECT_TO_APP = true;
const uint16_t DIRECT_APP_PORT_FALLBACK = 9100;
const bool DIRECT_MODE_PREFERRED = true;

static const char* CAM_WS_PATH = "/ws/glasses";
static const char* AUD_WS_PATH = "/ws/audio";
static const char* HTTP_STREAM_PATH = "/stream.wav";
static const char* DIRECT_WS_PATH = "/ws/direct";

// ===== Camera config =====
#define CAMERA_MODEL_XIAO_ESP32S3
#include "camera_pins.h"

framesize_t g_frame_size = FRAMESIZE_VGA;
#define JPEG_QUALITY  17
#define FB_COUNT      2
volatile int g_target_fps = 0; // 新增：0=不限，>0 则按该FPS限速发送

// 【新增】视频传输性能监控
volatile unsigned long frame_captured_count = 0;  // 采集帧计数
volatile unsigned long frame_sent_count = 0;      // 发送帧计数
volatile unsigned long frame_dropped_count = 0;   // 丢弃帧计数
volatile unsigned long last_stats_time = 0;       // 上次统计时间
volatile unsigned long ws_send_fail_count = 0;    // WebSocket发送失败计数

// ===== Mic (PDM RX) =====
#define I2S_MIC_CLOCK_PIN 42
#define I2S_MIC_DATA_PIN  41
const int SAMPLE_RATE     = 16000; 
const int CHUNK_MS        = 20;
const int BYTES_PER_CHUNK = SAMPLE_RATE * CHUNK_MS / 1000 * 2;
const int AUDIO_QUEUE_DEPTH = 10;

// ===== Speaker (I2S TX → MAX98357A) =====
#define I2S_SPK_BCLK 7
#define I2S_SPK_LRCK 8
#define I2S_SPK_DIN  9
const int TTS_RATE = 16000;

// ===== IMU (ICM42688 over SPI) / UDP =====
// 使用 D0~D3 作为 SPI
#define IMU_SPI_SCK   1   // D0
#define IMU_SPI_MOSI  2   // D1
#define IMU_SPI_MISO  3   // D2
#define IMU_SPI_CS    4   // D3
const char* UDP_HOST  = SERVER_HOST;
const int   UDP_PORT  = 12345;

WiFiUDP udp;

// ===== WS / Queues / I2S =====
WebsocketsClient wsCam;
WebsocketsClient wsAud;
WebsocketsClient wsDirect;
volatile bool cam_ws_ready = false;
volatile bool aud_ws_ready = false;
volatile bool direct_ws_ready = false;
volatile bool snapshot_in_progress = false; // 抓拍期间暂停实时采集
String g_direct_app_host = "";
uint16_t g_direct_app_port = DIRECT_APP_PORT_FALLBACK;
String g_direct_app_path = DIRECT_WS_PATH;
unsigned long g_last_direct_connect_ms = 0;
String g_serial_buffer = "";

typedef camera_fb_t* fb_ptr_t;
QueueHandle_t qFrames;

typedef struct {
  size_t n;
  uint8_t data[BYTES_PER_CHUNK];
} AudioChunk;
QueueHandle_t qAudio;

#define TTS_QUEUE_DEPTH 48
typedef struct { uint16_t n; uint8_t data[2048]; } TTSChunk;
QueueHandle_t qTTS;
volatile bool tts_playing = false;

I2SClass i2sIn;   // PDM RX (Mic)
I2SClass i2sOut;  // STD TX (Speaker)
volatile bool run_audio_stream = false;

String build_status_message() {
  String ip = WiFi.isConnected() ? WiFi.localIP().toString() : String("0.0.0.0");
  return "STATUS:device=" + String(DEVICE_ID) +
         ",net=" + String(ACTIVE_NETWORK->label) +
         ",ip=" + ip +
         ",rssi=" + String(WiFi.RSSI()) +
         ",direct=" + String(direct_ws_ready ? "on" : "off") +
         ",direct_host=" + (g_direct_app_host.length() ? g_direct_app_host : String("none")) +
         ",audio=" + String(ENABLE_AUDIO_TEST ? "on" : "off") +
         ",imu=" + String(ENABLE_IMU_UDP ? "on" : "off");
}

void send_cam_text(const String& msg) {
  if (cam_ws_ready) wsCam.send(msg);
}

bool has_camera_target() {
  return direct_ws_ready || cam_ws_ready;
}

void send_server_only_text(const String& text) {
  if (cam_ws_ready) wsCam.send("ROUTE:SERVER:" + text);
}

void send_app_cloud_text(const String& text) {
  if (cam_ws_ready) wsCam.send("ROUTE:APP_CLOUD:" + text);
}

void send_direct_text(const String& text) {
  if (direct_ws_ready) wsDirect.send(text);
}

void send_text_to_target(const String& target, const String& text) {
  if (target == "app") {
    if (direct_ws_ready) send_direct_text(text);
    else send_app_cloud_text(text);
    return;
  }

  if (target == "server") {
    send_server_only_text(text);
    return;
  }

  if (target == "both") {
    if (direct_ws_ready) send_direct_text(text);
    send_server_only_text(text);
  }
}

bool send_frame_to_preferred_target(const uint8_t* data, size_t len) {
  if (ENABLE_DIRECT_TO_APP && DIRECT_MODE_PREFERRED && direct_ws_ready) {
    bool ok = wsDirect.sendBinary((const char*)data, len);
    if (!ok) {
      Serial.println("[WS-DIRECT] sendBinary failed");
      wsDirect.close();
      direct_ws_ready = false;
    }
    return ok;
  }

  if (cam_ws_ready) {
    bool ok = wsCam.sendBinary((const char*)data, len);
    if (!ok) {
      Serial.println("[WS-CAM] sendBinary failed");
      wsCam.close();
      cam_ws_ready = false;
    }
    return ok;
  }

  if (direct_ws_ready) {
    bool ok = wsDirect.sendBinary((const char*)data, len);
    if (!ok) {
      Serial.println("[WS-DIRECT] sendBinary failed");
      wsDirect.close();
      direct_ws_ready = false;
    }
    return ok;
  }

  return false;
}

void reply_to_source(const String& source, const String& msg) {
  if (source == "direct") {
    send_direct_text(msg);
  } else {
    send_cam_text(msg);
  }
}

void update_direct_endpoint(const String& host, uint16_t port, const String& path) {
  bool changed = host != g_direct_app_host || port != g_direct_app_port || path != g_direct_app_path;
  g_direct_app_host = host;
  g_direct_app_port = port;
  g_direct_app_path = path.length() ? path : String(DIRECT_WS_PATH);
  if (changed && wsDirect.available()) {
    wsDirect.close();
    direct_ws_ready = false;
  }
  Serial.printf("[WS-DIRECT] endpoint=%s:%u%s\n", g_direct_app_host.c_str(), g_direct_app_port, g_direct_app_path.c_str());
}

void handle_text_command(const String& source, String cmd) {
  cmd.trim();

  if (cmd == "PING") {
    reply_to_source(source, "PONG");
    return;
  }

  if (cmd == "GET_STATUS") {
    reply_to_source(source, build_status_message());
    return;
  }

  if (cmd.startsWith("DIRECT:APP_ENDPOINT=")) {
    String rest = cmd.substring(strlen("DIRECT:APP_ENDPOINT="));
    int firstComma = rest.indexOf(',');
    int secondComma = rest.indexOf(',', firstComma + 1);
    if (firstComma > 0 && secondComma > firstComma) {
      String host = rest.substring(0, firstComma);
      uint16_t port = (uint16_t)rest.substring(firstComma + 1, secondComma).toInt();
      String path = rest.substring(secondComma + 1);
      update_direct_endpoint(host, port > 0 ? port : DIRECT_APP_PORT_FALLBACK, path);
      send_server_only_text("Direct endpoint updated to " + host + ":" + String(port));
    }
    return;
  }

  if (cmd == "DIRECT:DISABLE") {
    g_direct_app_host = "";
    if (wsDirect.available()) wsDirect.close();
    direct_ws_ready = false;
    send_server_only_text("Direct endpoint disabled");
    return;
  }

  if (cmd.startsWith("TEXT:")) {
    String text = cmd.substring(strlen("TEXT:"));
    Serial.println("[TEXT-" + source + "] " + text);
    reply_to_source(source, "TEXT_ACK:" + text);
    return;
  }

  if (cmd.startsWith("APP_IMAGE:")) {
    String imageUrl = cmd.substring(strlen("APP_IMAGE:"));
    Serial.println("[APP-IMAGE] " + imageUrl);
    reply_to_source(source, "APP_IMAGE_ACK:" + imageUrl);
    return;
  }

  if (cmd.startsWith("SET:FRAMESIZE=")) {
    String v = cmd.substring(strlen("SET:FRAMESIZE="));
    v.toUpperCase();
    framesize_t fs = g_frame_size;
    if (v == "SVGA") fs = FRAMESIZE_SVGA;
    else if (v == "XGA") fs = FRAMESIZE_XGA;
    else if (v == "VGA") fs = FRAMESIZE_VGA;
    apply_framesize(fs);
    reply_to_source(source, "SET_ACK:FRAMESIZE=" + v);
    return;
  }

  if (cmd.startsWith("SET:QUALITY=")) {
    int q = cmd.substring(strlen("SET:QUALITY=")).toInt();
    q = constrain(q, 5, 40);
    sensor_t* s = esp_camera_sensor_get();
    if (s) s->set_quality(s, q);
    reply_to_source(source, "SET_ACK:QUALITY=" + String(q));
    return;
  }

  if (cmd.startsWith("SET:FPS=")) {
    int f = cmd.substring(strlen("SET:FPS=")).toInt();
    g_target_fps = (f <= 0 ? 0 : constrain(f, 5, 60));
    reply_to_source(source, "SET_ACK:FPS=" + String(g_target_fps));
    return;
  }

  if (cmd == "SNAP:HQ") {
    if (snapshot_in_progress) return;
    snapshot_in_progress = true;
    sensor_t* s = esp_camera_sensor_get();
    framesize_t old_fs = g_frame_size;
    int old_q = JPEG_QUALITY;
    framesize_t target_fs = FRAMESIZE_SXGA;
    if (s) {
      s->set_framesize(s, target_fs);
      s->set_quality(s, 18);
    }
    vTaskDelay(pdMS_TO_TICKS(500));
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb && fb->format == PIXFORMAT_JPEG) {
      if (cam_ws_ready) {
        wsCam.send("SNAP:BEGIN");
        wsCam.sendBinary((const char*)fb->buf, fb->len);
        wsCam.send("SNAP:END");
      }
      if (direct_ws_ready) {
        wsDirect.sendBinary((const char*)fb->buf, fb->len);
      }
      esp_camera_fb_return(fb);
    } else {
      if (fb) esp_camera_fb_return(fb);
    }
    if (s) {
      s->set_framesize(s, old_fs);
      s->set_quality(s, old_q);
    }
    snapshot_in_progress = false;
    return;
  }
}

void process_serial_command(String cmd) {
  cmd.trim();
  if (!cmd.length()) return;

  if (cmd == "help") {
    Serial.println("Commands:");
    Serial.println("  send app <text>");
    Serial.println("  send server <text>");
    Serial.println("  send both <text>");
    Serial.println("  status");
    Serial.println("  direct off");
    return;
  }

  if (cmd == "status") {
    Serial.println(build_status_message());
    send_server_only_text(build_status_message());
    if (direct_ws_ready) send_direct_text(build_status_message());
    return;
  }

  if (cmd == "direct off") {
    g_direct_app_host = "";
    if (wsDirect.available()) wsDirect.close();
    direct_ws_ready = false;
    Serial.println("[WS-DIRECT] manually disabled");
    return;
  }

  if (cmd.startsWith("send app ")) {
    send_text_to_target("app", "SERIAL:" + cmd.substring(strlen("send app ")));
    return;
  }

  if (cmd.startsWith("send server ")) {
    send_text_to_target("server", "SERIAL:" + cmd.substring(strlen("send server ")));
    return;
  }

  if (cmd.startsWith("send both ")) {
    send_text_to_target("both", "SERIAL:" + cmd.substring(strlen("send both ")));
    return;
  }

  Serial.println("[SERIAL] unknown command, type help");
}

void handle_serial_input() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r' || c == '\n') {
      if (g_serial_buffer.length()) {
        process_serial_command(g_serial_buffer);
        g_serial_buffer = "";
      }
    } else {
      g_serial_buffer += c;
    }
  }
}

void try_connect_direct_ws() {
  if (!ENABLE_DIRECT_TO_APP || !g_direct_app_host.length() || direct_ws_ready) return;
  if (millis() - g_last_direct_connect_ms < 2000) return;
  g_last_direct_connect_ms = millis();
  Serial.printf("[WS-DIRECT] connecting to %s:%u%s\n", g_direct_app_host.c_str(), g_direct_app_port, g_direct_app_path.c_str());
  wsDirect.connect(g_direct_app_host.c_str(), g_direct_app_port, g_direct_app_path.c_str());
}

void connect_wifi_with_logging() {
  Serial.printf("[WiFi] mode=%s ssid=%s\n", ACTIVE_NETWORK->label, ACTIVE_NETWORK->ssid);
  WiFi.begin(ACTIVE_NETWORK->ssid, ACTIVE_NETWORK->password);

  int retry = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    retry++;
    if (retry % 20 == 0) {
      Serial.printf("\n[WiFi] still connecting to %s\n", ACTIVE_NETWORK->ssid);
    }
  }

  Serial.printf("\n[WiFi] connected, ip=%s, rssi=%d\n",
                WiFi.localIP().toString().c_str(),
                WiFi.RSSI());
}

// ====================================================================
// Camera
// ====================================================================
bool apply_framesize(framesize_t fs) {
  sensor_t* s = esp_camera_sensor_get();
  if (!s) return false;
  int r = s->set_framesize(s, fs);
  if (r == 0) { g_frame_size = fs; return true; }
  return false;
}

bool init_camera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM; config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM; config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM; config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM; config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM; config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM; config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM; config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn  = PWDN_GPIO_NUM; config.pin_reset = RESET_GPIO_NUM;

  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size   = g_frame_size;
  config.jpeg_quality = JPEG_QUALITY;
  config.fb_count     = FB_COUNT;
  config.fb_location  = CAMERA_FB_IN_PSRAM;
  config.grab_mode    = CAMERA_GRAB_LATEST;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) { Serial.printf("[CAM] init failed: 0x%x\n", err); return false; }

  sensor_t * s = esp_camera_sensor_get();
  if (s) {

    s->set_hmirror(s, 1);  // ★ 新增：水平镜像，与人眼左右一致（1=开，0=关）
    s->set_vflip(s, 0);    // ★ 新增：垂直翻转；若镜头“倒装”，改为 1

    s->set_brightness(s, 0);
    s->set_contrast(s, 1);
    s->set_saturation(s, 1);
    s->set_gain_ctrl(s, 1);
    s->set_exposure_ctrl(s, 0);
    s->set_whitebal(s, 1);
    s->set_awb_gain(s, 1);
    s->set_aec2(s, 0);
    s->set_aec_value(s, 40);
  }
  return true;
}

inline void enqueue_frame(camera_fb_t* fb) {
  if (!fb) return;
  if (xQueueSend(qFrames, &fb, 0) != pdPASS) {
    // 队列满，丢弃最旧的帧
    fb_ptr_t drop = nullptr;
    if (xQueueReceive(qFrames, &drop, 0) == pdPASS) {
      if (drop) {
        esp_camera_fb_return(drop);
        frame_dropped_count++;  // 统计丢帧
      }
    }
    xQueueSend(qFrames, &fb, 0);
  }
}

void taskCamCapture(void*) {
  unsigned long capture_fail_count = 0;
  
  for(;;){
    if (snapshot_in_progress) { vTaskDelay(pdMS_TO_TICKS(5)); continue; }
    
    if (has_camera_target()) {
      camera_fb_t* fb = esp_camera_fb_get();
      if (fb) {
        frame_captured_count++;
        if (fb->format != PIXFORMAT_JPEG) { 
          esp_camera_fb_return(fb);
          capture_fail_count++;
        }
        else { 
          enqueue_frame(fb);
        }
      } else {
        capture_fail_count++;
        vTaskDelay(pdMS_TO_TICKS(2));
      }
    } else {
      vTaskDelay(pdMS_TO_TICKS(20));
    }
  }
}

void taskCamSend(void*) {
  static TickType_t lastTick = 0;
  unsigned long last_sent_time = 0;
  
  for(;;){
    fb_ptr_t fb = nullptr;
    if (xQueueReceive(qFrames, &fb, pdMS_TO_TICKS(100)) == pdPASS) {
      if (fb && has_camera_target()) {
        // 发送节流：若设置了目标FPS，则按周期发，丢弃多余帧由 qFrames 机制承担
        if (g_target_fps > 0) {
          const int period_ms = 1000 / g_target_fps;
          TickType_t now = xTaskGetTickCount();
          int elapsed = (now - lastTick) * portTICK_PERIOD_MS;
          if (elapsed < period_ms) vTaskDelay(pdMS_TO_TICKS(period_ms - elapsed));
          lastTick = xTaskGetTickCount();
        }
        
        unsigned long send_start = millis();
        bool ok = send_frame_to_preferred_target(fb->buf, fb->len);
        unsigned long send_time = millis() - send_start;
        
        if (ok) {
          frame_sent_count++;
          last_sent_time = millis();
        } else {
          ws_send_fail_count++;
          Serial.println("[CAM-SEND] ERROR: No available target for frame");
          esp_camera_fb_return(fb);
          continue;
        }
        
        esp_camera_fb_return(fb);
        
      } else if (fb) { 
        esp_camera_fb_return(fb); 
      }
    }
  }
}
// ====================================================================
// Mic (PDM RX)
// ====================================================================
void init_i2s_in(){
  i2sIn.setPinsPdmRx(I2S_MIC_CLOCK_PIN, I2S_MIC_DATA_PIN);
  if (!i2sIn.begin(I2S_MODE_PDM_RX, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    Serial.println("[I2S IN] init failed");
    while(1) { delay(1000); }
  }
}

void taskMicCapture(void*){
  const int samples_per_chunk = BYTES_PER_CHUNK / 2; // int16
  for(;;){
    if (run_audio_stream && aud_ws_ready) {
      AudioChunk ch; ch.n = BYTES_PER_CHUNK;
      int16_t* out = reinterpret_cast<int16_t*>(ch.data);
      int i = 0;
      while (i < samples_per_chunk){
        int v = i2sIn.read();
        if (v == -1) { delay(1); continue; }
        out[i++] = (int16_t)v;
      }
      if (xQueueSend(qAudio, &ch, 0) != pdPASS){
        AudioChunk dump;
        xQueueReceive(qAudio, &dump, 0);
        xQueueSend(qAudio, &ch, 0);
      }
    } else {
      vTaskDelay(pdMS_TO_TICKS(5));
    }
  }
}

void taskMicUpload(void*){
  for(;;){
    if (run_audio_stream && aud_ws_ready){
      AudioChunk ch;
      if (xQueueReceive(qAudio, &ch, pdMS_TO_TICKS(100)) == pdPASS){
        wsAud.sendBinary((const char*)ch.data, ch.n);
      }
    } else {
      vTaskDelay(pdMS_TO_TICKS(10));
    }
  }
}

// ====================================================================
// Speaker (I2S TX) + HTTP /stream.wav (chunked-safe)
// ====================================================================
void init_i2s_out(){
  i2sOut.setPins(I2S_SPK_BCLK, I2S_SPK_LRCK, I2S_SPK_DIN);
  if (!i2sOut.begin(I2S_MODE_STD, TTS_RATE, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO)) {
    Serial.println("[I2S OUT] init failed");
    while(1){ delay(1000); }
  }
}

struct WavFmt {
  uint16_t audioFormat;   // 1=PCM
  uint16_t numChannels;   // 1=mono
  uint32_t sampleRate;    // 16000
  uint32_t byteRate;
  uint16_t blockAlign;
  uint16_t bitsPerSample; // 16
};

static inline void mono16_to_stereo32_msb(const int16_t* in, size_t nSamp, int32_t* outLR, float gain = 1.0f) {
  for (size_t i = 0; i < nSamp; ++i) {
    int32_t s = (int32_t)((float)in[i] * gain);
    int32_t v32 = s << 16;
    outLR[i*2 + 0] = v32;
    outLR[i*2 + 1] = v32;
  }
}

// === chunked 读取辅助 ===
static bool read_line(WiFiClient& cli, String& line, uint32_t timeout_ms=3000){
  line = "";
  uint32_t t0 = millis();
  while (millis() - t0 < timeout_ms){
    while (cli.available()){
      char ch = (char)cli.read();
      if (ch == '\n'){
        if (line.endsWith("\r")) line.remove(line.length()-1);
        return true;
      }
      line += ch;
    }
    delay(1);
  }
  return false;
}

static bool readN_http_body(WiFiClient& cli, uint8_t* buf, size_t n, bool chunked, size_t& chunk_left, uint32_t timeout_ms=3000){
  size_t got = 0;
  uint32_t t0 = millis();

  while (got < n){
    if (!cli.connected()) return false;
    if (!chunked){
      int avail = cli.available();
      if (avail > 0){
        int toread = (int)min((size_t)avail, n - got);
        int r = cli.read(buf + got, toread);
        if (r > 0) got += r;
      } else {
        if (millis() - t0 > timeout_ms) return false;
        delay(1);
      }
    } else {
      if (chunk_left == 0){
        String szline;
        if (!read_line(cli, szline, timeout_ms)) return false;
        int sc = szline.indexOf(';');
        if (sc >= 0) szline = szline.substring(0, sc);
        szline.trim();
        unsigned long sz = strtoul(szline.c_str(), nullptr, 16);
        if (sz == 0){
          String dummy;
          read_line(cli, dummy, 500);
          return false;
        }
        chunk_left = (size_t)sz;
      }
      int avail = cli.available();
      if (avail > 0){
        size_t want = min(n - got, chunk_left);
        int toread = (int)min((size_t)avail, want);
        int r = cli.read(buf + got, toread);
        if (r > 0){
          got += r;
          chunk_left -= (size_t)r;
          if (chunk_left == 0){
            while (cli.available() < 2) { if (millis() - t0 > timeout_ms) return false; delay(1); }
            cli.read(); cli.read();
          }
        }
      } else {
        if (millis() - t0 > timeout_ms) return false;
        delay(1);
      }
    }
  }
  return true;
}

static bool parse_wav_header(WiFiClient& cli, WavFmt& fmt, uint32_t& dataRemaining, bool chunked, size_t& chunk_left){
  uint8_t hdr12[12];
  if (!readN_http_body(cli, hdr12, 12, chunked, chunk_left)) return false;
  if (memcmp(hdr12, "RIFF", 4) != 0 || memcmp(hdr12 + 8, "WAVE", 4) != 0) return false;

  bool gotFmt = false;
  dataRemaining = 0;

  while (true) {
    uint8_t chdr[8];
    if (!readN_http_body(cli, chdr, 8, chunked, chunk_left)) return false;
    uint32_t sz = (uint32_t)chdr[4] | ((uint32_t)chdr[5] << 8) | ((uint32_t)chdr[6] << 16) | ((uint32_t)chdr[7] << 24);

    if (memcmp(chdr, "fmt ", 4) == 0) {
      if (sz < 16) return false;
      uint8_t fmtbuf[32];
      size_t toread = min(sz, (uint32_t)sizeof(fmtbuf));
      if (!readN_http_body(cli, fmtbuf, toread, chunked, chunk_left)) return false;
      uint32_t left = sz - (uint32_t)toread;
      while (left){
        uint8_t dump[64];
        size_t d = min((uint32_t)sizeof(dump), left);
        if (!readN_http_body(cli, dump, d, chunked, chunk_left)) return false;
        left -= d;
      }
      fmt.audioFormat   = (uint16_t) (fmtbuf[0] | (fmtbuf[1] << 8));
      fmt.numChannels   = (uint16_t) (fmtbuf[2] | (fmtbuf[3] << 8));
      fmt.sampleRate    = (uint32_t) (fmtbuf[4] | (fmtbuf[5] << 8) | (fmtbuf[6] << 16) | (fmtbuf[7] << 24));
      fmt.byteRate      = (uint32_t) (fmtbuf[8] | (fmtbuf[9] << 8) | (fmtbuf[10] << 16) | (fmtbuf[11] << 24));
      fmt.blockAlign    = (uint16_t) (fmtbuf[12] | (fmtbuf[13] << 8));
      fmt.bitsPerSample = (uint16_t) (fmtbuf[14] | (fmtbuf[15] << 8));
      gotFmt = true;
    }
    else if (memcmp(chdr, "data", 4) == 0) {
      if (!gotFmt) return false;
      dataRemaining = sz;
      return true;
    }
    else {
      uint32_t left = sz;
      while (left){
        uint8_t dump[128];
        size_t d = min((uint32_t)sizeof(dump), left);
        if (!readN_http_body(cli, dump, d, chunked, chunk_left)) return false;
        left -= d;
      }
    }
  }
}

// ---- HTTP 播放任务
static TaskHandle_t taskHttpPlayHandle = nullptr;
static volatile bool http_play_running = false;

void taskHttpPlay(void*){
  http_play_running = true;
  WiFiClient cli;

  auto readLine = [&](String& out, uint32_t timeout_ms)->bool {
    out = "";
    uint32_t t0 = millis();
    while (millis() - t0 < timeout_ms) {
      while (cli.available()) {
        char c = (char)cli.read();
        if (c == '\r') continue;
        if (c == '\n') return true;
        out += c;
        if (out.length() > 1024) return false;
      }
      delay(1);
    }
    return false;
  };

  auto readNRaw = [&](uint8_t* dst, size_t n, uint32_t timeout_ms)->bool {
    size_t got = 0;
    uint32_t t0 = millis();
    while (got < n) {
      if (!cli.connected()) return false;
      int avail = cli.available();
      if (avail > 0) {
        int take = (int)min((size_t)avail, n - got);
        int r = cli.read(dst + got, take);
        if (r > 0) { got += r; continue; }
      }
      if (millis() - t0 > timeout_ms) return false;
      delay(1);
    }
    return true;
  };

  auto makeBodyReader = [&](bool& is_chunked, uint32_t& chunk_left){
    return [&](uint8_t* dst, size_t n, uint32_t timeout_ms)->bool {
      size_t filled = 0;
      uint32_t t0 = millis();
      while (filled < n) {
        if (!cli.connected()) return false;
        if (is_chunked) {
          if (chunk_left == 0) {
            String szLine;
            if (!readLine(szLine, timeout_ms)) return false;
            int sc = szLine.indexOf(';');
            if (sc >= 0) szLine = szLine.substring(0, sc);
            szLine.trim();
            uint32_t sz = 0;
            if (sscanf(szLine.c_str(), "%x", &sz) != 1) return false;
            if (sz == 0) { String dummy; readLine(dummy, 200); return false; }
            chunk_left = sz;
          }
          size_t need = (size_t)min<uint32_t>(chunk_left, (uint32_t)(n - filled));
          while (cli.available() < (int)need) {
            if (millis() - t0 > timeout_ms) return false;
            if (!cli.connected()) return false;
            delay(1);
          }
          int r = cli.read(dst + filled, need);
          if (r <= 0) {
            if (millis() - t0 > timeout_ms) return false;
            delay(1); continue;
          }
          filled     += r;
          chunk_left -= r;
          if (chunk_left == 0) {
            char crlf[2];
            if (!readNRaw((uint8_t*)crlf, 2, 200)) return false;
          }
        } else {
          if (!readNRaw(dst + filled, n - filled, timeout_ms)) return false;
          filled = n;
        }
      }
      return true;
    };
  };

  static int32_t outLR[1024 * 2];
  const uint32_t BODY_TIMEOUT_MS = 1500;

  while (http_play_running) {
    if (!cli.connected()) {
      if (!cli.connect(SERVER_HOST, SERVER_HTTP_PORT)) { delay(500); continue; }
      String req =
        String("GET ") + HTTP_STREAM_PATH + " HTTP/1.1\r\n" +
        "Host: " + SERVER_HOST + ":" + String(SERVER_HTTP_PORT) + "\r\n" +
        "Connection: keep-alive\r\n\r\n";
      cli.print(req);
    }

    bool header_ok  = false;
    bool is_chunked = false;
    uint32_t content_len = 0;
    {
      String line; uint32_t t0 = millis();
      while (millis() - t0 < 3000) {
        if (!readLine(line, 1000)) { if (!cli.connected()) break; continue; }
        String u = line; u.toLowerCase();
        if (u.startsWith("transfer-encoding:")) { if (u.indexOf("chunked") >= 0) is_chunked = true; }
        else if (u.startsWith("content-length:")) { content_len = (uint32_t) strtoul(u.substring(strlen("content-length:")).c_str(), nullptr, 10); }
        if (line.length() == 0) { header_ok = true; break; }
      }
    }
    if (!header_ok) { cli.stop(); delay(300); continue; }

    uint32_t chunk_left = 0;
    auto readBody = makeBodyReader(is_chunked, chunk_left);

    uint8_t hdr12[12];
    if (!readBody(hdr12, 12, 1000)) { cli.stop(); delay(300); continue; }
    if (memcmp(hdr12, "RIFF", 4) != 0 || memcmp(hdr12 + 8, "WAVE", 4) != 0) { cli.stop(); delay(300); continue; }

    bool  gotFmt = false, gotData = false;
    uint8_t chdr[8];
    uint16_t audioFormat=0, numChannels=0, bitsPerSample=0;
    uint32_t sampleRate=0;

    while (!gotData) {
      if (!readBody(chdr, 8, 1000)) { cli.stop(); delay(300); goto reconnect; }
      uint32_t sz = (uint32_t)chdr[4] | ((uint32_t)chdr[5]<<8) | ((uint32_t)chdr[6]<<16) | ((uint32_t)chdr[7]<<24);

      if (memcmp(chdr, "fmt ", 4) == 0) {
        if (sz < 16) { cli.stop(); delay(300); goto reconnect; }
        uint8_t fmtbuf[32];
        size_t toread = min(sz, (uint32_t)sizeof(fmtbuf));
        if (!readBody(fmtbuf, toread, 1000)) { cli.stop(); delay(300); goto reconnect; }
        if (sz > toread) {
          size_t left = sz - toread;
          while (left) { uint8_t dump[128]; size_t d = min(left, sizeof(dump));
            if (!readBody(dump, d, 1000)) { cli.stop(); delay(300); goto reconnect; }
            left -= d;
          }
        }
        audioFormat   = (uint16_t)(fmtbuf[0] | (fmtbuf[1] << 8));
        numChannels   = (uint16_t)(fmtbuf[2] | (fmtbuf[3] << 8));
        sampleRate    = (uint32_t)(fmtbuf[4] | (fmtbuf[5] << 8) | (fmtbuf[6] << 16) | (fmtbuf[7] << 24));
        bitsPerSample = (uint16_t)(fmtbuf[14] | (fmtbuf[15] << 8));
        gotFmt = true;
      }
      else if (memcmp(chdr, "data", 4) == 0) {
        if (!gotFmt) { cli.stop(); delay(300); goto reconnect; }
        gotData = true;
      }
      else {
        size_t left = sz;
        while (left) { uint8_t dump[128]; size_t d = min(left, sizeof(dump));
          if (!readBody(dump, d, 1000)) { cli.stop(); delay(300); goto reconnect; }
          left -= d;
        }
      }
    }

    if (!(audioFormat==1 && numChannels==1 && bitsPerSample==16 && (sampleRate==8000 || sampleRate==12000 || sampleRate==16000))) {
      cli.stop(); delay(300); continue;
    }

    static uint32_t current_out_rate = 0;
    if (current_out_rate != sampleRate) {
      i2sOut.begin(I2S_MODE_STD, (int)sampleRate, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO);
      current_out_rate = sampleRate;
    }

    while (http_play_running) {
      uint8_t inbuf[2048];
      size_t  filled = 0;

      // 根据采样率计算20ms字节数（mono,16bit）
      uint32_t bytes20 = (sampleRate * 2 * 20) / 1000; // 16k=640,12k=480,8k=320
      if (bytes20 < 2) bytes20 = 2;

      if (!readBody(inbuf, bytes20, BODY_TIMEOUT_MS)) { break; }
      filled = bytes20;

      while (filled + bytes20 <= sizeof(inbuf)) {
        if (!readBody(inbuf + filled, bytes20, 2)) { break; }
        filled += bytes20;
      }

      if (filled & 1) filled -= 1;
      if (filled == 0) { vTaskDelay(pdMS_TO_TICKS(1)); continue; }

      size_t samp = filled / 2;
      mono16_to_stereo32_msb((const int16_t*)inbuf, samp, outLR, 1.0f);

      size_t bytes = samp * 2 * sizeof(int32_t);
      size_t off = 0;
      while (off < bytes && http_play_running) {
        size_t wrote = i2sOut.write((uint8_t*)outLR + off, bytes - off);
        if (wrote == 0) vTaskDelay(pdMS_TO_TICKS(1));
        else off += wrote;
      }
    }

  reconnect:
    cli.stop();
    delay(200);
  }

  cli.stop();
  vTaskDelete(nullptr);
}

void startStreamWav(){
  if (taskHttpPlayHandle) return;
  xTaskCreatePinnedToCore(taskHttpPlay, "http_wav", 8192, nullptr, 2, &taskHttpPlayHandle, 0);
}
void stopStreamWav(){
  if (!taskHttpPlayHandle) return;
  http_play_running = false;
  vTaskDelay(pdMS_TO_TICKS(50));
  taskHttpPlayHandle = nullptr;
}

// ====================================================================
// TTS（二进制分片）保留但默认不启用
// ====================================================================
void taskTTSPlay(void*){
  static int32_t stereo32Buf[1024*2];
  for(;;){
    if (!tts_playing){ vTaskDelay(pdMS_TO_TICKS(5)); continue; }
    TTSChunk ch;
    if (xQueueReceive(qTTS, &ch, pdMS_TO_TICKS(50)) == pdPASS){
      size_t inSamp  = ch.n / 2;
      int16_t* inPtr = (int16_t*)ch.data;
      size_t outPairs = 0;
      for (size_t i = 0; i < inSamp; ++i){
        int32_t s = (int32_t)inPtr[i];
        int32_t v32 = s << 16;
        stereo32Buf[outPairs*2 + 0] = v32;
        stereo32Buf[outPairs*2 + 1] = v32;
        outPairs++;
        if (outPairs >= 1024){
          size_t bytes = outPairs * 2 * sizeof(int32_t);
          size_t off = 0;
          while (off < bytes){
            size_t wrote = i2sOut.write((uint8_t*)stereo32Buf + off, bytes - off);
            if (wrote == 0) vTaskDelay(pdMS_TO_TICKS(1)); else off += wrote;
          }
          outPairs = 0;
        }
      }
      if (outPairs){
        size_t bytes = outPairs * 2 * sizeof(int32_t);
        size_t off = 0;
        while (off < bytes){
          size_t wrote = i2sOut.write((uint8_t*)stereo32Buf + off, bytes - off);
          if (wrote == 0) vTaskDelay(pdMS_TO_TICKS(1)); else off += wrote;
        }
      }
    }
  }
}

inline void tts_reset_queue(){ if (qTTS) xQueueReset(qTTS); }

// ====================================================================
// IMU (ICM42688 over SPI) 50Hz via UDP
// ====================================================================

// --- ICM42688-P registers (Bank0) ---
#define REG_WHO_AM_I      0x75  // expect 0x47
#define REG_BANK_SEL      0x76
#define REG_PWR_MGMT0     0x4E  // 0x0F => accel+gyro LN
#define REG_TEMP_H        0x1D  // then ACC(1F..24), GYR(25..2A)
#define BURST_FIRST       REG_TEMP_H
#define BURST_COUNT       14

// scale (常见默认为 ±16g / ±2000 dps)
static const float ACC_LSB_PER_G   = 2048.0f;   // 1 g = 2048 LSB
static const float GYR_LSB_PER_DPS = 16.4f;     // 1 dps = 16.4 LSB
static const float G               = 9.80665f;
static const float TEMP_SENS       = 132.48f;   // °C/LSB
static const float TEMP_OFFSET     = 25.0f;

static inline void imu_cs_low()  { digitalWrite(IMU_SPI_CS, LOW);  }
static inline void imu_cs_high() { digitalWrite(IMU_SPI_CS, HIGH); }

uint8_t imu_read8(uint8_t reg){
  imu_cs_low();
  SPI.transfer(reg | 0x80);
  uint8_t v = SPI.transfer(0x00);
  imu_cs_high();
  return v;
}
void imu_write8(uint8_t reg, uint8_t val){
  imu_cs_low();
  SPI.transfer(reg & 0x7F);
  SPI.transfer(val);
  imu_cs_high();
}
void imu_readn(uint8_t start_reg, uint8_t* dst, size_t n){
  imu_cs_low();
  SPI.transfer(start_reg | 0x80);
  for (size_t i=0;i<n;i++) dst[i] = SPI.transfer(0x00);
  imu_cs_high();
}

bool imu_init_spi(){
  SPI.begin(IMU_SPI_SCK, IMU_SPI_MISO, IMU_SPI_MOSI, IMU_SPI_CS);
  pinMode(IMU_SPI_CS, OUTPUT);
  imu_cs_high();
  delay(5);

  uint8_t who = imu_read8(REG_WHO_AM_I);
  if (who != 0x47) return false;

  imu_write8(REG_PWR_MGMT0, 0x0F); // accel+gyro LN
  delay(10);
  return true;
}

bool imu_read_once(float& tempC, float& ax, float& ay, float& az, float& gx, float& gy, float& gz){
  uint8_t raw[BURST_COUNT];
  imu_readn(BURST_FIRST, raw, sizeof(raw));

  auto s16 = [&](int idx)->int16_t {
    return (int16_t)((raw[idx] << 8) | raw[idx+1]);
  };

  int16_t tr  = s16(0);
  int16_t axr = s16(2);
  int16_t ayr = s16(4);
  int16_t azr = s16(6);
  int16_t gxr = s16(8);
  int16_t gyr = s16(10);
  int16_t gzr = s16(12);

  tempC = (float)tr / TEMP_SENS + TEMP_OFFSET;
  ax = ((float)axr / ACC_LSB_PER_G) * G;
  ay = ((float)ayr / ACC_LSB_PER_G) * G;
  az = ((float)azr / ACC_LSB_PER_G) * G;
  gx =  (float)gxr / GYR_LSB_PER_DPS;
  gy =  (float)gyr / GYR_LSB_PER_DPS;
  gz =  (float)gzr / GYR_LSB_PER_DPS;

  return true;
}

// 轻微平滑，便于观察；不改变 UDP 字段名
static const float EMA_ALPHA = 0.20f;
bool  ema_inited = false;
float ax_f=0, ay_f=0, az_f=0;

void taskImuLoop(void*){
  for(;;){
    static bool inited = false;
    if (!inited){
      inited = imu_init_spi();
      if (!inited){ vTaskDelay(pdMS_TO_TICKS(500)); continue; }
    }

    float tempC, ax, ay, az, gx, gy, gz;
    if (!imu_read_once(tempC, ax, ay, az, gx, gy, gz)){
      inited = false; vTaskDelay(pdMS_TO_TICKS(50)); continue;
    }

    if (!ema_inited){ ax_f=ax; ay_f=ay; az_f=az; ema_inited=true; }
    else {
      ax_f = EMA_ALPHA*ax + (1-EMA_ALPHA)*ax_f;
      ay_f = EMA_ALPHA*ay + (1-EMA_ALPHA)*ay_f;
      az_f = EMA_ALPHA*az + (1-EMA_ALPHA)*az_f;
    }

    char buf[256];
    unsigned long ts = millis();
    int n = snprintf(buf, sizeof(buf),
      "{\"ts\":%lu,\"temp_c\":%.2f,"
      "\"accel\":{\"x\":%.3f,\"y\":%.3f,\"z\":%.3f},"
      "\"gyro\":{\"x\":%.3f,\"y\":%.3f,\"z\":%.3f}}",
      ts, tempC, ax_f, ay_f, az_f, gx, gy, gz);

    if (n > 0) {
      udp.beginPacket(UDP_HOST, UDP_PORT);
      udp.write((const uint8_t*)buf, n);
      udp.endPacket();
    }
    vTaskDelay(pdMS_TO_TICKS(20)); // 50 Hz
  }
}

// ====================================================================
// Setup / Loop
// ====================================================================
void setup() {
  Serial.begin(115200);
  delay(300);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  esp_wifi_set_ps(WIFI_PS_NONE);
  esp_wifi_set_protocol(WIFI_IF_STA, WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);
  connect_wifi_with_logging();

  if (!init_camera()) { Serial.println("[CAM] init failed, reboot..."); delay(1500); esp_restart(); }

  udp.begin(0);

  qFrames = xQueueCreate(3, sizeof(fb_ptr_t));  // 增加到3个缓冲，减少丢帧

  xTaskCreatePinnedToCore(taskCamCapture, "cam_cap", 10240, NULL, 4, NULL, 1);
  xTaskCreatePinnedToCore(taskCamSend,    "cam_snd",  8192, NULL, 3, NULL, 1);

  if (ENABLE_AUDIO_TEST) {
    init_i2s_in();
    init_i2s_out();
    qAudio  = xQueueCreate(AUDIO_QUEUE_DEPTH, sizeof(AudioChunk));
    qTTS    = xQueueCreate(TTS_QUEUE_DEPTH, sizeof(TTSChunk));
    xTaskCreatePinnedToCore(taskMicCapture, "mic_cap",   4096, NULL, 2, NULL, 0);
    xTaskCreatePinnedToCore(taskMicUpload,  "mic_upl",   4096, NULL, 2, NULL, 1);
    xTaskCreatePinnedToCore(taskTTSPlay,    "tts_play",  4096, NULL, 2, NULL, 0);
  } else {
    Serial.println("[AUDIO] disabled for text/image test");
  }

  if (ENABLE_IMU_UDP) {
    xTaskCreatePinnedToCore(taskImuLoop,    "imu_loop",  4096, NULL, 2, NULL, 0);
  } else {
    Serial.println("[IMU] disabled for text/image test");
  }

  wsCam.onEvent([](WebsocketsEvent ev, String){
    if (ev == WebsocketsEvent::ConnectionOpened)  { 
      cam_ws_ready = true;  
      frame_sent_count = 0;
      frame_dropped_count = 0;
      ws_send_fail_count = 0;
      last_stats_time = millis();
      send_cam_text("HELLO:GLASSES|" + String(DEVICE_ID) + "|" + String(ACTIVE_NETWORK->label));
      send_cam_text(build_status_message());
    }
    if (ev == WebsocketsEvent::ConnectionClosed)  { cam_ws_ready = false; }
  });

  wsCam.onMessage([](WebsocketsMessage msg){
    if (msg.isText()){
      handle_text_command("cloud", msg.data());
    }
  });

  wsDirect.onEvent([](WebsocketsEvent ev, String){
    if (ev == WebsocketsEvent::ConnectionOpened) {
      direct_ws_ready = true;
      Serial.println("[WS-DIRECT] connected");
      send_direct_text("HELLO:DIRECT|" + String(DEVICE_ID) + "|" + WiFi.localIP().toString());
      send_direct_text(build_status_message());
    }
    if (ev == WebsocketsEvent::ConnectionClosed) {
      direct_ws_ready = false;
      Serial.println("[WS-DIRECT] closed");
    }
  });

  wsDirect.onMessage([](WebsocketsMessage msg){
    if (msg.isText()) {
      handle_text_command("direct", msg.data());
    }
  });

  wsAud.onEvent([](WebsocketsEvent ev, String){
    if (ev == WebsocketsEvent::ConnectionOpened)  { aud_ws_ready = true; }
    if (ev == WebsocketsEvent::ConnectionClosed)  { 
      aud_ws_ready = false; 
      stopStreamWav();
    }
  });

  wsAud.onMessage([](WebsocketsMessage msg){
    if (msg.isText()){
      String s = msg.data(); s.trim();
      if (s == "RESTART"){
        run_audio_stream = false; xQueueReset(qAudio); delay(50);
        wsAud.send("START"); run_audio_stream = true;
      }
    }
  });
}

void loop() {
  handle_serial_input();

  if (!wsCam.available()) {
    if (wsCam.connect(SERVER_HOST, SERVER_PORT, CAM_WS_PATH)) {
      Serial.println("[WS-CAM] connected");
    } else { delay(1000); }
  }

  try_connect_direct_ws();

  if (ENABLE_AUDIO_TEST && !wsAud.available()) {
    if (wsAud.connect(SERVER_HOST, SERVER_PORT, AUD_WS_PATH)) {
      Serial.println("[WS-AUD] connected");
      delay(50);
      run_audio_stream = true;
      wsAud.send("START");
      startStreamWav();
    } else { delay(2000); }
  }

  wsCam.poll();
  wsAud.poll();
  wsDirect.poll();
  delay(2);
}
