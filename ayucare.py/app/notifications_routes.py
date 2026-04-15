from flask import Blueprint, jsonify, session, request

from .extensions import db
from .models import Log, Patient, Doctor


notifications_bp = Blueprint("notifications", __name__)


def _get_current_doctor_id():
    doctor_id = session.get("doctor_id")
    return int(doctor_id) if doctor_id is not None else None


@notifications_bp.route("/unread", methods=["GET"])
def unread_notifications():
    """
    Return unread notifications for the logged-in doctor.

    Backed by the `logs` table where `is_read = 0`.
    """
    doctor_id = _get_current_doctor_id()
    if not doctor_id:
        return jsonify({"notifications": []})

    # Join logs -> patients to filter by doctor
    unread_logs = (
        db.session.query(Log, Patient)
        .join(Patient, Log.patient_id == Patient.id)
        .filter(Patient.doctor_id == doctor_id, Log.is_read.is_(False))
        .order_by(Log.logged_at.desc())
        .limit(50)
        .all()
    )

    items = []
    for log, patient in unread_logs:
        items.append(
            {
                "id": log.id,
                "patient_name": patient.name,
                "box_id": log.box_id,
                "dose_time": log.dose_time,
                "status": log.status,
                "logged_at": log.logged_at.isoformat(),
                "message": _build_notification_message(patient.name, log.box_id, log.dose_time, log.status),
            }
        )

    return jsonify({"notifications": items})


@notifications_bp.route("/mark_read", methods=["POST"])
def mark_notifications_read():
    """
    Mark notifications as read.

    Accepts JSON:
    - {"id": 123} to mark a single notification
    - {"all": true} to mark all for the logged-in doctor
    """
    doctor_id = _get_current_doctor_id()
    if not doctor_id:
        return jsonify({"success": False, "message": "Not authenticated."}), 401

    payload = request.get_json(silent=True) or {}
    mark_all = bool(payload.get("all"))
    log_id = payload.get("id")

    if mark_all:
        logs_to_mark = (
            db.session.query(Log)
            .join(Patient, Log.patient_id == Patient.id)
            .filter(Patient.doctor_id == doctor_id, Log.is_read.is_(False))
            .all()
        )
        for log in logs_to_mark:
            log.is_read = True
        db.session.commit()
        return jsonify({"success": True})

    if not log_id:
        return jsonify({"success": False, "message": "No notification id provided."}), 400

    log = (
        db.session.query(Log)
        .join(Patient, Log.patient_id == Patient.id)
        .filter(Patient.doctor_id == doctor_id, Log.id == log_id)
        .first()
    )
    if not log:
        return jsonify({"success": False, "message": "Notification not found."}), 404

    log.is_read = True
    db.session.commit()
    return jsonify({"success": True})


def _build_notification_message(patient_name: str, box_id: str, dose_time: str, status: str) -> str:
    slot = dose_time.capitalize()
    if status == "taken":
        return f"Patient {patient_name} from Box {box_id} has taken the {slot} medicine."
    elif status == "missed":
        return f"Patient {patient_name} from Box {box_id} missed the {slot} medicine."
    else:
        return f"Patient {patient_name} from Box {box_id} has {status} status for {slot} dose."

