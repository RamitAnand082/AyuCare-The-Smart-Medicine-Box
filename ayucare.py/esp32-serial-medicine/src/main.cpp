/*
 * ESP32 Serial Medicine Box
 *
 * - Buzzer on GPIO 25: turns ON when "START_ALARM" received, OFF on "STOP_ALARM".
 * - LCD I2C (GPIO 21=SDA, 22=SCL): shows alarm message when START_ALARM, clears on STOP_ALARM.
 * - Rocker switch on GPIO 13 (INPUT_PULLUP): when pressed, sends "SWITCH_PRESSED" over Serial.
 *
 * Protocol (115200 baud, newline-terminated):
 *   PC -> ESP32: START_ALARM                  -> buzzer on, LCD generic message
 *   PC -> ESP32: START_ALARM|Name|HH:MM|Slot  -> buzzer on, LCD shows details
 *   PC -> ESP32: STOP_ALARM   -> buzzer off, LCD "OK"
 *   ESP32 -> PC: SWITCH_PRESSED (when user presses the switch)
 */

#include <Arduino.h>
#include <LiquidCrystal_I2C.h>

static const uint8_t BUZZER_PIN = 25;
static const uint8_t SWITCH_PIN = 13;

// I2C LCD: 16x2, address 0x27 (use 0x3F if your module uses that)
LiquidCrystal_I2C lcd(0x27, 16, 2);

static const unsigned long DEBOUNCE_MS = 80;
static const size_t CMD_BUF_SIZE = 64;

char cmdBuf[CMD_BUF_SIZE];
size_t cmdLen = 0;
bool alarmOn = false;

// Switch: track state for edge detection and debounce
bool lastSwitchState = HIGH;
unsigned long lastSwitchChangeMs = 0;

void setBuzzer(bool on) {
  digitalWrite(BUZZER_PIN, on ? HIGH : LOW);
  alarmOn = on;
}

static String lastName = "";
static String lastTime = "";
static String lastSlot = "";

void setLcdAlarm(bool alarm) {
  lcd.clear();
  if (alarm) {
    lcd.setCursor(0, 0);
    if (lastName.length() > 0) {
      // Line 1: medicine (trim to 16 chars)
      lcd.print(lastName.substring(0, 16));
    } else {
      lcd.print("ALARM!");
    }
    lcd.setCursor(0, 1);
    if (lastTime.length() > 0 || lastSlot.length() > 0) {
      String line2 = "";
      if (lastSlot.length() > 0) {
        line2 += lastSlot;
        line2 += " ";
      }
      if (lastTime.length() > 0) {
        line2 += lastTime;
      }
      if (line2.length() == 0) line2 = "Take medicine";
      lcd.print(line2.substring(0, 16));
    } else {
      lcd.print("Take medicine");
    }
  } else {
    lcd.setCursor(0, 0);
    lcd.print("OK");
  }
}

void processCommand() {
  cmdBuf[cmdLen] = '\0';
  String s = String(cmdBuf);
  s.trim();

  if (s == "START_ALARM" || s.startsWith("START_ALARM|")) {
    // Optional payload: START_ALARM|Name|HH:MM|Slot
    lastName = "";
    lastTime = "";
    lastSlot = "";
    if (s.startsWith("START_ALARM|")) {
      int p1 = s.indexOf('|');             // after START_ALARM
      int p2 = s.indexOf('|', p1 + 1);     // after Name
      int p3 = s.indexOf('|', p2 + 1);     // after HH:MM
      if (p1 >= 0) {
        if (p2 > p1) lastName = s.substring(p1 + 1, p2);
        if (p3 > p2) {
          lastTime = s.substring(p2 + 1, p3);
          lastSlot = s.substring(p3 + 1);
        } else if (p2 > p1) {
          lastTime = s.substring(p2 + 1);
        }
      }
      lastName.trim();
      lastTime.trim();
      lastSlot.trim();
      // Capitalize slot for display
      if (lastSlot.length() > 0) {
        lastSlot.toLowerCase();
        lastSlot.setCharAt(0, (char)toupper(lastSlot[0]));
      }
    }
    setBuzzer(true);
    setLcdAlarm(true);
    Serial.println("[ESP32] Alarm ON");
  } else if (s == "STOP_ALARM") {
    setBuzzer(false);
    setLcdAlarm(false);
    Serial.println("[ESP32] Alarm OFF");
  }

  cmdLen = 0;
}

void setup() {
  Serial.begin(115200);
  delay(500);

  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);

  pinMode(SWITCH_PIN, INPUT_PULLUP);
  lastSwitchState = digitalRead(SWITCH_PIN);

  lcd.init();
  lcd.backlight();
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Ready");

  Serial.println("ESP32 Serial Medicine Box ready. Send START_ALARM / STOP_ALARM.");
}

void loop() {
  // --- Read Serial commands from PC ---
  while (Serial.available() > 0 && cmdLen < CMD_BUF_SIZE - 1) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdLen > 0) {
        processCommand();
      }
      cmdLen = 0;
      continue;
    }
    cmdBuf[cmdLen++] = c;
  }
  if (cmdLen >= CMD_BUF_SIZE - 1) {
    cmdLen = 0;
  }

  // --- Rocker switch: on press (LOW), send SWITCH_PRESSED once (debounced) ---
  bool sw = digitalRead(SWITCH_PIN);
  unsigned long now = millis();

  static bool switchSentThisPress = false;

  if (sw == HIGH) {
    lastSwitchState = HIGH;
    switchSentThisPress = false;  // allow next press to send again
  } else {
    // sw == LOW (pressed)
    if (lastSwitchState == HIGH) {
      lastSwitchChangeMs = now;
      lastSwitchState = LOW;
    }
    if ((now - lastSwitchChangeMs) >= DEBOUNCE_MS && !switchSentThisPress) {
      Serial.println("SWITCH_PRESSED");
      switchSentThisPress = true;
    }
  }

  delay(10);
}
