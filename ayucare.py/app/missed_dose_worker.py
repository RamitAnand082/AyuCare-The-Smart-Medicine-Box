import threading
import time as time_module
from datetime import datetime, time, timedelta

from .extensions import db, socketio
from .models import Patient, Schedule, Log, MedicineEntry


SLOTS = ("morning", "afternoon", "night")


def start_missed_dose_worker(app, check_interval_seconds: int = 5, grace_seconds: int = 25):
    """
    Background worker that marks scheduled doses as MISSED shortly after
    the scheduled time when no TAKEN log exists for that dose today.

    - check_interval_seconds: how often to check (default 5s)
    - grace_seconds: how long after the scheduled time we wait before
      considering the dose as missed (default 25s)
    """

    def loop():
        while True:
            try:
                with app.app_context():
                    _check_and_mark_missed(grace_seconds=grace_seconds)
            except Exception:
                # Keep the worker alive even if something goes wrong.
                pass
            time_module.sleep(check_interval_seconds)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def _parse_hhmm(value: str, default: str) -> tuple[int, int]:
    raw = (value or default).strip()
    parts = raw.split(":")
    if len(parts) != 2:
        parts = default.split(":")
    return int(parts[0]), int(parts[1])


def _check_and_mark_missed(grace_seconds: int = 25) -> None:
    now = datetime.now()
    today = now.date()
    start_of_day = datetime.combine(today, time.min)
    end_of_day = datetime.combine(today, time.max)
    grace = timedelta(seconds=grace_seconds)

    rows = (
        db.session.query(Patient, Schedule)
        .join(Schedule, Schedule.patient_id == Patient.id)
        .all()
    )

    for patient, sched in rows:
        if sched is None:
            continue

        for slot in SLOTS:
            enabled = bool(getattr(sched, slot, False))
            if not enabled:
                continue

            time_value = getattr(sched, f"{slot}_time", None)
            default_time = {"morning": "08:00", "afternoon": "14:00", "night": "20:00"}[slot]
            hh, mm = _parse_hhmm(time_value, default_time)
            scheduled_dt = datetime.combine(today, time(hour=hh, minute=mm))

            # Only consider missed after scheduled time + grace window.
            if now < scheduled_dt + grace:
                continue

            taken_exists = (
                db.session.query(Log.id)
                .filter(
                    Log.patient_id == patient.id,
                    Log.box_id == patient.box_id,
                    Log.dose_time == slot,
                    Log.status == "taken",
                    Log.logged_at >= start_of_day,
                    Log.logged_at <= end_of_day,
                )
                .first()
            )
            if taken_exists:
                continue

            missed_exists = (
                db.session.query(Log.id)
                .filter(
                    Log.patient_id == patient.id,
                    Log.box_id == patient.box_id,
                    Log.dose_time == slot,
                    Log.status == "missed",
                    Log.logged_at >= start_of_day,
                    Log.logged_at <= end_of_day,
                )
                .first()
            )
            if missed_exists:
                continue

            log_entry = Log(
                patient_id=patient.id,
                box_id=patient.box_id,
                dose_time=slot,
                status="missed",
                is_read=False,
            )
            db.session.add(log_entry)
            db.session.commit()

            message = f"Patient {patient.name} from Box {patient.box_id} missed the {slot.capitalize()} medicine."
            # For debugging you can watch this in the server terminal.
            print(
                f"[Ayucare] Missed dose: patient={patient.name} "
                f"box={patient.box_id} slot={slot} at {log_entry.logged_at}"
            )
            socketio.emit(
                "dose_update",
                {
                    "id": log_entry.id,
                    "patient_name": patient.name,
                    "box_id": patient.box_id,
                    "dose_time": slot,
                    "status": "missed",
                    "message": message,
                    "logged_at": log_entry.logged_at.isoformat(),
                },
                broadcast=True,
            )

    # --- New: multi-medicine entries missed-dose handling (additive) --------
    entries = (
        db.session.query(MedicineEntry, Patient)
        .join(Patient, MedicineEntry.patient_id == Patient.id)
        .filter(MedicineEntry.active.is_(True))
        .all()
    )

    for entry, patient in entries:
        # Parse HH:MM
        try:
            hh, mm = _parse_hhmm(entry.dose_time, "08:00")
        except Exception:
            continue

        scheduled_dt = datetime.combine(today, time(hour=hh, minute=mm))
        if now < scheduled_dt + grace:
            continue

        taken_exists = (
            db.session.query(Log.id)
            .filter(
                Log.patient_id == patient.id,
                Log.box_id == patient.box_id,
                Log.status == "taken",
                Log.medicine_entry_id == entry.id,
                Log.logged_at >= start_of_day,
                Log.logged_at <= end_of_day,
            )
            .first()
        )
        if taken_exists:
            continue

        missed_exists = (
            db.session.query(Log.id)
            .filter(
                Log.patient_id == patient.id,
                Log.box_id == patient.box_id,
                Log.status == "missed",
                Log.medicine_entry_id == entry.id,
                Log.logged_at >= start_of_day,
                Log.logged_at <= end_of_day,
            )
            .first()
        )
        if missed_exists:
            continue

        log_entry = Log(
            patient_id=patient.id,
            box_id=patient.box_id,
            dose_time="custom",
            status="missed",
            is_read=False,
            medicine_entry_id=entry.id,
            medicine_name=entry.medicine_name,
            scheduled_time=entry.dose_time,
        )
        db.session.add(log_entry)
        db.session.commit()

        message = f"Patient {patient.name} from Box {patient.box_id} missed {entry.medicine_name} ({entry.dose_time})."
        print(
            f"[Ayucare] Missed medicine entry: patient={patient.name} "
            f"box={patient.box_id} med={entry.medicine_name} at {entry.dose_time}"
        )
        socketio.emit(
            "dose_update",
            {
                "id": log_entry.id,
                "patient_name": patient.name,
                "box_id": patient.box_id,
                "dose_time": "custom",
                "status": "missed",
                "message": message,
                "logged_at": log_entry.logged_at.isoformat(),
            },
            broadcast=True,
        )
