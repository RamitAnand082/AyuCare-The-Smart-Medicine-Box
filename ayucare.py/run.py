from app import create_app
from app.extensions import socketio
from app.missed_dose_worker import start_missed_dose_worker


app = create_app()


if __name__ == "__main__":
    # For development only. In production, run with a proper WSGI/ASGI server.
    # SocketIO wraps the Flask app to support WebSocket-based real-time features.
    # Start background worker once (no Flask reloader to keep things simple).
    start_missed_dose_worker(app, check_interval_seconds=5, grace_seconds=25)
    # Bind to all interfaces so ESP32 on the same LAN can reach the server
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, use_reloader=False)

