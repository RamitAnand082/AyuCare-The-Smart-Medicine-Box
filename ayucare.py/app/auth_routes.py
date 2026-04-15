from flask import Blueprint, render_template, request, redirect, url_for, flash, session
import re

from .extensions import db
from .models import Doctor


auth_bp = Blueprint("auth", __name__)


def password_is_valid(password: str) -> bool:
    """
    Enforce password rules:
    - Length between 8 and 12 characters
    - At least 2 digits
    - At least 1 uppercase letter
    - At least 1 special character
    """
    if not (8 <= len(password) <= 12):
        return False

    digits = sum(c.isdigit() for c in password)
    if digits < 2:
        return False

    if not any(c.isupper() for c in password):
        return False

    # Define special characters as anything that is not alphanumeric or whitespace
    special_pattern = re.compile(r"[^\w\s]")
    if not special_pattern.search(password):
        return False

    return True


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """
    Doctor registration.

    Fields:
    - name
    - license_id
    - password
    - confirm_password
    """
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        license_id = request.form.get("license_id", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not name or not license_id or not password or not confirm_password:
            flash("All fields are required.", "error")
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("register.html")

        if not password_is_valid(password):
            flash("Password does not meet complexity requirements.", "error")
            return render_template("register.html")

        # Check for existing doctor with the same license ID
        existing = Doctor.query.filter_by(license_id=license_id).first()
        if existing:
            flash("A doctor with this License ID already exists.", "error")
            return render_template("register.html")

        doctor = Doctor(name=name, license_id=license_id)
        doctor.set_password(password)
        db.session.add(doctor)
        db.session.commit()

        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """
    Doctor login using License ID and password.
    """
    if request.method == "POST":
        license_id = request.form.get("license_id", "").strip()
        password = request.form.get("password", "")

        if not license_id or not password:
            flash("License ID and password are required.", "error")
            return render_template("login.html")

        doctor = Doctor.query.filter_by(license_id=license_id).first()
        if doctor is None or not doctor.check_password(password):
            flash("Invalid License ID or password.", "error")
            return render_template("login.html")

        # Successful login: store minimal info in the session
        session.clear()
        session["doctor_id"] = doctor.id
        session["doctor_name"] = doctor.name

        return redirect(url_for("dashboard.dashboard"))

    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    """
    Clear the session and redirect to the login page.
    """
    session.clear()
    return redirect(url_for("auth.login"))

