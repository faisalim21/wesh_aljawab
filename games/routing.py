# games/routing.py
from django.urls import re_path
from .consumers import LettersGameConsumer

# WebSocket: /ws/letters/<session_id>/?role=host|display|contestant
websocket_urlpatterns = [
    re_path(
        r"^ws/letters/(?P<session_id>[^/]+)/?$",
        LettersGameConsumer.as_asgi(),
    ),
]
