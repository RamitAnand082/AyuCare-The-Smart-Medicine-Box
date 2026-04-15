from datetime import datetime

from flask import Blueprint, request, jsonify

from .extensions import db, socketio
from .models import Patient, Schedule, Log, MedicineEntry
from flask import session
from flask_socketio import join_room


api_bp = Blueprint("api", __name__)


@api_bp.route("/get_schedule/<string:box_id>", methods=["GET"])
def get_schedule(box_id: str):
    """
    Return the medicine schedule for the given box_id.

    Response example:
    {
        "success": true,
        "box_id": "BOX123",
        "patient_name": "John Doe",
        "schedule": {
            "morning": true,
            "afternoon": false,
            "night": true
        }
    }
    """
    patient = Patient.query.filter_by(box_id=box_id).first()
    if patient is None:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "No patient found for the given box ID.",
                }
            ),
            404,
        )

    sched = patient.schedule
    # New (backward-compatible): include multi-medicine entries list
    entries = (
        MedicineEntry.query.filter_by(box_id=patient.box_id, active=True)
        .order_by(MedicineEntry.dose_time.asc(), MedicineEntry.created_at.asc())
        .all()
    )

    return jsonify(
        {
            "success": True,
            "box_id": patient.box_id,
            "patient_name": patient.name,
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
            "medicine_entries": [
                {
                    "id": e.id,
                    "medicine_name": e.medicine_name,
                    "dose_time": e.dose_time,
                    "slot": getattr(e, "slot", "custom"),
                    "note": e.note or "",
                }
                for e in entries
            ],
        }
    )


@api_bp.route("/get_medicine_entries/<string:box_id>", methods=["GET"])
def get_medicine_entries(box_id: str):
    """
    New API for ESP32/PC scripts: return all active medicine entries for a box.

    Response:
    {
      "success": true,
      "box_id": "BOX001",
      "patient_name": "...",
      "entries": [
        {"id": 1, "medicine_name": "...", "dose_time": "HH:MM", "note": "..."},
        ...
      ]
    }
    """
    patient = Patient.query.filter_by(box_id=box_id).first()
    if patient is None:
        return jsonify({"success": False, "message": "No patient found for the given box ID."}), 404

    entries = (
        MedicineEntry.query.filter_by(box_id=patient.box_id, active=True)
        .order_by(MedicineEntry.dose_time.asc(), MedicineEntry.created_at.asc())
        .all()
    )

    return jsonify(
        {
            "success": True,
            "box_id": patient.box_id,
            "patient_name": patient.name,
            "entries": [
                {
                    "id": e.id,
                    "medicine_name": e.medicine_name,
                    "dose_time": e.dose_time,
                    "slot": getattr(e, "slot", "custom"),
                    "note": e.note or "",
                }
                for e in entries
            ],
        }
    )


@api_bp.route("/update_status", methods=["POST"])
def update_status():
    """
    Receive dose status updates from the ESP32 / hardware.

    Expected JSON body:
    {
        "box_id": "BOX123",
        "dose_time": "morning" | "afternoon" | "night",
        "status": "taken" | "missed" | "pending"
    }
    """
    try:
        payload = request.get_json(silent=True) or {}
        box_id = (payload.get("box_id") or "").strip()
        dose_time = (payload.get("dose_time") or "").strip().lower()
        status = (payload.get("status") or "").strip().lower()
        medicine_entry_id = payload.get("medicine_entry_id")
        medicine_name = (payload.get("medicine_name") or "").strip()
        scheduled_time = (payload.get("scheduled_time") or "").strip()

        # Backward compatible:
        # - Legacy devices must send dose_time in {morning, afternoon, night}
        # - New devices/scripts can send medicine_entry_id + scheduled_time (dose_time can be "custom")
        if not box_id:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "Invalid or missing box_id.",
                    }
                ),
                400,
            )

        if medicine_entry_id is None and dose_time not in {"morning", "afternoon", "night"}:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "Invalid or missing dose_time. Must be morning/afternoon/night (legacy).",
                    }
                ),
                400,
            )

        if status not in {"taken", "missed", "pending"}:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "Invalid status. Must be 'taken', 'missed', or 'pending'.",
                    }
                ),
                400,
            )

        patient = Patient.query.filter_by(box_id=box_id).first()
        if patient is None:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "No patient found for the given box ID.",
                    }
                ),
                404,
            )

        log_entry = Log(
            patient_id=patient.id,
            box_id=box_id,
            dose_time=dose_time,
            status=status,
            is_read=False,
            medicine_entry_id=int(medicine_entry_id) if medicine_entry_id is not None else None,
            medicine_name=medicine_name or None,
            scheduled_time=scheduled_time or None,
        )
        db.session.add(log_entry)
        db.session.commit()

        doctor_id = patient.doctor_id
        message = _build_notification_message(
            patient_name=patient.name,
            box_id=box_id,
            dose_time=dose_time,
            status=status,
            medicine_name=medicine_name or None,
            scheduled_time=scheduled_time or None,
        )
        if status in {"taken", "missed"}:
            emit_payload = {
                "id": log_entry.id,
                "patient_name": patient.name,
                "box_id": box_id,
                "dose_time": dose_time,
                "status": status,
                "message": message,
                "logged_at": (log_entry.logged_at or datetime.utcnow()).isoformat(),
            }
            try:
                socketio.emit("dose_update", emit_payload, broadcast=True)
            except Exception:
                pass

        return jsonify({"success": True, "message": "Dose status recorded."})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
def _build_notification_message(
    patient_name: str,
    box_id: str,
    dose_time: str,
    status: str,
    medicine_name: str | None = None,
    scheduled_time: str | None = None,
) -> str:
    slot = dose_time.capitalize()
    if medicine_name:
        label = medicine_name
        if scheduled_time:
            label = f"{medicine_name} ({scheduled_time})"
    else:
        label = f"{slot} medicine"
    if status == "taken":
        return f"Patient {patient_name} from Box {box_id} has taken {label}."
    elif status == "missed":
        return f"Patient {patient_name} from Box {box_id} missed {label}."
    else:
        return f"Patient {patient_name} from Box {box_id} has {status} status for {label}."

