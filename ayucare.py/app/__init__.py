import os
from flask import Flask

from .extensions import db, socketio


def create_app():
    """
    Application factory for the Smart Medicine Box system.

    Sets up:
    - Flask app instance and configuration
    - SQLAlchemy (SQLite) database
    - Flask-SocketIO for real-time communication
    - Blueprint registration
    """
    app = Flask(__name__, instance_relative_config=True)

    # In production, load this from environment or a secret manager.
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")

    # SQLite database file inside the instance folder
    db_path = os.path.join(app.instance_path, "ayucare.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Ensure instance folder exists
    try:
        os.makedirs(app.instance_path, exist_ok=True)
    except OSError:
        pass

    # ---- Initialize extensions --------------------------------------------
    db.init_app(app)
    socketio.init_app(app)

    # So the dashboard receives real-time dose_update: join the doctor's socket
    # to room "doctor_<id>" when they ask (session is reliable in this handler).
    from flask import session
    from flask_socketio import join_room

    @socketio.on("connect")
    def handle_socket_connect():
        did = session.get("doctor_id")
        if did is not None:
            join_room(f"doctor_{did}")

    @socketio.on("join_dashboard")
    def handle_join_dashboard(data=None):
        """Client sends doctor_id so we add this socket to their room (session may be missing in SocketIO)."""
        did = None
        if data and isinstance(data, dict):
            did = data.get("doctor_id")
        if did is None:
            did = session.get("doctor_id")
        if did is not None:
            join_room(f"doctor_{did}")

    # Import models so SQLAlchemy knows about them before create_all
    from . import models  # noqa: F401

    # Create tables if they do not exist and ensure newer columns exist.
    with app.app_context():
        from sqlalchemy import text

        db.create_all()

        # Backwards-compatible migration: older DBs may not have `logs.is_read`.
        # This ALTER is safe to run once; if the column already exists, ignore the error.
        try:
            db.session.execute(
                text(
                    "ALTER TABLE logs ADD COLUMN is_read BOOLEAN NOT NULL DEFAULT 0"
                )
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Migration: add schedule time columns for medicine reminder times.
        for col, default in [
            ("morning_time", "'08:00'"),
            ("afternoon_time", "'14:00'"),
            ("night_time", "'20:00'"),
        ]:
            try:
                db.session.execute(
                    text(f"ALTER TABLE schedules ADD COLUMN {col} VARCHAR(5) DEFAULT {default}")
                )
                db.session.commit()
            except Exception:
                db.session.rollback()

        # Additive migration for multi-medicine tracking (safe to ignore if exists)
        for ddl in [
            "ALTER TABLE logs ADD COLUMN medicine_entry_id INTEGER",
            "ALTER TABLE logs ADD COLUMN medicine_name VARCHAR(255)",
            "ALTER TABLE logs ADD COLUMN scheduled_time VARCHAR(5)",
        ]:
            try:
                db.session.execute(text(ddl))
                db.session.commit()
            except Exception:
                db.session.rollback()

        # Migration: add medicine_entries.slot (safe to ignore if exists)
        try:
            db.session.execute(text("ALTER TABLE medicine_entries ADD COLUMN slot VARCHAR(16) NOT NULL DEFAULT 'custom'"))
            db.session.commit()
        except Exception:
            db.session.rollback()

    # ---- Blueprint registration -------------------------------------------
    from .auth_routes import auth_bp
    from .dashboard_routes import dashboard_bp
    from .api_routes import api_bp
    from .notifications_routes import notifications_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(notifications_bp, url_prefix="/notifications")

    # Route root to login or dashboard depending on session
    @app.route("/")
    def index():
        from flask import redirect, url_for, session

        if session.get("doctor_id"):
            return redirect(url_for("dashboard.dashboard"))
        return redirect(url_for("auth.login"))

    return app

