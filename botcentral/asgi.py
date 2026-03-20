"""
ASGI config for botcentral project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.0/howto/deployment/asgi/
"""
'''
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'botcentral.settings')

application = get_asgi_application()
'''

"""
botcentral/asgi.py  — REPLACE your entire existing asgi.py with this

What changed:
  - ProtocolTypeRouter splits HTTP vs WebSocket traffic
  - HTTP still goes to Django normally — nothing breaks
  - WebSocket connections go to our consumer via AuthMiddlewareStack
    (so we know which user is connected, just like request.user)
"""

import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'botcentral.settings')

# Must be called before any Django model imports
django_asgi_app = get_asgi_application()

# Import AFTER Django setup to avoid AppRegistryNotReady errors
import core.routing

application = ProtocolTypeRouter({
    # Normal HTTP requests — views, webhooks, everything — unchanged
    "http": django_asgi_app,

    # WebSocket connections — authenticated via session cookie
    "websocket": AuthMiddlewareStack(
        URLRouter(core.routing.websocket_urlpatterns)
    ),
})
