"""
Shared Flask extensions.

This module defines singletons for:
- SQLAlchemy (database ORM)
- SocketIO (real-time communication)

They are initialized in `create_app` inside `app/__init__.py`.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO

# SQLAlchemy instance used across the app
db = SQLAlchemy()

# SocketIO instance used for real-time notifications
socketio = SocketIO(async_mode="threading", cors_allowed_origins="*")

