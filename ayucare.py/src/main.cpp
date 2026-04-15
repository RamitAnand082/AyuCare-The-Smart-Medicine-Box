#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <LiquidCrystal_I2C.h>
#include <Wire.h>
#include <time.h>

// NodeMCU-32S / ESP32-WROOM: default I2C pins for LCD backpack
static const int I2C_SDA = 21;
static const int I2C_SCL = 22;

// -------------------- USER CONFIGURATIONS --------------------

// WiFi credentials
const char *WIFI_SSID     = "Ramit's A36";
const char *WIFI_PASSWORD = "ssdnssdn";

// API configuration (matches Ayucare README)
// Base URL of your Flask server, WITHOUT trailing slash.
// Examples:
//   "http://192.168.1.100:5000"
//   "http://ayucare.local:5000"
// IMPORTANT: this is your computer's LAN IP so the ESP32 can reach Flask.
// [FIXED] Must match Flask host IP shown by run.py ("Running on http://<LAN-IP>:5000")
const char *API_BASE_URL        = "http://10.209.143.198:5000";

// Box ID assigned in the dashboard (e.g. "BOX001")
const char *BOX_ID              = "BOX001";

// How often to re-fetch schedule from server (ms)
const unsigned long SCHEDULE_REFRESH_INTERVAL_MS = 10UL * 1000UL; // [FIXED] refresh every 10s

// Medicine alert behavior
const unsigned long MEDICINE_BUZZER_ON_MS = 5000UL; // [FIXED] buzzer ON for 5 seconds only

// Time / NTP (IST = UTC +5:30, no DST)
const long GMT_OFFSET_SEC    = 19800; // 5.5 hours
const int  DAYLIGHT_OFFSET_S = 0;
const char *NTP_SERVER_1     = "pool.ntp.org";
const char *NTP_SERVER_2     = "time.nist.gov";

// Hardware pins
const int BUZZER_PIN  = 25;     // Active HIGH buzzer
const int BUTTON_PIN  = 26;     // INPUT_PULLUP, active LOW  [FIXED: match user wiring]
const bool BUZZER_ACTIVE_HIGH = true; // set false if your buzzer is active-LOW
const bool ENABLE_BUTTON_SELFTEST = false; // disable in production alarm flow

// LCD: address chosen at runtime after I2C scan (library ctor needs fixed addr, so we allocate once).
LiquidCrystal_I2C *lcd = nullptr;
uint8_t g_lcdI2cAddr = 0;

// Dose button-press timeout
const unsigned long DOSE_RESPONSE_WINDOW_MS = 15000UL; // 15 seconds

// Max rows from dashboard (medicine_entries); all can share the same HH:MM
const int MAX_SCHEDULE_ENTRIES = 24;
// Rotate LCD between medicines when several are due together (16x2 display)
const unsigned long ALARM_LCD_ROTATE_MS = 2500UL;

// -------------------- TYPES & GLOBAL STATE --------------------

struct ScheduleEntry {
  bool enabled = false;
  bool valid = false;
  int hour = 0;
  int minute = 0;
  String medicineName;
  String note;
  String slot; // morning | afternoon | night | custom
  int medicineEntryId = -1;
  int lastTriggeredDay = -1;
};

ScheduleEntry scheduleEntries[MAX_SCHEDULE_ENTRIES];
int scheduleEntryCount = 0;

unsigned long lastScheduleFetchMs = 0;
bool timeInitialized = false;

// -------------------- DEBUG/ALARM STATE (non-blocking) --------------------
// [DEBUG MODE FIX] Schedule match starts alarm state immediately; loop()
// then turns buzzer OFF after 5 seconds and posts taken/missed when button/timeout happens.
bool alarmActive = false;
// When several doses are due at once, all indices are listed; LCD rotates through them.
int alarmGroupCount = 0;
int alarmGroupIndices[MAX_SCHEDULE_ENTRIES];
int alarmLcdShowIdx = 0;
unsigned long alarmLastRotateMs = 0;
bool alarmBuzzerOn = false;
unsigned long alarmBuzzerOffAtMs = 0;
unsigned long alarmResponseEndAtMs = 0;
bool alarmButtonReady = false; // require a HIGH release before accepting press
unsigned long buttonSelftestBeepOffAtMs = 0;
unsigned long alarmBuzzerPatternMs = 0;
unsigned long alarmLcdRefreshMs = 0;
// Button debounce state for non-blocking press detection.
uint32_t buttonLowSinceMs = 0;
bool buttonFireArmed = true;

// -------------------- FORWARD DECLARATIONS --------------------

void initLCD();
void showLCD(const String &line1, const String &line2 = "");
void connectWiFi();
void ensureWiFiConnected();
bool initTimeIfNeeded();
bool getLocalTimeSafe(struct tm *timeInfo);
void printLocalTimeToSerial();
bool fetchSchedule();
bool parseScheduleJson(const String &payload);
bool parseTimeString(const char *timeStr, ScheduleEntry &entry);
void restoreLastTriggeredDay(ScheduleEntry &se, const ScheduleEntry *prev, int prevCount);
void checkAndHandleDoses(const struct tm &nowInfo); // [ADDED/FIXED] use already-fetched time
bool isEntryDue(const ScheduleEntry &entry, const struct tm &nowInfo);
void checkSchedule(const struct tm &nowInfo); // [ADDED] runs once per second
void triggerMultiAlarm(const int *dueIndices, int dueCount, const struct tm &nowInfo);
void processActiveAlarm(const struct tm &nowInfo, unsigned long nowMs);
String formatScheduleHM(const ScheduleEntry &e);
void processButtonSelftest(unsigned long nowMs);
bool waitForButtonPressWithin(unsigned long windowMs);
void triggerBuzzer(bool on);
void scanI2cBusAndPrint();
uint8_t pickLcdI2cAddress();
void runStartupHardwareTest();
bool postDoseStatus(const char *doseName, const char *status, const struct tm &nowInfo,
                    int medicineEntryId = -1, const char *medicineName = nullptr,
                    const char *scheduledTime = nullptr);
String formatTimeForStatus(const struct tm &nowInfo);

// -------------------- SETUP & LOOP --------------------

void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(BUZZER_PIN, OUTPUT);
  triggerBuzzer(false);

  pinMode(BUTTON_PIN, INPUT_PULLUP);

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);

  initLCD();
  showLCD("Booting...", "ESP32 Ayucare");
  Serial.println("Booting ESP32 Ayucare pill box...");
  runStartupHardwareTest();

  connectWiFi();

  if (initTimeIfNeeded()) {
    printLocalTimeToSerial();
  }

  // Initial schedule fetch
  if (fetchSchedule()) {
    showLCD("Schedule loaded", "from server");
  } else {
    showLCD("Sched fetch fail", "Will retry");
  }

  delay(2000);
}

void loop() {
  ensureWiFiConnected();

  const unsigned long nowMs = millis();

  // Initialize time if needed (NTP sync)
  if (!timeInitialized && WiFi.status() == WL_CONNECTED) {
    initTimeIfNeeded();
  }

  // Periodically refresh schedule. (No delay() here.)
  if (nowMs - lastScheduleFetchMs >= SCHEDULE_REFRESH_INTERVAL_MS) {
    if (fetchSchedule()) {
      Serial.println("Schedule refreshed");
    } else {
      Serial.println("Failed to refresh schedule");
    }
    lastScheduleFetchMs = nowMs;
  }

  // Cache local time once per second (required: compare every second)
  static unsigned long lastSecondCheckMs = 0;
  static bool nowValid = false;
  static struct tm latestNowInfo;

  // Wall clock must keep ticking after NTP sync even if WiFi drops briefly — do not gate on WL_CONNECTED.
  if (lastSecondCheckMs == 0 || (nowMs - lastSecondCheckMs) >= 1000UL) {
    lastSecondCheckMs = nowMs;
    struct tm nowInfo;
    if (getLocalTimeSafe(&nowInfo)) {
      latestNowInfo = nowInfo;
      nowValid = true;

      if (!timeInitialized) {
        timeInitialized = true;
        Serial.println("Time synchronized successfully (runtime)");
        showLCD("Time synced", "IST");
      }

      if (!alarmActive) {
        checkSchedule(latestNowInfo);
      }
    } else {
      nowValid = false;
      static unsigned long lastTimeFailLogMs = 0;
      if (nowMs - lastTimeFailLogMs >= 10000UL) {
        lastTimeFailLogMs = nowMs;
        Serial.println("[TIME] getLocalTime() not valid yet — waiting for NTP (WiFi must connect once)");
      }
    }
  }

  // While alarm is active, keep processing (no time re-fetch needed; uses cached latestNowInfo).
  if (alarmActive && nowValid) {
    processActiveAlarm(latestNowInfo, nowMs);
  }
  if (!alarmActive) {
    processButtonSelftest(nowMs);
  }

  yield();
}

// -------------------- LCD FUNCTIONS --------------------

void scanI2cBusAndPrint() {
  Serial.println("[I2C] Scanning 0x08-0x77 ...");
  int found = 0;
  for (uint8_t addr = 0x08; addr <= 0x77; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("  device at 0x%02X\n", addr);
      found++;
    }
  }
  if (found == 0) {
    Serial.println("  (no devices ACK — check SDA/SCL wiring and pull-ups)");
  }
}

uint8_t pickLcdI2cAddress() {
  // HD44780+I2C backpacks usually use a PCF8574 in 0x20-0x27; 0x27 / 0x3F are most common.
  static const uint8_t kCandidates[] = {0x27, 0x3F, 0x38, 0x20, 0x21, 0x22,
                                          0x23, 0x24, 0x25, 0x26};
  for (uint8_t addr : kCandidates) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      return addr;
    }
  }
  return 0;
}

void initLCD() {
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);
  delay(80);

  scanI2cBusAndPrint();
  g_lcdI2cAddr = pickLcdI2cAddress();
  if (g_lcdI2cAddr == 0) {
    Serial.println("[LCD] No device at known LCD addresses — schedule alarms will log only");
    lcd = nullptr;
    return;
  }

  Serial.printf("[LCD] Initializing 16x2 at 0x%02X (SDA=%d SCL=%d)\n", g_lcdI2cAddr, I2C_SDA, I2C_SCL);

  if (lcd != nullptr) {
    delete lcd;
    lcd = nullptr;
  }
  lcd = new LiquidCrystal_I2C(g_lcdI2cAddr, 16, 2);

  // marcoschwartz LiquidCrystal_I2C::init() calls Wire.begin() with no args — can break explicit pins.
  lcd->init();
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);

  lcd->backlight();
  lcd->clear();
  lcd->setCursor(0, 0);
  lcd->print("LCD OK");
  lcd->setCursor(0, 1);
  char addrLine[18];
  snprintf(addrLine, sizeof(addrLine), "0x%02X", g_lcdI2cAddr);
  lcd->print(addrLine);
  Serial.println("[LCD] init complete");
}

void showLCD(const String &line1, const String &line2) {
  if (lcd == nullptr) {
    Serial.printf("[LCD:disabled] %s | %s\n", line1.c_str(), line2.c_str());
    return;
  }
  lcd->clear();
  lcd->setCursor(0, 0);
  lcd->print(line1.substring(0, 16));
  lcd->setCursor(0, 1);
  lcd->print(line2.substring(0, 16));
}

// -------------------- WIFI FUNCTIONS --------------------

void connectWiFi() {
  showLCD("Connecting WiFi", WIFI_SSID);
  Serial.printf("Connecting to WiFi SSID: %s\n", WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  WiFi.disconnect(true, true);
  delay(200);

  const int maxAttempts = 3;
  const unsigned long wifiTimeoutMs = 20000UL; // 20 seconds per attempt

  for (int attempt = 1; attempt <= maxAttempts; attempt++) {
    Serial.printf("[WIFI] Attempt %d/%d\n", attempt, maxAttempts);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    unsigned long startAttemptTime = millis();
    while (WiFi.status() != WL_CONNECTED && (millis() - startAttemptTime) < wifiTimeoutMs) {
      delay(500);
      Serial.print(".");
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
      Serial.print("WiFi connected. IP: ");
      Serial.println(WiFi.localIP());
      showLCD("WiFi connected", WiFi.localIP().toString());
      return;
    }

    wl_status_t st = WiFi.status();
    Serial.printf("[WIFI] Failed, status=%d\n", (int)st);
    if (st == WL_NO_SSID_AVAIL) {
      Serial.println("[WIFI] SSID not found. Use 2.4GHz and verify exact SSID.");
    } else if (st == WL_CONNECT_FAILED || st == WL_DISCONNECTED) {
      Serial.println("[WIFI] Auth/disconnect issue. Recheck password and signal.");
    }

    WiFi.disconnect();
    delay(1000);
  }

  Serial.println("WiFi connection failed after retries");
  showLCD("WiFi failed", "Check SSID/PASS");
}

void ensureWiFiConnected() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi disconnected, reconnecting...");
    connectWiFi();
  }
}

// -------------------- TIME / NTP FUNCTIONS --------------------

bool initTimeIfNeeded() {
  if (timeInitialized) {
    return true;
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Cannot init time: WiFi not connected");
    return false;
  }

  Serial.println("Configuring time via NTP...");
  showLCD("Syncing time", "with NTP");
  configTime(GMT_OFFSET_SEC, DAYLIGHT_OFFSET_S, NTP_SERVER_1, NTP_SERVER_2);

  struct tm timeInfo;
  // Try multiple times to allow NTP sync (may take >10s on some networks)
  for (int i = 0; i < 30; i++) {
    if (getLocalTimeSafe(&timeInfo)) {
      timeInitialized = true;
      Serial.println("Time synchronized successfully");
      showLCD("Time synced", "IST");
      delay(1000);
      return true;
    }
    delay(500);
  }

  Serial.println("Failed to synchronize time");
  showLCD("Time sync fail", "Will retry");
  return false;
}

bool getLocalTimeSafe(struct tm *timeInfo) {
  if (!getLocalTime(timeInfo)) {
    return false;
  }
  return true;
}

void printLocalTimeToSerial() {
  struct tm timeInfo;
  if (getLocalTimeSafe(&timeInfo)) {
    Serial.printf("Current local time: %02d-%02d-%04d %02d:%02d:%02d\n",
                  timeInfo.tm_mday,
                  timeInfo.tm_mon + 1,
                  timeInfo.tm_year + 1900,
                  timeInfo.tm_hour,
                  timeInfo.tm_min,
                  timeInfo.tm_sec);
  }
}

// -------------------- SCHEDULE / HTTP FUNCTIONS --------------------

bool fetchSchedule() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Cannot fetch schedule: WiFi not connected");
    return false;
  }

  // Matches README: GET /api/get_schedule/<box_id>
  String url = String(API_BASE_URL) + "/api/get_schedule/" + BOX_ID;
  Serial.print("Fetching schedule from: ");
  Serial.println(url);

  HTTPClient http;
  http.begin(url);
  int httpCode = http.GET();

  if (httpCode <= 0) {
    Serial.printf("HTTP GET failed: %s\n", http.errorToString(httpCode).c_str());
    http.end();
    return false;
  }

  if (httpCode != HTTP_CODE_OK) {
    Serial.printf("HTTP GET returned code %d\n", httpCode);
    http.end();
    return false;
  }

  String payload = http.getString();
  http.end();

  Serial.println("Schedule response:");
  Serial.println(payload);

  if (!parseScheduleJson(payload)) {
    Serial.println("Failed to parse schedule JSON");
    return false;
  }

  showLCD("Schedule updated", "");
  return true;
}

/*
  Expected JSON format (Ayucare README):

  {
    "success": true,
    "box_id": "BOX123",
    "patient_name": "John Doe",
    "schedule": {
      "morning": true,
      "afternoon": false,
      "night": true
    },
    "times": {
      "morning": "08:00",
      "afternoon": "14:00",
      "night": "20:00"
    }
  }

  We use:
    - schedule.morning/afternoon/night as enable flags
    - times.morning/afternoon/night as "HH:MM" strings in IST
*/
// [FIX] Keep "already triggered today" across HTTP schedule refreshes; otherwise grace window
// retriggers the same alarm every ~10s after the user dismissed it.
void restoreLastTriggeredDay(ScheduleEntry &se, const ScheduleEntry *prev, int prevCount) {
  if (prev == nullptr || prevCount <= 0) return;
  for (int p = 0; p < prevCount; p++) {
    const ScheduleEntry &o = prev[p];
    if (se.hour != o.hour || se.minute != o.minute) continue;
    if (se.medicineEntryId >= 0 && se.medicineEntryId == o.medicineEntryId) {
      se.lastTriggeredDay = o.lastTriggeredDay;
      return;
    }
    if (se.medicineEntryId < 0 && o.medicineEntryId < 0 && se.slot == o.slot) {
      se.lastTriggeredDay = o.lastTriggeredDay;
      return;
    }
  }
}

bool parseScheduleJson(const String &payload) {
  // Large enough for many medicine_entries rows (ArduinoJson 7)
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    Serial.print("JSON parse error: ");
    Serial.println(err.c_str());
    return false;
  }

  JsonObject schedule = doc["schedule"];
  JsonObject times    = doc["times"];
  JsonObject medicines = doc["medicines"];
  JsonArray medicineEntries = doc["medicine_entries"].as<JsonArray>();

  ScheduleEntry prevEntries[MAX_SCHEDULE_ENTRIES];
  int prevCount = scheduleEntryCount;
  for (int i = 0; i < prevCount; i++) {
    prevEntries[i] = scheduleEntries[i];
  }

  scheduleEntryCount = 0;

  // [MULTI-MED] Load every active dashboard row (same time allowed; custom slot included).
  if (!medicineEntries.isNull() && medicineEntries.size() > 0) {
    for (JsonObject entry : medicineEntries) {
      if (scheduleEntryCount >= MAX_SCHEDULE_ENTRIES) {
        Serial.println("[WARN] MAX_SCHEDULE_ENTRIES reached; ignoring extra rows");
        break;
      }
      ScheduleEntry se;
      se.enabled = true;
      se.slot = String((const char *)(entry["slot"] | "custom"));
      se.medicineName = String((const char *)(entry["medicine_name"] | ""));
      se.note = String((const char *)(entry["note"] | ""));
      se.medicineEntryId = entry["id"].is<int>() ? entry["id"].as<int>() : -1;
      const char *doseTime = entry["dose_time"];
      if (!doseTime) doseTime = "";
      se.valid = parseTimeString(doseTime, se);
      if (!se.valid) {
        Serial.printf("[WARN] Skip entry id=%d (bad time)\n", se.medicineEntryId);
        continue;
      }
      restoreLastTriggeredDay(se, prevEntries, prevCount);
      scheduleEntries[scheduleEntryCount++] = se;
    }
  }

  const bool usedMedicineEntries = scheduleEntryCount > 0;

  if (usedMedicineEntries) {
    Serial.println("Parsed schedule source: medicine_entries (all rows)");
  } else {
    Serial.println("Parsed schedule source: legacy schedule/times");
  }

  // Fallback: legacy schedule/times (for backward compatibility).
  if (!usedMedicineEntries) {
    auto addLegacy = [&](const char *slotKey, const char *medKey) {
      if (scheduleEntryCount >= MAX_SCHEDULE_ENTRIES) return;
      if (schedule.isNull() || times.isNull() || times[slotKey].isNull()) return;
      ScheduleEntry se;
      se.slot = String(slotKey);
      se.enabled = schedule[slotKey] | false;
      const char *timeStr = times[slotKey];
      se.medicineName = String(medicines[medKey] ? medicines[medKey].as<const char *>() : "");
      se.note = "";
      se.medicineEntryId = -1;
      se.valid = timeStr ? parseTimeString(timeStr, se) : false;
      if (se.valid) {
        restoreLastTriggeredDay(se, prevEntries, prevCount);
        scheduleEntries[scheduleEntryCount++] = se;
      }
    };
    addLegacy("morning", "morning");
    addLegacy("afternoon", "afternoon");
    addLegacy("night", "night");
  }

  Serial.printf("Parsed schedule: %d entr%s\n", scheduleEntryCount, scheduleEntryCount == 1 ? "y" : "ies");
  for (int i = 0; i < scheduleEntryCount; i++) {
    ScheduleEntry &e = scheduleEntries[i];
    if (e.enabled && e.valid) {
      Serial.printf("  [%d] %s %02d:%02d | %s (id=%d)\n",
                    i, e.slot.c_str(), e.hour, e.minute,
                    e.medicineName.c_str(), e.medicineEntryId);
    } else {
      Serial.printf("  [%d] %s (disabled/invalid)\n", i, e.slot.c_str());
    }
  }

  return true;
}

bool parseTimeString(const char *timeStr, ScheduleEntry &dose) {
  if (!timeStr) return false;

  String t = String(timeStr);
  t.trim();
  if (t.length() < 4) return false;

  // Accept both 24h ("14:30") and 12h ("02:30 PM").
  t.toUpperCase();
  bool isAM = t.endsWith("AM");
  bool isPM = t.endsWith("PM");
  if (isAM || isPM) {
    t.remove(t.length() - 2);
    t.trim();
  }

  int h = 0, m = 0;
  if (sscanf(t.c_str(), "%d:%d", &h, &m) != 2) {
    return false;
  }

  if (isAM || isPM) {
    if (h < 1 || h > 12 || m < 0 || m > 59) return false;
    if (isAM) {
      h = (h == 12) ? 0 : h;
    } else {
      h = (h == 12) ? 12 : (h + 12);
    }
  } else {
    if (h < 0 || h > 23 || m < 0 || m > 59) return false;
  }

  // If schedule time changed from dashboard, allow a new trigger today.
  if (dose.hour != h || dose.minute != m) {
    dose.lastTriggeredDay = -1;
    Serial.printf("[SCHED_CHANGE] Time updated to %02d:%02d, reset trigger lock\n", h, m);
  }

  dose.hour = h;
  dose.minute = m;
  return true;
}

// -------------------- DOSE LOGIC --------------------

void checkAndHandleDoses(const struct tm &nowInfo) {
  // [ADDED: debug] Print time + schedule info once per minute (prevents log spam).
  static int lastDebugMinuteOfDay = -1;
  static int lastDebugDayOfMonth = -1;

  int minuteOfDay = nowInfo.tm_hour * 60 + nowInfo.tm_min;
  if (lastDebugMinuteOfDay != minuteOfDay || lastDebugDayOfMonth != nowInfo.tm_mday) {
    lastDebugMinuteOfDay = minuteOfDay;
    lastDebugDayOfMonth = nowInfo.tm_mday;

    Serial.printf("[TIME] %02d:%02d (day=%d) IST\n", nowInfo.tm_hour, nowInfo.tm_min, nowInfo.tm_mday);
    Serial.printf("[SCHED] loaded entries=%d\n", scheduleEntryCount);
  }

  int dueIndices[MAX_SCHEDULE_ENTRIES];
  int dueCount = 0;
  for (int i = 0; i < scheduleEntryCount; i++) {
    ScheduleEntry &e = scheduleEntries[i];
    if (e.enabled && e.valid && isEntryDue(e, nowInfo)) {
      dueIndices[dueCount++] = i;
      Serial.printf("[MATCH] due idx=%d %s %02d:%02d %s\n",
                    i, e.slot.c_str(), e.hour, e.minute, e.medicineName.c_str());
    }
  }

  if (dueCount > 0) {
    triggerMultiAlarm(dueIndices, dueCount, nowInfo);
  }
}

// [ADDED] Required function: called once per second from loop()
void checkSchedule(const struct tm &nowInfo) {
  static int lastHeartbeatKey = -1;
  int key = nowInfo.tm_mday * 1440 + nowInfo.tm_hour * 60 + nowInfo.tm_min;
  if (key != lastHeartbeatKey) {
    lastHeartbeatKey = key;
    Serial.printf("[SCHED_TICK] %02d:%02d:%02d entries=%d lcd=%s\n",
                  nowInfo.tm_hour, nowInfo.tm_min, nowInfo.tm_sec, scheduleEntryCount,
                  lcd ? "ok" : "off");
  }
  checkAndHandleDoses(nowInfo);
}

bool isEntryDue(const ScheduleEntry &dose, const struct tm &nowInfo) {
  // Avoid multiple triggers on same day
  if (dose.lastTriggeredDay == nowInfo.tm_mday) {
    return false;
  }

  // Exact HH:MM match
  if (nowInfo.tm_hour == dose.hour && nowInfo.tm_min == dose.minute) {
    return true;
  }

  // [FIXED] Small grace window (up to 2 minutes late) to avoid missing alarms
  // when schedule updates arrive slightly after the exact minute.
  const int nowSec = nowInfo.tm_hour * 3600 + nowInfo.tm_min * 60 + nowInfo.tm_sec;
  const int dueSec = dose.hour * 3600 + dose.minute * 60;
  const int delta = nowSec - dueSec;
  if (delta > 0 && delta <= 120) {
    return true;
  }

  return false;
}

// [MULTI-MED] One alarm cycle for every dose due this minute (same buzzer/LCD window).
void triggerMultiAlarm(const int *dueIndices, int dueCount, const struct tm &nowInfo) {
  if (alarmActive) {
    Serial.println("[ALARM] Ignored batch start (alarm already active)");
    return;
  }
  if (dueCount <= 0 || dueIndices == nullptr) return;

  for (int i = 0; i < dueCount; i++) {
    scheduleEntries[dueIndices[i]].lastTriggeredDay = nowInfo.tm_mday;
  }

  alarmGroupCount = dueCount;
  for (int i = 0; i < dueCount; i++) {
    alarmGroupIndices[i] = dueIndices[i];
  }
  alarmLcdShowIdx = 0;
  alarmLastRotateMs = millis();

  Serial.printf("[ALARM_START] batch count=%d at %02d:%02d:%02d\n",
                dueCount, nowInfo.tm_hour, nowInfo.tm_min, nowInfo.tm_sec);
  for (int i = 0; i < dueCount; i++) {
    ScheduleEntry &e = scheduleEntries[dueIndices[i]];
    Serial.printf("       [%d] %s | %s\n", dueIndices[i], e.slot.c_str(), e.medicineName.c_str());
  }

  alarmActive = true;

  buttonLowSinceMs = 0;
  buttonFireArmed = true;

  alarmBuzzerOn = true;
  alarmBuzzerOffAtMs = millis() + MEDICINE_BUZZER_ON_MS;
  alarmResponseEndAtMs = millis() + DOSE_RESPONSE_WINDOW_MS;
  alarmButtonReady = false;
  alarmBuzzerPatternMs = millis();
  alarmLcdRefreshMs = 0;

  ScheduleEntry &first = scheduleEntries[dueIndices[0]];
  String line1 = dueCount > 1 ? String("Take: ") + dueCount + " meds" : String("Take Medicine");
  String line2 = first.medicineName.length() ? first.medicineName : first.slot;
  Serial.printf("[TRIGGER_HW] LCD + buzzer GPIO%d active_high=%d\n", BUZZER_PIN, BUZZER_ACTIVE_HIGH ? 1 : 0);
  showLCD(line1, line2);
  triggerBuzzer(true);
}

void processActiveAlarm(const struct tm &nowInfo, unsigned long nowMs) {
  if (!alarmActive) return;

  if (alarmGroupCount <= 0) {
    alarmActive = false;
    return;
  }

  // Enforce max buzzer-on duration (was set but never applied before).
  if (alarmBuzzerOn && nowMs >= alarmBuzzerOffAtMs) {
    alarmBuzzerOn = false;
    triggerBuzzer(false);
    Serial.printf("[ALARM] Buzzer stopped after %lu ms (reminder stays on LCD)\n", MEDICINE_BUZZER_ON_MS);
  }

  // Rotate which medicine is highlighted on the 16x2 LCD when several are due together.
  if (alarmGroupCount > 1 && (nowMs - alarmLastRotateMs) >= ALARM_LCD_ROTATE_MS) {
    alarmLastRotateMs = nowMs;
    alarmLcdShowIdx = (alarmLcdShowIdx + 1) % alarmGroupCount;
  }

  ScheduleEntry &cur = scheduleEntries[alarmGroupIndices[alarmLcdShowIdx]];
  const char *slotDbg = cur.slot.length() ? cur.slot.c_str() : "dose";

  // Non-blocking stable press detection (debounce without delay()).
  const bool buttonLow = (digitalRead(BUTTON_PIN) == LOW);

  // Require one released (HIGH) state after alarm starts.
  if (!buttonLow) {
    alarmButtonReady = true;
  }

  if (alarmButtonReady && buttonLow) {
    if (buttonLowSinceMs == 0) buttonLowSinceMs = nowMs;
    if (buttonFireArmed && (nowMs - buttonLowSinceMs) >= 30UL) {
      buttonFireArmed = false;
      // Stop buzzer immediately when button pressed.
      if (alarmBuzzerOn) {
        triggerBuzzer(false);
        alarmBuzzerOn = false;
        Serial.printf("[BUTTON] %s pressed -> buzzer OFF (batch=%d)\n", slotDbg, alarmGroupCount);
      }
      Serial.println("[TAKEN] batch confirmed");
      if (alarmGroupCount > 1) {
        showLCD("Dose taken", String("All ") + alarmGroupCount + " logged");
      } else {
        showLCD("Dose taken", cur.medicineName.length() ? cur.medicineName : cur.slot);
      }
      for (int i = 0; i < alarmGroupCount; i++) {
        ScheduleEntry &e = scheduleEntries[alarmGroupIndices[i]];
        const char *slot = e.slot.length() ? e.slot.c_str() : "custom";
        String hm = formatScheduleHM(e);
        postDoseStatus(slot, "taken", nowInfo, e.medicineEntryId,
                       e.medicineName.length() ? e.medicineName.c_str() : nullptr,
                       hm.c_str());
      }
      alarmActive = false;
      alarmGroupCount = 0;
      showLCD("Next dose waiting", "");
      return;
    }
  } else {
    buttonLowSinceMs = 0;
    buttonFireArmed = true;
  }

  // Beep pattern while buzzer phase is active (250ms ON / 350ms OFF).
  if (alarmBuzzerOn) {
    const unsigned long elapsed = nowMs - alarmBuzzerPatternMs;
    const bool buzzerHighPhase = (elapsed % 600UL) < 250UL;
    triggerBuzzer(buzzerHighPhase);
  }

  // Keep LCD updated for the full response window (not only while buzzer is on).
  if (alarmLcdRefreshMs == 0 || (nowMs - alarmLcdRefreshMs) >= 1000UL) {
    String line1 = alarmGroupCount > 1
                       ? (String("Med ") + (alarmLcdShowIdx + 1) + "/" + alarmGroupCount)
                       : String("Take Medicine");
    String line2 = cur.medicineName.length() ? cur.medicineName : cur.slot;
    if (cur.note.length() > 0) {
      bool showNote = ((nowMs / 1000UL) % 2UL) == 1UL;
      if (showNote) {
        line2 = "Note: " + cur.note;
      }
    }
    showLCD(line1, line2);
    alarmLcdRefreshMs = nowMs;
  }

  // If no button press within response window: mark missed.
  if (nowMs >= alarmResponseEndAtMs) {
    if (alarmBuzzerOn) {
      triggerBuzzer(false);
      alarmBuzzerOn = false;
    }
    Serial.printf("[MISSED] timeout batch=%d\n", alarmGroupCount);
    if (alarmGroupCount > 1) {
      showLCD("Dose missed", String("All ") + alarmGroupCount + " logged");
    } else {
      showLCD("Dose missed", cur.medicineName.length() ? cur.medicineName : cur.slot);
    }
    for (int i = 0; i < alarmGroupCount; i++) {
      ScheduleEntry &e = scheduleEntries[alarmGroupIndices[i]];
      const char *slot = e.slot.length() ? e.slot.c_str() : "custom";
      String hm = formatScheduleHM(e);
      postDoseStatus(slot, "missed", nowInfo, e.medicineEntryId,
                     e.medicineName.length() ? e.medicineName.c_str() : nullptr,
                     hm.c_str());
    }
    alarmActive = false;
    alarmGroupCount = 0;
    showLCD("Next dose waiting", "");
  }
}

void processButtonSelftest(unsigned long nowMs) {
  if (!ENABLE_BUTTON_SELFTEST) return;

  // Auto-stop short debug beep.
  if (buttonSelftestBeepOffAtMs != 0 && nowMs >= buttonSelftestBeepOffAtMs) {
    triggerBuzzer(false);
    buttonSelftestBeepOffAtMs = 0;
  }

  // Detect stable button press in idle mode.
  const bool buttonLow = (digitalRead(BUTTON_PIN) == LOW);
  if (buttonLow) {
    if (buttonLowSinceMs == 0) buttonLowSinceMs = nowMs;
    if (buttonFireArmed && (nowMs - buttonLowSinceMs) >= 30UL) {
      buttonFireArmed = false;
      Serial.println("[BTN_TEST] Button press detected");
      showLCD("Button OK", "Switch detected");
      triggerBuzzer(true);
      buttonSelftestBeepOffAtMs = nowMs + 120UL; // short confirmation beep
    }
  } else {
    buttonLowSinceMs = 0;
    buttonFireArmed = true;
  }
}

// Wait for a LOW (pressed) on the button within a small window (ms).
// Returns true if a press was detected.
bool waitForButtonPressWithin(unsigned long windowMs) {
  unsigned long start = millis();
  while (millis() - start < windowMs) {
    int state = digitalRead(BUTTON_PIN);
    if (state == LOW) {
      // Basic debounce
      delay(50);
      if (digitalRead(BUTTON_PIN) == LOW) {
        // Wait until release to avoid double-count
        while (digitalRead(BUTTON_PIN) == LOW) {
          delay(10);
        }
        return true;
      }
    }
    delay(5);
  }
  return false;
}

// -------------------- BUZZER & STATUS POST FUNCTIONS --------------------

void triggerBuzzer(bool on) {
  const bool levelHigh = BUZZER_ACTIVE_HIGH ? on : !on;
  digitalWrite(BUZZER_PIN, levelHigh ? HIGH : LOW);
}

void runStartupHardwareTest() {
  Serial.println("[HW] Startup test: LCD + buzzer");
  showLCD("Hardware test", "Buzzer check");

  triggerBuzzer(true);
  delay(250);
  triggerBuzzer(false);
  delay(150);
  triggerBuzzer(true);
  delay(250);
  triggerBuzzer(false);

  showLCD("Boot complete", "Waiting sched");
  Serial.println("[HW] Startup test done");
}

/*
  POST /api/update_status

  Ayucare README expects JSON:

  {
    "box_id": "BOX123",
    "dose_time": "morning",
    "status": "taken"
  }

  We also log a local timestamp string for debugging, but server does not require it.
*/
bool postDoseStatus(const char *doseName, const char *status, const struct tm &nowInfo,
                    int medicineEntryId, const char *medicineName, const char *scheduledTime) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Cannot POST status: WiFi not connected");
    return false;
  }

  String url = String(API_BASE_URL) + "/api/update_status";
  HTTPClient http;
  http.begin(url);
  http.addHeader("Content-Type", "application/json");

  JsonDocument doc;
  doc["box_id"] = BOX_ID;
  doc["dose_time"] = doseName;
  doc["status"] = status;
  doc["time"] = formatTimeForStatus(nowInfo);
  if (medicineEntryId >= 0) {
    doc["medicine_entry_id"] = medicineEntryId;
  }
  if (medicineName != nullptr && strlen(medicineName) > 0) {
    doc["medicine_name"] = medicineName;
  }
  if (scheduledTime != nullptr && strlen(scheduledTime) > 0) {
    doc["scheduled_time"] = scheduledTime;
  }

  String body;
  serializeJson(doc, body);

  Serial.print("POST ");
  Serial.println(url);
  Serial.print("Payload: ");
  Serial.println(body);

  int httpCode = http.POST(body);
  if (httpCode <= 0) {
    Serial.printf("HTTP POST failed: %s\n", http.errorToString(httpCode).c_str());
    http.end();
    return false;
  }

  String resp = http.getString();
  http.end();

  Serial.printf("Status POST code: %d\n", httpCode);
  Serial.println("Response:");
  Serial.println(resp);

  if (httpCode == HTTP_CODE_OK || httpCode == HTTP_CODE_CREATED) {
    showLCD("Status sent", status);
    return true;
  } else {
    showLCD("Status send err", String(httpCode));
    return false;
  }
}

String formatTimeForStatus(const struct tm &nowInfo) {
  char buf[20];
  // "YYYY-MM-DD HH:MM:SS"
  snprintf(buf, sizeof(buf), "%04d-%02d-%02d %02d:%02d:%02d",
           nowInfo.tm_year + 1900,
           nowInfo.tm_mon + 1,
           nowInfo.tm_mday,
           nowInfo.tm_hour,
           nowInfo.tm_min,
           nowInfo.tm_sec);
  return String(buf);
}

String formatScheduleHM(const ScheduleEntry &e) {
  char buf[8];
  snprintf(buf, sizeof(buf), "%02d:%02d", e.hour, e.minute);
  return String(buf);
}

