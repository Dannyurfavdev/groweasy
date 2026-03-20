"""
core/consumers.py  — CREATE THIS FILE (new file, doesn't exist yet)

A Consumer is like a Django view but for WebSocket connections.
Instead of handling a single HTTP request, it handles a persistent
connection that stays open while the browser is on the page.

FLOW:
  1. Browser opens ws://yoursite/ws/notifications/3/
  2. connect() fires → user authenticated? → join group "project_3_alerts"
  3. Celery task fires → calls group_send() to "project_3_alerts"
  4. send_notification() fires on every consumer in that group
  5. JSON sent to browser → toast or push notification shown

WHY ASYNC?
  Django Channels consumers are async by default. This means
  they don't block the server while waiting for connections —
  thousands of users can be connected simultaneously.
"""

import json
from channels.generic.websocket import AsyncWebsocketConsumer


class NotificationConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        # Get project_id from the URL (defined in routing.py)
        self.project_id = self.scope["url_route"]["kwargs"]["project_id"]

        # Group name — all browser tabs watching the same project
        # share this group so they all get notified simultaneously
        self.group_name = f"project_{self.project_id}_alerts"

        # Reject unauthenticated connections (not logged in)
        if self.scope["user"].is_anonymous:
            await self.close()
            return

        # Join the channel group for this project
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        # Accept the WebSocket handshake
        await self.accept()

    async def disconnect(self, close_code):
        # Leave the group when browser navigates away or closes tab
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        # We don't expect messages FROM the browser
        # Notifications are server → browser only
        pass

    # ------------------------------------------------------------------
    # This method is called when Celery does:
    #   channel_layer.group_send(group_name, {"type": "send.notification", ...})
    #
    # Django Channels maps "type" to method name:
    #   "send.notification" → send_notification()
    #   (dots become underscores)
    # ------------------------------------------------------------------
    async def send_notification(self, event):
        """Forward notification from Celery to the browser as JSON."""
        await self.send(text_data=json.dumps({
            "type":       event["notification_type"],
            "title":      event["title"],
            "message":    event["message"],
            "severity":   event["severity"],   # danger | warning | info
            "project_id": event["project_id"],
            "timestamp":  event["timestamp"],
        }))