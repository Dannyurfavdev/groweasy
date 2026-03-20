"""
core/routing.py  — CREATE THIS FILE (new file, doesn't exist yet)

This is the WebSocket equivalent of urls.py.

URL pattern explanation:
  /ws/notifications/<project_id>/

  Each project gets its own isolated channel group.
  So if a user is watching Project 3, they only receive
  notifications for Project 3 — not other projects.

  Example:
    ws://127.0.0.1:8000/ws/notifications/3/
"""

from django.urls import re_path
from core import consumers

websocket_urlpatterns = [
    re_path(
        r"ws/notifications/(?P<project_id>\d+)/$",
        consumers.NotificationConsumer.as_asgi()
    ),
]