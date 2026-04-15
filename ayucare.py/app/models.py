"""
Database models for the Smart Medicine Box Doctor Monitoring System.

Using SQLAlchemy ORM via `app.extensions.db`.
"""

from datetime import datetime

from werkzeug.security import generate_password_hash, check_password_hash

from .extensions import db


class Doctor(db.Model):
    __tablename__ = "doctors"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    license_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patients = db.relationship("Patient", back_populates="doctor", cascade="all, delete-orphan")

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)


class Patient(db.Model):
    __tablename__ = "patients"

    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctors.id"), nullable=False, index=True)

    name = db.Column(db.String(120), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    box_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    doctor = db.relationship("Doctor", back_populates="patients")
    schedule = db.relationship(
        "Schedule",
        uselist=False,
        back_populates="patient",
        cascade="all, delete-orphan",
    )
    logs = db.relationship("Log", back_populates="patient", cascade="all, delete-orphan")
    medicine_entries = db.relationship(
        "MedicineEntry",
        back_populates="patient",
        cascade="all, delete-orphan",
    )


class MedicineEntry(db.Model):
    """
    New multi-medicine model (additive; does not break legacy Schedule).

    One patient can have unlimited medicine entries, each at its own time.
    Supports:
    - multiple medicines per same slot/time
    - custom times (not only morning/afternoon/night)
    """

    __tablename__ = "medicine_entries"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)

    # Duplicate box_id for quick lookup by ESP32; kept in sync at insert/update.
    box_id = db.Column(db.String(64), nullable=False, index=True)

    medicine_name = db.Column(db.String(255), nullable=False)
    dose_time = db.Column(db.String(5), nullable=False)  # "HH:MM"
    # Optional categorization (doesn't affect timing). One of: morning/afternoon/night/custom
    slot = db.Column(db.String(16), nullable=False, default="custom", index=True)
    note = db.Column(db.Text, nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    patient = db.relationship("Patient", back_populates="medicine_entries")


class Schedule(db.Model):
    __tablename__ = "schedules"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)

    # Flags indicating if a dose is configured
    morning = db.Column(db.Boolean, default=False)
    afternoon = db.Column(db.Boolean, default=False)
    night = db.Column(db.Boolean, default=False)

    # Human-readable medicine descriptions for each slot
    morning_medicine = db.Column(db.Text, nullable=True)
    afternoon_medicine = db.Column(db.Text, nullable=True)
    night_medicine = db.Column(db.Text, nullable=True)

    # Times for each dose slot (24h format "HH:MM", e.g. "08:00", "14:00", "20:00")
    morning_time = db.Column(db.String(5), nullable=True, default="08:00")
    afternoon_time = db.Column(db.String(5), nullable=True, default="14:00")
    night_time = db.Column(db.String(5), nullable=True, default="20:00")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship("Patient", back_populates="schedule")


class Log(db.Model):
    """
    Stores dose status updates coming from ESP32 and also powers
    the doctor's notification center.
    """

    __tablename__ = "logs"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True)
    box_id = db.Column(db.String(64), nullable=False, index=True)

    # 'morning' | 'afternoon' | 'night'
    dose_time = db.Column(db.String(16), nullable=False)

    # 'taken' | 'missed' | 'pending'
    status = db.Column(db.String(16), nullable=False)

    logged_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Used for the doctor's notification center
    is_read = db.Column(db.Boolean, default=False, nullable=False, index=True)

    # New (additive) fields for multi-medicine entries.
    # Null for legacy slot-based schedule logs.
    medicine_entry_id = db.Column(db.Integer, nullable=True, index=True)
    medicine_name = db.Column(db.String(255), nullable=True)
    scheduled_time = db.Column(db.String(5), nullable=True)  # "HH:MM"

    patient = db.relationship("Patient", back_populates="logs")

