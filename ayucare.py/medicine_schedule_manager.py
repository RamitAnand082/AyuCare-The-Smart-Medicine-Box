"""
Multi-medicine schedule manager via Serial (ESP32).

- Fetches schedule from Flask dashboard API (set times in the web UI).
- Sends START_ALARM at scheduled times; shows desktop notification.
- Listens for SWITCH_PRESSED from ESP32; shows success, sends STOP_ALARM, logs to CSV.
- If no switch within 5 minutes: shows missed-dose alert and logs.

Usage:
  python medicine_schedule_manager.py COM8
  python medicine_schedule_manager.py COM8 --box-id BOX001 --api http://127.0.0.1:5000

Schedule is loaded from the dashboard. Set times in the web UI for your patient's box_id.
"""

import argparse
import csv
import json
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    import serial
except ImportError:
    print("Install pyserial: pip install pyserial")
    sys.exit(1)

try:
    from plyer import notification
except ImportError:
    print("Install plyer: pip install plyer")
    sys.exit(1)


# --- Config: Flask API and Box ID (schedule is fetched from dashboard) ---
API_BASE_URL = "http://127.0.0.1:5000"
BOX_ID = "BOX001"
SCHEDULE_REFRESH_SECONDS = 5 * 60  # refresh from API every 5 minutes

BAUD = 115200
ALARM_TIMEOUT_SECONDS = 5 * 60  # 5 minutes
CSV_PATH = Path(__file__).resolve().parent / "adherence_log.csv"


def notify_desktop(title: str, message: str, timeout: int = 10) -> None:
    """Show a desktop notification (Windows/macOS/Linux)."""
    try:
        notification.notify(title=title, message=message, timeout=timeout)
    except Exception as e:
        print(f"[Notify] {title}: {message} ({e})")


def log_adherence(medicine_name: str, status: str) -> None:
    """Append one row to adherence_log.csv: timestamp, medicine_name, status."""
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if f.tell() == 0:
            w.writerow(["timestamp", "medicine_name", "status"])
        w.writerow([datetime.now().isoformat(), medicine_name, status])


def post_status_to_api(
    api_base: str,
    box_id: str,
    status: str,
    *,
    medicine_entry_id: int | None = None,
    medicine_name: str | None = None,
    scheduled_time: str | None = None,
) -> bool:
    """POST to /api/update_status so dashboard reflects taken/missed."""
    url = f"{api_base.rstrip('/')}/api/update_status"
    try:
        payload = {
            "box_id": box_id,
            "dose_time": "custom",
            "status": status,
        }
        if medicine_entry_id is not None:
            payload["medicine_entry_id"] = medicine_entry_id
        if medicine_name:
            payload["medicine_name"] = medicine_name
        if scheduled_time:
            payload["scheduled_time"] = scheduled_time

        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[API] POST update_status failed: {e}")
        return False


def parse_time(s: str) -> tuple[int, int]:
    """Parse 'HH:MM' -> (hour, minute)."""
    parts = s.strip().split(":")
    if len(parts) != 2:
        return 0, 0
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 0, 0


def fetch_entries_from_api(api_base: str, box_id: str) -> list[dict]:
    """
    Fetch medicine entries from Flask API GET /api/get_medicine_entries/<box_id>.
    Returns list of {"id": int, "name": str, "time": "HH:MM", "slot": str}.
    """
    url = f"{api_base.rstrip('/')}/api/get_medicine_entries/{box_id}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"[API] Failed to fetch entries: {e}")
        return []

    if not data.get("success"):
        print("[API] Entries fetch returned success=False")
        return []

    result = []
    for e in data.get("entries") or []:
        try:
            entry_id = int(e.get("id"))
        except Exception:
            continue
        name = (e.get("medicine_name") or "").strip()
        t = (e.get("dose_time") or "").strip()
        slot = (e.get("slot") or "custom").strip().lower()
        if not name or not t:
            continue
        result.append({"id": entry_id, "name": name, "time": t, "slot": slot})

    return result


def run_manager(port: str, baud: int = BAUD, api_base: str = API_BASE_URL, box_id: str = BOX_ID) -> None:
    ser = None
    alarm_state = {"active": False, "entry_id": None, "medicine": None, "time": None, "slot": None, "started_at": None}
    lock = threading.Lock()
    schedule_lock = threading.Lock()
    medicine_entries: list[dict] = []
    pending_queue: list[dict] = []

    def open_serial() -> serial.Serial | None:
        try:
            return serial.Serial(port, baud, timeout=0.1)
        except Exception as e:
            print(f"Cannot open {port}: {e}")
            return None

    def send_line(line: str) -> None:
        if ser and ser.is_open:
            try:
                ser.write((line.strip() + "\n").encode("utf-8"))
                ser.flush()
            except Exception as e:
                print(f"Serial write error: {e}")

    def reader_thread_fn() -> None:
        buffer = ""
        while True:
            if ser is None or not ser.is_open:
                time.sleep(0.5)
                continue
            try:
                data = ser.read(ser.in_waiting or 1)
                if data:
                    buffer += data.decode("utf-8", errors="ignore")
                    while "\n" in buffer or "\r" in buffer:
                        if "\n" in buffer:
                            line, _, buffer = buffer.partition("\n")
                        else:
                            line, _, buffer = buffer.partition("\r")
                        line = line.strip()
                        if line:
                            if line == "SWITCH_PRESSED":
                                with lock:
                                    med = alarm_state.get("medicine")
                                    entry_id = alarm_state.get("entry_id")
                                    scheduled_time = alarm_state.get("time")
                                    if med and alarm_state.get("active"):
                                        alarm_state["active"] = False
                                        alarm_state["entry_id"] = None
                                        alarm_state["medicine"] = None
                                        alarm_state["time"] = None
                                        alarm_state["slot"] = None
                                        alarm_state["started_at"] = None
                                        send_line("STOP_ALARM")
                                        notify_desktop("SUCCESS", f"{med} was taken!")
                                        log_adherence(med, "taken")
                                        post_status_to_api(
                                            api_base,
                                            box_id,
                                            "taken",
                                            medicine_entry_id=entry_id,
                                            medicine_name=med,
                                            scheduled_time=scheduled_time,
                                        )
                                        print(f"[OK] {med} taken, logged.")
                            # ignore other lines or echo for debug
                else:
                    time.sleep(0.02)
            except Exception as e:
                print(f"Serial read error: {e}")
                time.sleep(0.5)

    def refresh_thread_fn() -> None:
        """Periodically fetch medicine entries from Flask API."""
        nonlocal medicine_entries
        while True:
            fetched = fetch_entries_from_api(api_base, box_id)
            if fetched:
                with schedule_lock:
                    medicine_entries = fetched
                print(f"[API] Entries updated: {fetched}")
            time.sleep(SCHEDULE_REFRESH_SECONDS)

    def scheduler_thread_fn() -> None:
        last_triggered: dict[int, str] = {}  # entry_id -> "YYYY-MM-DD" so we trigger once per day per entry
        while True:
            with schedule_lock:
                sched = list(medicine_entries)
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            current_minute = (now.hour, now.minute)

            # enqueue all due medicines at this minute
            due = []
            for entry in sched:
                entry_id = entry["id"]
                name = entry["name"]
                slot = entry.get("slot", "custom")
                h, m = parse_time(entry["time"])
                if (h, m) != current_minute:
                    continue
                if last_triggered.get(entry_id) == today:
                    continue
                last_triggered[entry_id] = today
                due.append(entry)

            with lock:
                pending_queue.extend(due)

            # If no alarm active, start next from queue
            with lock:
                if (not alarm_state["active"]) and pending_queue:
                    next_entry = pending_queue.pop(0)
                    alarm_state["active"] = True
                    alarm_state["entry_id"] = next_entry["id"]
                    alarm_state["medicine"] = next_entry["name"]
                    alarm_state["time"] = next_entry["time"]
                    alarm_state["slot"] = next_entry.get("slot", "custom")
                    alarm_state["started_at"] = time.monotonic()
                    med = alarm_state["medicine"]
                    slot = alarm_state.get("slot") or "custom"
                    t = alarm_state.get("time") or ""
                    # Send details so ESP32 LCD can show what to take
                    send_line(f"START_ALARM|{med}|{t}|{slot}")
                    notify_desktop("ALARM", f"Time for {med}!")
                    print(f"[ALARM] {med} at {now.strftime('%H:%M')}")

            # Check 5-minute timeout for current alarm
            with lock:
                if alarm_state["active"] and alarm_state["started_at"] is not None:
                    elapsed = time.monotonic() - alarm_state["started_at"]
                    if elapsed >= ALARM_TIMEOUT_SECONDS:
                        med = alarm_state["medicine"]
                        entry_id = alarm_state.get("entry_id")
                        scheduled_time = alarm_state.get("time")
                        alarm_state["active"] = False
                        alarm_state["entry_id"] = None
                        alarm_state["medicine"] = None
                        alarm_state["time"] = None
                        alarm_state["slot"] = None
                        alarm_state["started_at"] = None
                        send_line("STOP_ALARM")
                        notify_desktop(
                            "ALERT",
                            "Medicine was NOT taken! Please check the patient.",
                            timeout=15,
                        )
                        if med:
                            log_adherence(med, "missed")
                        post_status_to_api(
                            api_base,
                            box_id,
                            "missed",
                            medicine_entry_id=entry_id,
                            medicine_name=med,
                            scheduled_time=scheduled_time,
                        )
                        print(f"[MISSED] {med or 'Unknown'} (timeout).")

            time.sleep(1)

    # --- Main ---
    ser = open_serial()
    if not ser:
        return

    # Initial schedule fetch from API
    with schedule_lock:
        medicine_entries = fetch_entries_from_api(api_base, box_id)
    if not medicine_entries:
        print("[WARN] No medicine entries from API. Start Flask (python run.py) and add entries in dashboard.")
    else:
        print("[API] Initial entries:", medicine_entries)

    print(f"Serial opened: {port} @ {baud}")
    print("Alarm timeout:", ALARM_TIMEOUT_SECONDS, "seconds. Log file:", CSV_PATH)
    print("Waiting for schedule and SWITCH_PRESSED... (Ctrl+C to exit)")

    reader = threading.Thread(target=reader_thread_fn, daemon=True)
    refresh = threading.Thread(target=refresh_thread_fn, daemon=True)
    scheduler = threading.Thread(target=scheduler_thread_fn, daemon=True)
    reader.start()
    refresh.start()
    scheduler.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        if ser and ser.is_open:
            send_line("STOP_ALARM")
            ser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-medicine schedule manager over Serial")
    parser.add_argument("port", default="COM8", nargs="?", help="Serial port (e.g. COM8)")
    parser.add_argument("--baud", type=int, default=BAUD, help="Baud rate")
    parser.add_argument("--api", default=API_BASE_URL, help="Flask API base URL (e.g. http://127.0.0.1:5000)")
    parser.add_argument("--box-id", default=BOX_ID, help="Box ID from dashboard (e.g. BOX001)")
    args = parser.parse_args()
    run_manager(args.port, args.baud, args.api, args.box_id)


if __name__ == "__main__":
    main()
