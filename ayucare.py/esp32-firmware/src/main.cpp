#include <Arduino.h>

// Simple skeleton firmware for the Smart Medicine Box (ESP32).
// This works with the Ayucare Python project + medicine_box_monitor.py.
//
// It listens for schedule text over serial:
//
//   SCHEDULE_START
//   CLOCK=2026-02-24 21:25:00
//   MORNING=1,19:15,YourMedicine
//   AFTERNOON=0,14:00,
//   NIGHT=0,20:00,
//   SCHEDULE_END
//
// For now this example parses and prints what it receives, so you can
// extend it to drive your buzzer / LEDs and real-time clock.

struct DoseSlotConfig {
  bool enabled = false;
  int hour = 8;
  int minute = 0;
  String medicine;
};

DoseSlotConfig morningCfg;
DoseSlotConfig afternoonCfg;
DoseSlotConfig nightCfg;

// Hardware pins (per your wiring)
static const int SWITCH_PIN = 13;  // rocker switch to GND (INPUT_PULLUP)
static const int BUZZER_PIN = 14;  // active buzzer + to GPIO14, - to GND

// Alarm behavior
static const unsigned long ALARM_WINDOW_MS = 25000;  // 25 seconds to confirm

// Simple wall-clock synced from PC via CLOCK= line
struct ClockState {
  bool synced = false;
  uint32_t baseMillis = 0;
  int baseSeconds = 0;   // seconds since midnight at baseMillis
  int baseDayId = 0;     // monotonically increasing day id
};

ClockState clockState;

// Per-slot "already rang today" marker
int lastAlarmDay_morning = -1;
int lastAlarmDay_afternoon = -1;
int lastAlarmDay_night = -1;

// Active alarm state
bool alarmActive = false;
int alarmSlot = 0;  // 1=morning, 2=afternoon, 3=night
unsigned long alarmStartedAtMs = 0;
bool lastSwitchState = HIGH;

String bufferLine;

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println();
  Serial.println(F("======================================"));
  Serial.println(F("  SMART MEDICINE BOX ESP32 (PlatformIO)"));
  Serial.println(F("======================================"));
  Serial.println(F("Waiting for schedule from PC..."));

  pinMode(SWITCH_PIN, INPUT_PULLUP);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
}

// Day id (days since 2000-01-01) using a civil-date-to-days algorithm.
// daysFromCivilRaw(2000,1,1) = 730425 for this algorithm form.
static int daysFromCivil2000_01_01() { return 730425; }
static int daysFromCivilRaw(int y, unsigned m, unsigned d) {
  y -= m <= 2;
  const int era = (y >= 0 ? y : y - 399) / 400;
  const unsigned yoe = (unsigned)(y - era * 400);
  const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + (int)doe;
}
static int dayIdFromYMD(int y, unsigned m, unsigned d) {
  return daysFromCivilRaw(y, m, d) - daysFromCivil2000_01_01();
}

static bool parseClockLine(const String &line) {
  // CLOCK=YYYY-MM-DD HH:MM:SS
  if (!line.startsWith(F("CLOCK="))) return false;
  String v = line.substring(6);
  v.trim();
  if (v.length() < 19) return false;

  int y = v.substring(0, 4).toInt();
  int mon = v.substring(5, 7).toInt();
  int day = v.substring(8, 10).toInt();
  int hh = v.substring(11, 13).toInt();
  int mm = v.substring(14, 16).toInt();
  int ss = v.substring(17, 19).toInt();

  if (mon < 1 || mon > 12 || day < 1 || day > 31 || hh < 0 || hh > 23 || mm < 0 || mm > 59 || ss < 0 || ss > 59) {
    return false;
  }

  clockState.baseDayId = dayIdFromYMD(y, (unsigned)mon, (unsigned)day);
  clockState.baseSeconds = hh * 3600 + mm * 60 + ss;
  clockState.baseMillis = millis();
  clockState.synced = true;

  Serial.print(F("[CLOCK] synced to "));
  Serial.println(v);
  return true;
}

static void getCurrentClock(int &dayIdOut, int &secondsOut) {
  if (!clockState.synced) {
    dayIdOut = 0;
    secondsOut = 0;
    return;
  }
  uint32_t elapsed = (millis() - clockState.baseMillis) / 1000;
  uint32_t total = (uint32_t)clockState.baseSeconds + elapsed;
  dayIdOut = clockState.baseDayId + (int)(total / 86400UL);
  secondsOut = (int)(total % 86400UL);
}

static void buzzerOn() { digitalWrite(BUZZER_PIN, HIGH); }
static void buzzerOff() { digitalWrite(BUZZER_PIN, LOW); }

static int slotSeconds(const DoseSlotConfig &cfg) { return cfg.hour * 3600 + cfg.minute * 60; }

void parseSlotLine(const String &line, const char *slotName, DoseSlotConfig &cfg) {
  // Expected format: SLOT=1,HH:MM,MedicineName
  // Example: MORNING=1,19:15,Aspirin
  const int prefixLen = strlen(slotName) + 1;  // slot + '='
  if (!line.startsWith(slotName) || line.length() <= prefixLen) {
    return;
  }

  String payload = line.substring(prefixLen);  // e.g. "1,19:15,Aspirin"

  int firstComma = payload.indexOf(',');
  int secondComma = payload.indexOf(',', firstComma + 1);

  if (firstComma == -1) {
    return;
  }

  String enabledStr = payload.substring(0, firstComma);
  String timeStr;
  String medStr;

  if (secondComma == -1) {
    timeStr = payload.substring(firstComma + 1);
    medStr = "";
  } else {
    timeStr = payload.substring(firstComma + 1, secondComma);
    medStr = payload.substring(secondComma + 1);
  }

  enabledStr.trim();
  timeStr.trim();
  medStr.trim();

  cfg.enabled = (enabledStr.toInt() != 0);

  if (timeStr.length() >= 4) {
    int colonPos = timeStr.indexOf(':');
    if (colonPos > 0) {
      cfg.hour = timeStr.substring(0, colonPos).toInt();
      cfg.minute = timeStr.substring(colonPos + 1).toInt();
    }
  }

  cfg.medicine = medStr;

  Serial.print(F("[PARSED] "));
  Serial.print(slotName);
  Serial.print(F(" enabled="));
  Serial.print(cfg.enabled ? F("1") : F("0"));
  Serial.print(F(" time="));
  if (cfg.hour < 10) Serial.print('0');
  Serial.print(cfg.hour);
  Serial.print(':');
  if (cfg.minute < 10) Serial.print('0');
  Serial.print(cfg.minute);
  Serial.print(F(" med=\""));
  Serial.print(cfg.medicine);
  Serial.println('\"');
}

void processScheduleLine(const String &line) {
  // Example lines:
  //   MORNING=1,19:15,YourMedicine
  //   AFTERNOON=0,14:00,
  //   NIGHT=1,20:00,Vitamin D
  Serial.print(F("[SCHEDULE] "));
  Serial.println(line);

  if (parseClockLine(line)) {
    return;
  }

  if (line.startsWith(F("MORNING="))) {
    parseSlotLine(line, "MORNING", morningCfg);
  } else if (line.startsWith(F("AFTERNOON="))) {
    parseSlotLine(line, "AFTERNOON", afternoonCfg);
  } else if (line.startsWith(F("NIGHT="))) {
    parseSlotLine(line, "NIGHT", nightCfg);
  }
}

void loop() {
  static bool inSchedule = false;

  while (Serial.available() > 0) {
    char c = Serial.read();

    if (c == '\n' || c == '\r') {
      if (bufferLine.length() == 0) {
        continue;
      }

      if (bufferLine == "SCHEDULE_START") {
        inSchedule = true;
        Serial.println(F("[INFO] SCHEDULE_START received"));
      } else if (bufferLine == "SCHEDULE_END") {
        inSchedule = false;
        Serial.println(F("[INFO] SCHEDULE_END received"));
        Serial.println(F("[INFO] You can now use the parsed times to drive alarms."));
      } else if (inSchedule) {
        processScheduleLine(bufferLine);
      } else {
        // Other messages from PC / monitor script
        Serial.print(F("[PC] "));
        Serial.println(bufferLine);
      }

      bufferLine = "";
    } else {
      bufferLine += c;
    }
  }

  // Alarm logic: ring exactly at the scheduled HH:MM (from Ayucare),
  // give patient 25 seconds to confirm by pressing the switch.
  int dayId = 0;
  int secs = 0;
  getCurrentClock(dayId, secs);

  // Edge detect switch
  bool sw = digitalRead(SWITCH_PIN);
  bool pressedEdge = (lastSwitchState == HIGH && sw == LOW);
  lastSwitchState = sw;

  if (alarmActive) {
    if (pressedEdge) {
      // Confirmed by patient
      buzzerOff();
      alarmActive = false;
      Serial.println(F("--------------------------------------"));
      if (alarmSlot == 1) Serial.println(F(">>> SUCCESS: Dose #1 confirmed by patient."));
      else if (alarmSlot == 2) Serial.println(F(">>> SUCCESS: Dose #2 confirmed by patient."));
      else Serial.println(F(">>> SUCCESS: Dose #3 confirmed by patient."));
      Serial.println(F("--------------------------------------"));
    } else if (millis() - alarmStartedAtMs >= ALARM_WINDOW_MS) {
      // Timeout, stop alarm (server will mark missed if no TAKEN log arrives)
      buzzerOff();
      alarmActive = false;
    }
    return;
  }

  if (!clockState.synced) {
    // Can't schedule alarms until CLOCK= sync received from PC
    return;
  }

  // Allow a small window (2 seconds) to avoid missing the exact second.
  auto tryTrigger = [&](const DoseSlotConfig &cfg, int slotSec, int &lastDayRef, int slotNum) {
    if (!cfg.enabled) return;
    if (lastDayRef == dayId) return;
    if (secs >= slotSec && secs < slotSec + 2) {
      lastDayRef = dayId;
      alarmActive = true;
      alarmSlot = slotNum;
    }
  };

  tryTrigger(morningCfg, slotSeconds(morningCfg), lastAlarmDay_morning, 1);
  if (!alarmActive) tryTrigger(afternoonCfg, slotSeconds(afternoonCfg), lastAlarmDay_afternoon, 2);
  if (!alarmActive) tryTrigger(nightCfg, slotSeconds(nightCfg), lastAlarmDay_night, 3);

  if (alarmActive) {
    alarmStartedAtMs = millis();
    buzzerOn();
    Serial.println(F(".\n[!] NOTIFICATION: Medicine Time!\r"));
  }
}

