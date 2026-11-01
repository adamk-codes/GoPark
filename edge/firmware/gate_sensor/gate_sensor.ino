// GoPark gate presence sensor. Posts one gate_sensor signal per car, exactly as
// edge/INTEGRATION.md §2–§4. The beam rules and the queue match edge/gate_logic.py
// (that module is what the unit tests check).
//
//   A = street side, B = lot side, about 1 m apart, bumper height.
//   A then B -> enter. B then A -> exit. observed_at is the second beam.
//   One beam, or A-B-A (backed out), is not an event.
//
// Flash with the ESP32 Arduino core. Fill in the defines below. Serial is 115200.
// Type "e" or "x" and Enter to post a passage without the beams (bench test).
// Diff that JSON against: uv run --group edge python -m edge.sensor_sim --lot-id AUB --dry-run
//
// CLOCK_MODE 0: NTP, Beirut (EET-2EEST,M3.5.0/0,M10.5.0/0). Run the camera with --clock field.
// CLOCK_MODE 1: GET /dev/clock every 60 s. Run the camera with --clock demo (the default).

#include <WiFi.h>
#include <HTTPClient.h>
#include <time.h>

#define WIFI_SSID   "changeme"
#define WIFI_PASS   "changeme"
#define API_BASE    "http://192.168.1.20:8000"
#define LOT_ID      "AUB"
#define GATE_N      1
#define LANE        1
#define CLOCK_MODE  0

// Digital break-beams: INPUT_PULLUP, broken when the pin is LOW.
// SENSOR_MODE 1 uses two HC-SR04s instead: a distance under CAR_CM means "broken".
#define SENSOR_MODE 0
#define PIN_A       18
#define PIN_B       19
#define TRIG_A      25
#define ECHO_A      26
#define TRIG_B      27
#define ECHO_B      14
#define CAR_CM      80

static const uint32_t DEBOUNCE_MS = 30;
static const uint32_t CLEAR_MS = 800;
static const uint32_t MAX_PASSAGE_MS = 20000;
static const int QUEUE_MAX = 50;
static const uint32_t BACKOFF_MS[] = {1000, 2000, 5000, 10000};
static const int BACKOFF_N = 4;

struct Queued {
  bool used;
  char id[40];
  char kind[8];
  char observed_at[40];
};

static Queued queueSlots[QUEUE_MAX];
static int qHead = 0, qCount = 0, qAttempt = 0;
static uint32_t nextTryMs = 0;

struct Civil {
  int Y, M, D, h, m, s;
  char offset[8];
  bool set;
  uint32_t atMs;
};

static Civil clockAnchor = {};

static bool rawA = false, rawB = false, stableA = false, stableB = false;
static uint32_t sinceA = 0, sinceB = 0;
static char edgeBeam[8];
static uint32_t edgeMs[8];
static int edgeN = 0;
static bool clearArmed = false;
static uint32_t clearSince = 0;

static bool pinBroken(int pin) {
  return digitalRead(pin) == LOW;
}

static bool sonarBroken(int trig, int echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);
  unsigned long us = pulseIn(echo, HIGH, 30000);
  if (us == 0) return false;
  return (us / 58.0f) < CAR_CM;
}

static bool beamBroken(bool whichA) {
  if (SENSOR_MODE == 0) return pinBroken(whichA ? PIN_A : PIN_B);
  return sonarBroken(whichA ? TRIG_A : TRIG_B, whichA ? ECHO_A : ECHO_B);
}

static bool leap(int y) {
  return (y % 4 == 0 && y % 100 != 0) || (y % 400 == 0);
}

static int monthDays(int y, int m) {
  static const int d[] = {0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  return d[m] + (m == 2 && leap(y) ? 1 : 0);
}

static void addSeconds(Civil &c, int add) {
  int s = c.s + add;
  int m = c.m + s / 60;
  c.s = s % 60;
  if (c.s < 0) { c.s += 60; m -= 1; }
  int h = c.h + m / 60;
  c.m = m % 60;
  if (c.m < 0) { c.m += 60; h -= 1; }
  int days = h / 24;
  c.h = h % 24;
  if (c.h < 0) { c.h += 24; days -= 1; }
  c.D += days;
  while (c.D > monthDays(c.Y, c.M)) {
    c.D -= monthDays(c.Y, c.M);
    if (++c.M > 12) { c.M = 1; c.Y++; }
  }
  while (c.D < 1) {
    if (--c.M < 1) { c.M = 12; c.Y--; }
    c.D += monthDays(c.Y, c.M);
  }
}

static void formatCivil(const Civil &c, char *out, size_t n) {
  snprintf(out, n, "%04d-%02d-%02dT%02d:%02d:%02d%s", c.Y, c.M, c.D, c.h, c.m, c.s, c.offset);
}

static bool parseIso(const char *s, Civil &c) {
  char sign = '+';
  int oh = 0, om = 0;
  int n = sscanf(s, "%d-%d-%dT%d:%d:%d%c%d:%d", &c.Y, &c.M, &c.D, &c.h, &c.m, &c.s, &sign, &oh, &om);
  if (n < 6) return false;
  if (n >= 9) snprintf(c.offset, sizeof(c.offset), "%c%02d:%02d", sign, oh, om);
  else if (strchr(s, 'Z')) snprintf(c.offset, sizeof(c.offset), "+00:00");
  else return false;
  return true;
}

static bool clockReady() {
  if (CLOCK_MODE == 0) {
    time_t now = time(nullptr);
    return now > 1700000000;
  }
  return clockAnchor.set;
}

static void formatNow(char *out, size_t n) {
  if (CLOCK_MODE == 0) {
    time_t now = time(nullptr);
    struct tm tm;
    localtime_r(&now, &tm);
    char z[8] = "+0000";
    strftime(out, n, "%Y-%m-%dT%H:%M:%S", &tm);
    strftime(z, sizeof(z), "%z", &tm);
    size_t len = strlen(out);
    if (strlen(z) >= 5 && len + 6 < n) {
      snprintf(out + len, n - len, "%c%c%c:%c%c", z[0], z[1], z[2], z[3], z[4]);
    }
    return;
  }
  Civil c = clockAnchor;
  addSeconds(c, (int)((millis() - clockAnchor.atMs) / 1000));
  formatCivil(c, out, n);
}

static void formatAt(uint32_t atMs, char *out, size_t n) {
  // Seconds precision, anchored at the beam time, not at the later HTTP post.
  if (CLOCK_MODE == 0) {
    time_t now = time(nullptr) - (time_t)((millis() - atMs) / 1000);
    struct tm tm;
    localtime_r(&now, &tm);
    char z[8] = "+0000";
    strftime(out, n, "%Y-%m-%dT%H:%M:%S", &tm);
    strftime(z, sizeof(z), "%z", &tm);
    size_t len = strlen(out);
    if (strlen(z) >= 5 && len + 6 < n)
      snprintf(out + len, n - len, "%c%c%c:%c%c", z[0], z[1], z[2], z[3], z[4]);
    return;
  }
  Civil c = clockAnchor;
  addSeconds(c, (int)((atMs - clockAnchor.atMs) / 1000));
  formatCivil(c, out, n);
}

static void syncClock() {
  if (CLOCK_MODE == 0) {
    configTzTime("EET-2EEST,M3.5.0/0,M10.5.0/0", "pool.ntp.org", "time.nist.gov");
    return;
  }
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.setTimeout(3000);
  http.begin(String(API_BASE) + "/dev/clock");
  int code = http.GET();
  String body = code == 200 ? http.getString() : "";
  http.end();
  if (code != 200) {
    Serial.printf("clock: GET /dev/clock -> %d\n", code);
    return;
  }
  int key = body.indexOf("\"now\"");
  int q1 = body.indexOf('"', key + 5);
  int q2 = q1 >= 0 ? body.indexOf('"', q1 + 1) : -1;
  if (q1 < 0 || q2 < 0) return;
  String iso = body.substring(q1 + 1, q2);
  Civil c = {};
  if (!parseIso(iso.c_str(), c)) return;
  c.set = true;
  c.atMs = millis();
  clockAnchor = c;
  Serial.printf("clock: %s\n", iso.c_str());
}

static void makeUuid(char *out) {
  uint8_t b[16];
  for (int i = 0; i < 16; i++) b[i] = esp_random() & 0xff;
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  snprintf(out, 40, "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
           b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7], b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]);
}

static void enqueue(const char *kind, const char *observedAt) {
  if (qCount == QUEUE_MAX) {
    queueSlots[qHead].used = false;
    qHead = (qHead + 1) % QUEUE_MAX;
    qCount--;
    Serial.println("queue full: dropped oldest");
  }
  int idx = (qHead + qCount) % QUEUE_MAX;
  Queued &slot = queueSlots[idx];
  slot.used = true;
  makeUuid(slot.id);
  snprintf(slot.kind, sizeof(slot.kind), "%s", kind);
  snprintf(slot.observed_at, sizeof(slot.observed_at), "%s", observedAt);
  qCount++;
  Serial.printf("queued %s %s id %s (%d waiting)\n", kind, observedAt, slot.id, qCount);
}

static int postHead() {
  if (qCount == 0 || WiFi.status() != WL_CONNECTED) return -1;
  Queued &slot = queueSlots[qHead];
  char body[320];
  snprintf(body, sizeof(body),
           "{\"client_event_id\":\"%s\",\"device_id\":\"gate-%s-%d\",\"lot_id\":\"%s\","
           "\"source\":\"gate_sensor\",\"kind\":\"%s\",\"observed_at\":\"%s\","
           "\"payload\":{\"gate_id\":\"%s-%d\",\"lane\":%d}}",
           slot.id, LOT_ID, GATE_N, LOT_ID, slot.kind, slot.observed_at, LOT_ID, GATE_N, LANE);
  HTTPClient http;
  http.setTimeout(3000);
  http.begin(String(API_BASE) + "/signals");
  http.addHeader("Content-Type", "application/json");
  int code = http.POST((uint8_t *)body, strlen(body));
  http.end();
  Serial.printf("POST /signals %s -> %d\n", slot.id, code);
  return code;
}

static void pumpQueue() {
  if (qCount == 0) return;
  uint32_t now = millis();
  if ((int32_t)(now - nextTryMs) < 0) return;
  int code = postHead();
  if (code >= 200 && code < 300) {
    queueSlots[qHead].used = false;
    qHead = (qHead + 1) % QUEUE_MAX;
    qCount--;
    qAttempt = 0;
    nextTryMs = now;
    return;
  }
  if (code == 422 || (code >= 400 && code < 500 && code != 408 && code != 429)) {
    Serial.printf("drop %s (HTTP %d)\n", queueSlots[qHead].id, code);
    queueSlots[qHead].used = false;
    qHead = (qHead + 1) % QUEUE_MAX;
    qCount--;
    qAttempt = 0;
    nextTryMs = now;
    return;
  }
  uint32_t delayMs = BACKOFF_MS[qAttempt < BACKOFF_N ? qAttempt : BACKOFF_N - 1];
  qAttempt++;
  nextTryMs = now + delayMs;
  Serial.printf("retry in %lu ms (%d still queued)\n", (unsigned long)delayMs, qCount);
}

static void noteEdge(char beam, uint32_t t) {
  if (edgeN < 8) {
    edgeBeam[edgeN] = beam;
    edgeMs[edgeN] = t;
    edgeN++;
  }
}

static void debounce(bool broken, bool &raw, bool &stable, uint32_t &since, char beam, uint32_t t) {
  if (broken != raw) {
    raw = broken;
    since = t;
  }
  if ((uint32_t)(t - since) < DEBOUNCE_MS || stable == broken) return;
  stable = broken;
  if (broken) noteEdge(beam, t);
}

static void finishPassage() {
  // Collapse consecutive breaks of the same beam, then accept only A-B or B-A.
  // observed_at is the second beam's break, not this later "both clear" moment.
  char beams[8];
  uint32_t times[8];
  int n = 0;
  for (int i = 0; i < edgeN; i++) {
    if (n == 0 || beams[n - 1] != edgeBeam[i]) {
      beams[n] = edgeBeam[i];
      times[n] = edgeMs[i];
      n++;
    }
  }
  const char *kind = nullptr;
  uint32_t at = 0;
  if (n == 2 && beams[0] == 'A' && beams[1] == 'B') { kind = "enter"; at = times[1]; }
  else if (n == 2 && beams[0] == 'B' && beams[1] == 'A') { kind = "exit"; at = times[1]; }
  else if (n > 0) Serial.println("no event (one beam or the car backed out)");
  if (kind && clockReady()) {
    char iso[40];
    formatAt(at, iso, sizeof(iso));
    enqueue(kind, iso);
  } else if (kind) {
    Serial.println("passage ignored: clock not ready");
  }
  edgeN = 0;
  clearArmed = false;
}

static void sampleBeams(bool aBroken, bool bBroken, uint32_t t) {
  debounce(aBroken, rawA, stableA, sinceA, 'A', t);
  debounce(bBroken, rawB, stableB, sinceB, 'B', t);
  if (stableA || stableB) {
    if (edgeN > 0 && (uint32_t)(t - edgeMs[0]) > MAX_PASSAGE_MS) {
      edgeN = 0;
      Serial.println("beam held too long; passage dropped");
    }
    clearArmed = false;
    return;
  }
  if (edgeN == 0) {
    clearArmed = false;
    return;
  }
  if (!clearArmed) {
    clearArmed = true;
    clearSince = t;
  }
  if ((uint32_t)(t - clearSince) >= CLEAR_MS) finishPassage();
}

static void handleSerial() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) return;
  char c = line.charAt(0);
  if (c != 'e' && c != 'E' && c != 'x' && c != 'X') return;
  if (!clockReady()) {
    Serial.println("clock not ready");
    return;
  }
  char iso[40];
  formatNow(iso, sizeof(iso));
  enqueue(c == 'e' || c == 'E' ? "enter" : "exit", iso);
}

static uint32_t lastClockMs = 0;
static uint32_t lastWifiMs = 0;

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("gopark gate sensor");
  if (SENSOR_MODE == 0) {
    pinMode(PIN_A, INPUT_PULLUP);
    pinMode(PIN_B, INPUT_PULLUP);
  } else {
    pinMode(TRIG_A, OUTPUT);
    pinMode(TRIG_B, OUTPUT);
    pinMode(ECHO_A, INPUT);
    pinMode(ECHO_B, INPUT);
  }
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("wifi: joining %s, api %s, lot %s gate %d lane %d, clock %s\n",
                WIFI_SSID, API_BASE, LOT_ID, GATE_N, LANE, CLOCK_MODE == 0 ? "field" : "demo");
  syncClock();
  lastClockMs = millis();
}

void loop() {
  uint32_t now = millis();
  if (WiFi.status() != WL_CONNECTED && (uint32_t)(now - lastWifiMs) > 5000) {
    lastWifiMs = now;
    WiFi.reconnect();
  }
  if ((uint32_t)(now - lastClockMs) > 60000) {
    lastClockMs = now;
    syncClock();
  }
  handleSerial();
  if (SENSOR_MODE == 0) {
    sampleBeams(beamBroken(true), beamBroken(false), now);
  } else {
    // Sonar blocks for up to 30 ms per echo; sample both, then the same decoder.
    bool a = beamBroken(true);
    bool b = beamBroken(false);
    sampleBeams(a, b, millis());
  }
  pumpQueue();
  delay(SENSOR_MODE == 0 ? 10 : 60);
}
