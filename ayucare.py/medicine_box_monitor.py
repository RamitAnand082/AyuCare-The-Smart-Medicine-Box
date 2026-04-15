"""
Smart Medicine Box – Desktop monitor.

Listens to ESP32 over USB serial. When the hardware sends 'PATIENT_TOOK_MEDS',
shows a desktop notification, logs to medicine_log.csv, and prints a confirmation.

Usage:
    python medicine_box_monitor.py

Close the Serial Monitor in VS Code/PlatformIO before running; one port can't
be used by two programs at once.
"""

import csv
import json
import sys
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("Error: pyserial is not installed. Run: pip install pyserial")
    sys.exit(1)

try:
    from plyer import notification
except ImportError:
    print("Error: plyer is not installed. Run: pip install plyer")
    sys.exit(1)


# ----- Configuration -----
DEFAULT_PORT = "COM8"
BAUD_RATE = 115200
# Trigger when ESP32 sends this exact string (e.g. "succes dose one" or "PATIENT_TOOK_MEDS")
TRIGGER_STRING = "succes dose one"
LOG_FILE = "medicine_log.csv"
ERROR_LOG_FILE = "medicine_box_last_error.txt"
VERBOSE = False  # set True to print every line received from ESP32

# Map ESP32 message to API dose_time (for Ayucare dashboard)
# Multiple variants: "succes dose one", "Dose #1 confirmed", etc.
DOSE_MAP = [
    ("succes dose one", "morning"),
    ("success dose one", "morning"),
    ("dose one", "morning"),
    ("dose 1", "morning"),
    ("dose #1", "morning"),
    ("dose #2", "afternoon"),
    ("dose #3", "night"),
    ("succes dose two", "afternoon"),
    ("success dose two", "afternoon"),
    ("dose two", "afternoon"),
    ("dose 2", "afternoon"),
    ("succes dose three", "night"),
    ("success dose three", "night"),
    ("dose three", "night"),
    ("dose 3", "night"),
]
AYUCARE_API_URL = "http://127.0.0.1:5000/api/update_status"
AYUCARE_GET_SCHEDULE_URL = "http://127.0.0.1:5000/api/get_schedule"
SCHEDULE_SYNC_INTERVAL = 300  # seconds (5 min) – resend schedule to device


def _normalize(s: str) -> str:
    """Normalize serial data for matching: strip and collapse whitespace."""
    return " ".join(s.replace("\r", " ").replace("\n", " ").split()).lower()


def save_error_to_file(message: str):
    """Write error message to a file so you can open and copy it."""
    try:
        with open(ERROR_LOG_FILE, "w", encoding="utf-8") as f:
            f.write(message)
        print(f"\n(Error saved to {ERROR_LOG_FILE} — open that file to copy the message.)")
    except Exception:
        pass


def ensure_log_header():
    """Create CSV file with header if it doesn't exist."""
    try:
        with open(LOG_FILE, "x", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "event"])
    except FileExistsError:
        pass


def log_event(event: str):
    """Append one row to medicine_log.csv with current timestamp."""
    ensure_log_header()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([timestamp, event])


def show_desktop_notification(title: str, message: str):
    """Show a native desktop notification (Windows/Mac/Linux)."""
    try:
        notification.notify(
            title=title,
            message=message,
            app_name="Smart Medicine Box",
            timeout=5,
        )
    except Exception as e:
        print(f"  [Notification failed: {e}]")


def list_available_ports():
    """Return a list of (port, description) for all available serial ports."""
    return [(p.device, p.description or "—") for p in list_ports.comports()]


def open_serial(port: str):
    """
    Open serial connection. Raises serial.SerialException if port is in use
    or device is unplugged.
    """
    return serial.Serial(
        port=port,
        baudrate=BAUD_RATE,
        timeout=0.1,
        write_timeout=1,
    )


def get_matched_trigger_and_dose_time(buffer: str):
    """
    If buffer contains a known trigger, return (trigger_string, dose_time).
    Otherwise return (None, "morning").
    """
    t = buffer.lower()
    for trigger, dose_time in DOSE_MAP:
        if trigger in t:
            return trigger, dose_time
    if TRIGGER_STRING in buffer:
        return TRIGGER_STRING, "morning"
    return None, "morning"


def fetch_schedule(box_id: str):
    """Fetch schedule for box_id from Ayucare API. Returns dict or None."""
    url = f"{AYUCARE_GET_SCHEDULE_URL.rstrip('/')}/{box_id}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("success"):
                return data
    except Exception:
        pass
    return None


def build_schedule_lines(data: dict) -> str:
    """
    Build lines for ESP32: SCHEDULE_START, then MORNING=1,08:00,Aspirin etc., then SCHEDULE_END.
    ESP32 can parse these to set reminder times and which slots are enabled.
    """
    sched = data.get("schedule", {})
    times = data.get("times", {})
    meds = data.get("medicines", {})
    # Include a live clock sync line so ESP32 can keep wall-clock time
    # without WiFi/RTC (it uses the PC time as the source).
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = ["SCHEDULE_START", f"CLOCK={now_str}"]
    for slot in ("morning", "afternoon", "night"):
        on = 1 if sched.get(slot, False) else 0
        default_time = {"morning": "08:00", "afternoon": "14:00", "night": "20:00"}[slot]
        t = (times.get(slot) or default_time).strip()[:5]
        m = (meds.get(slot) or "").strip().replace(",", " ")  # no comma in value
        lines.append(f"{slot.upper()}={on},{t},{m}")
    lines.append("SCHEDULE_END")
    return "\n".join(lines) + "\n"


def send_schedule_to_esp32(ser, box_id: str) -> bool:
    """Fetch schedule from API and send it to the ESP32 over serial. Returns True if sent."""
    data = fetch_schedule(box_id)
    if not data:
        return False
    payload = build_schedule_lines(data)
    try:
        ser.write(payload.encode("utf-8"))
        return True
    except Exception:
        return False


def notify_ayucare_app(box_id: str, dose_time: str) -> bool:
    """
    Send dose-update to Ayucare web app so the dashboard shows the notification.
    Returns True if the server accepted the update.
    """
    payload = json.dumps({
        "box_id": box_id,
        "dose_time": dose_time,
        "status": "taken",
    }).encode("utf-8")
    req = urllib.request.Request(
        AYUCARE_API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status in (200, 201)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        if e.code == 404:
            print(f"  [Dashboard: no patient with box_id {box_id!r}. Add this box in Ayucare.]")
        else:
            print(f"  [Dashboard failed: HTTP {e.code}] {body}")
        return False
    except urllib.error.URLError as e:
        print(f"  [Dashboard not updated: is run.py running? Open http://127.0.0.1:5000 and stay on dashboard.]")
        return False
    except Exception as e:
        print(f"  [Ayucare app not reached: {e}. Is run.py running?]")
        return False


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--list" in sys.argv or "-l" in sys.argv:
        ports = list_available_ports()
        if not ports:
            print("No serial ports found. Plug in the ESP32 and try again.")
        else:
            print("Available serial ports:")
            for dev, desc in ports:
                print(f"  {dev}  —  {desc}")
            print("\nRun: python medicine_box_monitor.py <PORT>   e.g.  python medicine_box_monitor.py COM8")
        sys.exit(0)

    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    box_id = None
    if "--box-id" in sys.argv:
        i = sys.argv.index("--box-id")
        if i + 1 < len(sys.argv):
            raw = sys.argv[i + 1].strip()
            # Must be a box id like BOX123, not a URL
            if raw and not any(x in raw for x in ("http", "127.0.0.1", "/api/", ".")):
                box_id = raw
            else:
                print("Warning: --box-id should be the box id only (e.g. BOX123), not a URL. Ignoring.")
    port = DEFAULT_PORT
    for a in args:
        if a.upper().startswith("COM") and len(a) <= 5:
            port = a
            break
    print(f"Using port: {port}")
    if box_id:
        print(f"Box ID: {box_id} → dose will appear in Ayucare dashboard when you press the button")
    else:
        print("To see notifications in the Ayucare app: use --box-id BOX123 (only the id, e.g. BOX123)")
        print("  Example: python medicine_box_monitor.py COM8 --box-id BOX123")
    print("Monitoring Medicine Box... (Ctrl+C to stop)")
    print("Waiting for ESP32 to send e.g. 'succes dose one' or 'dose one' when you press the button.")
    if verbose:
        print("(Verbose: showing all data from ESP32)\n")
    else:
        print("(If nothing happens when you press the button, run with --verbose to see what the ESP32 sends.)\n")

    try:
        ser = open_serial(port)
    except serial.SerialException as e:
        lines = [
            "ERROR: Could not open serial port.",
            "  - Is the COM port already in use? Close the Serial Monitor in VS Code/PlatformIO.",
            "  - Is the ESP32 plugged in and on the correct port?",
            f"  Details: {e}",
        ]
        ports = list_available_ports()
        if ports:
            lines.append("\nAvailable ports on this computer:")
            for dev, desc in ports:
                lines.append(f"  {dev}  —  {desc}")
            lines.append(f"\nTry: python medicine_box_monitor.py {ports[0][0]}")
        else:
            lines.append("\nNo serial ports detected. Plug in the ESP32 (USB) and run again.")
        msg = "\n".join(lines)
        print(msg)
        save_error_to_file(msg)
        sys.exit(1)

    # Send schedule from dashboard to device (software → hardware)
    if box_id:
        if send_schedule_to_esp32(ser, box_id):
            print("Schedule sent to device (from dashboard). Will resync every 5 min.")
        else:
            print("Could not send schedule to device (is run.py running and this box in the dashboard?).")
        def _sync_loop():
            while True:
                time.sleep(SCHEDULE_SYNC_INTERVAL)
                try:
                    if send_schedule_to_esp32(ser, box_id):
                        print(f"  [{datetime.now().strftime('%H:%M:%S')}] Schedule resynced to device.")
                except Exception:
                    pass
        t = threading.Thread(target=_sync_loop, daemon=True)
        t.start()

    buffer = ""
    last_heartbeat = time.monotonic()
    HEARTBEAT_INTERVAL = 30  # seconds

    try:
        while True:
            try:
                # Periodic "still alive" message
                now = time.monotonic()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                    print(f"  [{datetime.now().strftime('%H:%M:%S')}] Still listening... (waiting for {TRIGGER_STRING!r})")
                    last_heartbeat = now

                if ser.in_waiting:
                    data = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
                    if verbose and data.strip():
                        print(f"  [ESP32] {repr(data)}")
                    buffer += data
                    # Normalize so we match "succes dose one\r\n" or "dose one" etc.
                    normalized = _normalize(buffer)
                    matched_trigger = None
                    for trigger, _ in DOSE_MAP:
                        if _normalize(trigger) in normalized:
                            matched_trigger = trigger
                            break
                    if not matched_trigger and _normalize(TRIGGER_STRING) in normalized:
                        matched_trigger = TRIGGER_STRING
                    if matched_trigger:
                        _, dose_time = get_matched_trigger_and_dose_time(buffer)
                        log_event(matched_trigger)
                        show_desktop_notification(
                            "Medicine Box",
                            "Patient took their medicine.",
                        )
                        if box_id:
                            if notify_ayucare_app(box_id, dose_time):
                                print(f"  [{datetime.now().strftime('%H:%M:%S')}] Dose ({dose_time}) → sent to dashboard. Check the notification bell on the dashboard.")
                            else:
                                print(f"  [{datetime.now().strftime('%H:%M:%S')}] Dose detected – logged; see message above for why dashboard was not updated.")
                        else:
                            print(f"  [{datetime.now().strftime('%H:%M:%S')}] Dose detected – logged only. To show in dashboard, run with: --box-id BOX123")
                        # Remove the matched part from buffer (approximate)
                        buf_lower = buffer.lower()
                        idx = buf_lower.find(matched_trigger.lower())
                        if idx >= 0:
                            buffer = (buffer[:idx] + buffer[idx + len(matched_trigger):]).lstrip("\r\n ")
                        else:
                            buffer = buffer[256:] if len(buffer) > 512 else ""
                else:
                    # Prevent buffer from growing if we never see the trigger
                    if len(buffer) > 512:
                        buffer = buffer[-256:]
                    time.sleep(0.1)  # avoid busy loop when no data
            except serial.SerialException as e:
                msg = f"ERROR: Serial connection lost. Is the hardware unplugged?\n  Details: {e}"
                print("\n" + msg)
                save_error_to_file(msg)
                sys.exit(1)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
