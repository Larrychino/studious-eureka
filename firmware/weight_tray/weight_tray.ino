/*
  MedVerify weight tray firmware (ESP32 + HX711 load cell amplifier).

  The tray weighs continuously, waits for the weight to settle, and reports
  each settled change bigger than the noise threshold as
      {ts, weight_before_g, weight_after_g}
  to POST /api/devices/tray-events. The server decides whether it was a
  removal or a return and reconciles it against what staff recorded.

  Events are queued in RAM and sent in batches, so a Wi-Fi drop does not lose
  them (up to QUEUE_SIZE events). Time comes from NTP so events carry the
  moment they happened, not the moment they were sent.

  Libraries: "HX711" by Bogdan Necula (bogde), ESP32 Arduino core.
*/
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <time.h>
#include "HX711.h"
#include "config.h"

static const float NOISE_G = 1.5f;          // ignore smaller changes (matches server NOISE_G)
static const float SETTLE_SPREAD_G = 0.6f;  // max spread across the window to count as settled
static const int   WINDOW = 10;             // samples (~1 s at 10 Hz)
static const int   QUEUE_SIZE = 64;
static const unsigned long SEND_EVERY_MS = 5000;

struct TrayEvent { time_t ts; float before; float after; };

HX711 scale;
float window_buf[WINDOW];
int window_pos = 0, window_filled = 0;
float last_settled = NAN;
TrayEvent queue_buf[QUEUE_SIZE];
int queue_len = 0;
unsigned long last_send = 0;

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(250);
}

void enqueue(float before, float after) {
  if (queue_len == QUEUE_SIZE) {  // full: drop the oldest, keep the newest
    memmove(queue_buf, queue_buf + 1, sizeof(TrayEvent) * (QUEUE_SIZE - 1));
    queue_len--;
  }
  queue_buf[queue_len++] = {time(nullptr), before, after};
}

String isoTime(time_t t) {
  char buf[32];
  struct tm tm;
  gmtime_r(&t, &tm);
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm);
  return String(buf);
}

void sendQueue() {
  if (queue_len == 0) return;
  connectWifi();
  if (WiFi.status() != WL_CONNECTED) return;
  String body = "{\"firmware\":\"tray-0.1\",\"events\":[";
  for (int i = 0; i < queue_len; i++) {
    if (i) body += ",";
    body += "{\"ts\":\"" + isoTime(queue_buf[i].ts) + "\",\"weight_before_g\":" + String(queue_buf[i].before, 1) +
            ",\"weight_after_g\":" + String(queue_buf[i].after, 1) + "}";
  }
  body += "]}";
  WiFiClientSecure client;
  client.setCACert(ROOT_CA);
  HTTPClient http;
  http.begin(client, MV_URL);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Id", DEVICE_ID);
  http.addHeader("X-Device-Key", DEVICE_KEY);
  int code = http.POST(body);
  http.end();
  if (code >= 200 && code < 300) queue_len = 0;  // otherwise keep and retry
}

void setup() {
  Serial.begin(115200);
  scale.begin(HX711_DOUT_PIN, HX711_SCK_PIN);
  scale.set_scale(CALIBRATION_FACTOR);
  // Do NOT tare on boot: the tray may already hold medicines. Tare once, empty, at install
  // and store the offset; here we use the absolute reading.
  connectWifi();
  configTime(0, 0, "pool.ntp.org", "time.google.com");
}

void loop() {
  if (scale.is_ready()) {
    float g = scale.get_units(1);
    window_buf[window_pos] = g;
    window_pos = (window_pos + 1) % WINDOW;
    if (window_filled < WINDOW) window_filled++;
    if (window_filled == WINDOW) {
      float lo = window_buf[0], hi = window_buf[0], sum = 0;
      for (int i = 0; i < WINDOW; i++) { lo = min(lo, window_buf[i]); hi = max(hi, window_buf[i]); sum += window_buf[i]; }
      if (hi - lo <= SETTLE_SPREAD_G) {
        float settled = sum / WINDOW;
        if (isnan(last_settled)) {
          last_settled = settled;
        } else if (fabs(settled - last_settled) >= NOISE_G) {
          Serial.printf("change %.1f -> %.1f g\n", last_settled, settled);
          enqueue(last_settled, settled);
          last_settled = settled;
        }
      }
    }
  }
  if (millis() - last_send > SEND_EVERY_MS) {
    last_send = millis();
    sendQueue();
  }
  delay(100);
}
