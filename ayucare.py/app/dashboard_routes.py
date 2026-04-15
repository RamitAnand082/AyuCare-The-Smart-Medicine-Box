from functools import wraps
from datetime import datetime, date
from typing import Optional

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
)

from .extensions import db
from .models import Doctor, Patient, Schedule, Log, MedicineEntry


dashboard_bp = Blueprint("dashboard", __name__)


def login_required(view):
    """
    Simple decorator to protect routes that require authentication.
    """

    @wraps(view)
    def wrapped_view(**kwargs):
        if not session.get("doctor_id"):
            return redirect(url_for("auth.login"))
        return view(**kwargs)

    return wrapped_view


@dashboard_bp.route("/dashboard")
@login_required
def dashboard():
    """
    Render the main doctor dashboard page.

    The page shell is rendered server-side, while patient data and status tables
    are loaded asynchronously via Fetch API.
    """
    return render_template(
        "dashboard.html",
        doctor_name=session.get("doctor_name"),
        doctor_id=session.get("doctor_id"),
    )


@dashboard_bp.route("/dashboard/adherence")
@login_required
def adherence_view():
    """
    Render the patient adherence page with charts and summary cards.
    """
    return render_template(
        "adherence.html",
        doctor_name=session.get("doctor_name"),
        doctor_id=session.get("doctor_id"),
    )


@dashboard_bp.route("/dashboard/patients", methods=["GET"])
@login_required
def get_patients():
    """
    Return JSON list of all patients (simplified for single-doctor use),
    including schedules and medicine names.
    """
    patients = Patient.query.order_by(Patient.created_at.desc()).all()

    result = []
    for p in patients:
        sched = p.schedule
        result.append(
            {
                "id": p.id,
                "name": p.name,
                "age": p.age,
                "box_id": p.box_id,
                "schedule": {
                    "morning": bool(sched.morning) if sched else False,
                    "afternoon": bool(sched.afternoon) if sched else False,
                    "night": bool(sched.night) if sched else False,
                },
                "times": {
                    "morning": (sched.morning_time or "08:00").strip() if sched else "08:00",
                    "afternoon": (sched.afternoon_time or "14:00").strip() if sched else "14:00",
                    "night": (sched.night_time or "20:00").strip() if sched else "20:00",
                },
                "medicines": {
                    "morning": (sched.morning_medicine or "").strip()
                    if sched and sched.morning_medicine
                    else "",
                    "afternoon": (sched.afternoon_medicine or "").strip()
                    if sched and sched.afternoon_medicine
                    else "",
                    "night": (sched.night_medicine or "").strip()
                    if sched and sched.night_medicine
                    else "",
                },
            }
        )

    return jsonify({"patients": result})


@dashboard_bp.route("/dashboard/patients", methods=["POST"])
@login_required
def add_patient():
    """
    Add a new patient for the logged-in doctor.

    Expected JSON body:
    {
        "name": "Patient Name",
        "age": 42,
        "box_id": "BOX123"
    }
    """
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    age = data.get("age")
    box_id = (data.get("box_id") or "").strip()

    if not name or not age or not box_id:
        return jsonify({"success": False, "message": "Name, age, and box ID are required."}), 400

    try:
        age_int = int(age)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Age must be a valid integer."}), 400

    doctor_id = session["doctor_id"]

    # Ensure unique box_id
    existing = Patient.query.filter_by(box_id=box_id).first()
    if existing:
        return jsonify({"success": False, "message": "Box ID is already assigned to another patient."}), 400

    patient = Patient(doctor_id=doctor_id, name=name, age=age_int, box_id=box_id)
    db.session.add(patient)
    db.session.flush()  # ensure patient.id is available

    # Initialize an empty schedule row for this patient
    schedule = Schedule(
        patient_id=patient.id,
        morning=False,
        afternoon=False,
        night=False,
    )
    db.session.add(schedule)
    db.session.commit()

    return jsonify({"success": True, "message": "Patient added successfully."})


def _validate_time(val: str) -> Optional[str]:
    """
    Validate time and normalize to 24h HH:MM for storage.

    Accepts:
      - 24h: "14:30"
      - 12h: "2:30 PM", "02:30pm"
    """
    if not val or not isinstance(val, str):
        return None
    s = val.strip().replace(".", ":").upper()
    if ":" not in s:
        return None

    # Detect optional AM/PM suffix.
    is_am = s.endswith("AM")
    is_pm = s.endswith("PM")
    if is_am or is_pm:
        s = s[:-2].strip()

    parts = s.split(":", 1)
    try:
        h, m = int(parts[0].strip()), int(parts[1].strip()[:2])
        if is_am or is_pm:
            if not (1 <= h <= 12 and 0 <= m <= 59):
                return None
            if is_am:
                h = 0 if h == 12 else h
            else:  # PM
                h = 12 if h == 12 else h + 12
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"
    except (ValueError, IndexError):
        pass
    return None


@dashboard_bp.route("/dashboard/medicine_entries", methods=["GET"])
@login_required
def list_medicine_entries():
    """
    Return multi-medicine entries for all patients (one row per entry).
    """
    entries = (
        db.session.query(MedicineEntry, Patient)
        .join(Patient, MedicineEntry.patient_id == Patient.id)
        .order_by(Patient.created_at.desc(), MedicineEntry.dose_time.asc(), MedicineEntry.created_at.asc())
        .all()
    )

    rows = []
    for e, p in entries:
        rows.append(
            {
                "id": e.id,
                "patient_id": p.id,
                "patient_name": p.name,
                "box_id": p.box_id,
                "medicine_name": e.medicine_name,
                "dose_time": e.dose_time,
                "slot": getattr(e, "slot", "custom"),
                "note": e.note or "",
                "active": bool(e.active),
            }
        )

    return jsonify({"entries": rows})


@dashboard_bp.route("/dashboard/medicine_entries", methods=["POST"])
@login_required
def add_medicine_entry():
    """
    Add a new multi-medicine entry for a patient.

    Expected JSON:
    {
      "patient_id": 1,
      "medicine_name": "Paracetamol",
      "dose_time": "09:15",
      "note": "after food"   // optional
    }
    """
    data = request.get_json(silent=True) or {}
    patient_id = data.get("patient_id")
    medicine_name = (data.get("medicine_name") or "").strip()
    dose_time = _validate_time(data.get("dose_time") or "")
    slot = (data.get("slot") or "custom").strip().lower()
    note = (data.get("note") or "").strip()

    if not patient_id or not medicine_name or not dose_time:
        return jsonify({"success": False, "message": "patient_id, medicine_name, and valid dose_time are required."}), 400

    if slot not in {"morning", "afternoon", "night", "custom"}:
        slot = "custom"

    patient = Patient.query.filter_by(id=int(patient_id)).first()
    if patient is None:
        return jsonify({"success": False, "message": "Patient not found."}), 404

    entry = MedicineEntry(
        patient_id=patient.id,
        box_id=patient.box_id,
        medicine_name=medicine_name,
        dose_time=dose_time,
        slot=slot,
        note=note or None,
        active=True,
    )
    db.session.add(entry)
    db.session.commit()

    return jsonify({"success": True, "message": "Medicine entry added.", "id": entry.id})


@dashboard_bp.route("/dashboard/medicine_entries/<int:entry_id>", methods=["PUT"])
@login_required
def update_medicine_entry(entry_id: int):
    """
    Update a medicine entry (name/time/note/active).
    """
    data = request.get_json(silent=True) or {}
    entry = MedicineEntry.query.filter_by(id=entry_id).first()
    if entry is None:
        return jsonify({"success": False, "message": "Medicine entry not found."}), 404

    if "medicine_name" in data:
        name = (data.get("medicine_name") or "").strip()
        if name:
            entry.medicine_name = name

    if "dose_time" in data:
        t = _validate_time(data.get("dose_time") or "")
        if t:
            entry.dose_time = t

    if "slot" in data:
        slot = (data.get("slot") or "custom").strip().lower()
        if slot in {"morning", "afternoon", "night", "custom"}:
            entry.slot = slot

    if "note" in data:
        note = (data.get("note") or "").strip()
        entry.note = note or None

    if "active" in data:
        entry.active = bool(data.get("active"))

    db.session.commit()
    return jsonify({"success": True, "message": "Medicine entry updated."})


@dashboard_bp.route("/dashboard/medicine_entries/<int:entry_id>", methods=["DELETE"])
@login_required
def delete_medicine_entry(entry_id: int):
    """
    Delete one medicine entry.
    """
    entry = MedicineEntry.query.filter_by(id=entry_id).first()
    if entry is None:
        return jsonify({"success": False, "message": "Medicine entry not found."}), 404

    db.session.delete(entry)
    db.session.commit()
    return jsonify({"success": True, "message": "Medicine entry deleted."})


@dashboard_bp.route("/dashboard/patients/<int:patient_id>/schedule", methods=["PUT"])
@login_required
def update_schedule(patient_id: int):
    """
    Update the medicine schedule for an existing patient.

    Expected JSON body:
    {
        "morning": true/false,
        "afternoon": true/false,
        "night": true/false,
        "morning_time": "08:00",
        "afternoon_time": "14:00",
        "night_time": "20:00",
        "morning_medicine": "Paracetamol 500mg",
        "afternoon_medicine": "Vitamin D",
        "night_medicine": "None"
    }
    Times must be 24h HH:MM format.
    """
    data = request.get_json(silent=True) or {}
    morning = bool(data.get("morning", False))
    afternoon = bool(data.get("afternoon", False))
    night = bool(data.get("night", False))

    # Only update times if explicitly provided; otherwise keep existing
    morning_time = _validate_time(data.get("morning_time"))
    afternoon_time = _validate_time(data.get("afternoon_time"))
    night_time = _validate_time(data.get("night_time"))

    morning_medicine = (data.get("morning_medicine") or "").strip()
    afternoon_medicine = (data.get("afternoon_medicine") or "").strip()
    night_medicine = (data.get("night_medicine") or "").strip()

    # In this simplified single-doctor setup, just look up by id.
    patient = Patient.query.filter_by(id=patient_id).first()
    if patient is None:
        return jsonify({"success": False, "message": "Patient not found."}), 404

    schedule = patient.schedule
    if schedule is None:
        schedule = Schedule(patient_id=patient.id)
        db.session.add(schedule)

    schedule.morning = bool(morning)
    schedule.afternoon = bool(afternoon)
    schedule.night = bool(night)
    if morning_time is not None:
        schedule.morning_time = morning_time
    elif schedule.morning_time is None:
        schedule.morning_time = "08:00"
    if afternoon_time is not None:
        schedule.afternoon_time = afternoon_time
    elif schedule.afternoon_time is None:
        schedule.afternoon_time = "14:00"
    if night_time is not None:
        schedule.night_time = night_time
    elif schedule.night_time is None:
        schedule.night_time = "20:00"
    schedule.morning_medicine = morning_medicine or None
    schedule.afternoon_medicine = afternoon_medicine or None
    schedule.night_medicine = night_medicine or None

    db.session.commit()

    return jsonify({"success": True, "message": "Schedule updated successfully."})


@dashboard_bp.route("/dashboard/patients/<int:patient_id>", methods=["DELETE"])
@login_required
def delete_patient(patient_id: int):
    """
    Delete a patient (and related schedule and logs) for the logged-in doctor.
    """
    # In this simplified single-doctor setup, just look up by id.
    patient = Patient.query.filter_by(id=patient_id).first()
    if patient is None:
        return jsonify({"success": False, "message": "Patient not found."}), 404

    db.session.delete(patient)
    db.session.commit()

    return jsonify({"success": True, "message": "Patient deleted successfully."})


@dashboard_bp.route("/dashboard/status", methods=["GET"])
@login_required
def live_status():
    """
    Returns live dose status for all patients of the logged-in doctor.

    Status for each enabled slot (morning/afternoon/night) is derived from the
    latest log entry for *today*:
    - If there is a log with status 'taken'  -> 'Taken'
    - If there is a log with status 'missed' -> 'Missed'
    - If there is no log                     -> 'Pending'
    """
    # For now, show live status for all patients (single-doctor setup).
    patients = Patient.query.order_by(Patient.created_at.desc()).all()

    today = date.today()

    result = []
    for p in patients:
        sched = p.schedule
        patient_status = {
            "id": p.id,
            "name": p.name,
            "age": p.age,
            "box_id": p.box_id,
            "times": {
                "morning": (sched.morning_time or "08:00").strip() if sched else "08:00",
                "afternoon": (sched.afternoon_time or "14:00").strip() if sched else "14:00",
                "night": (sched.night_time or "20:00").strip() if sched else "20:00",
            },
            "status": {},
            "medicines": {
                "morning": (sched.morning_medicine or "").strip()
                if sched and sched.morning_medicine
                else "",
                "afternoon": (sched.afternoon_medicine or "").strip()
                if sched and sched.afternoon_medicine
                else "",
                "night": (sched.night_medicine or "").strip()
                if sched and sched.night_medicine
                else "",
            },
        }

        # For each enabled slot, determine the latest status for today
        for slot_name in ("morning", "afternoon", "night"):
            if not getattr(sched, slot_name, False):
                continue

            log = (
                Log.query.filter_by(patient_id=p.id, dose_time=slot_name)
                .filter(Log.logged_at >= datetime.combine(today, datetime.min.time()))
                .filter(Log.logged_at <= datetime.combine(today, datetime.max.time()))
                .order_by(Log.logged_at.desc())
                .first()
            )

            if log is None:
                status_label = "Pending"
            else:
                if log.status == "taken":
                    status_label = "Taken"
                elif log.status == "missed":
                    status_label = "Missed"
                else:
                    status_label = "Pending"

            patient_status["status"][slot_name] = status_label

        result.append(patient_status)

    return jsonify({"patients": result})


@dashboard_bp.route("/all_adherence_data", methods=["GET"])
@login_required
def all_adherence_data():
    """
    Return adherence metrics for all patients.

    Adherence % is computed as:
      taken_count / (taken_count + missed_count) * 100

    Status 'pending' (if ever used) is ignored in the denominator.
    """
    patients = Patient.query.order_by(Patient.created_at.asc()).all()

    result = []
    for p in patients:
        # Consider only explicit taken/missed logs for this patient
        base_query = Log.query.filter_by(patient_id=p.id).filter(
            Log.status.in_(("taken", "missed"))
        )

        taken_count = base_query.filter_by(status="taken").count()
        missed_count = base_query.filter_by(status="missed").count()
        total_considered = taken_count + missed_count

        if total_considered > 0:
            adherence_pct = (taken_count / total_considered) * 100.0
        else:
            adherence_pct = 0.0

        if adherence_pct >= 90.0:
            grade = "A+"
        elif adherence_pct >= 75.0:
            grade = "A"
        elif adherence_pct >= 50.0:
            grade = "B"
        else:
            grade = "C"

        result.append(
            {
                "id": p.id,
                "name": p.name,
                "age": p.age,
                "box_id": p.box_id,
                "adherence_percent": round(adherence_pct, 1),
                "grade": grade,
                "taken_count": taken_count,
                "missed_count": missed_count,
                "total_events": total_considered,
            }
        )

    return jsonify({"patients": result})

