import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import OriginValidator

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'wesh_aljawab.settings')

django_asgi_app = get_asgi_application()

from games import routing

ALLOWED_ORIGINS = [
    "https://wesh-aljawab.onrender.com",
    "http://wesh-aljawab.onrender.com",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": OriginValidator(
        AuthMiddlewareStack(
            URLRouter(routing.websocket_urlpatterns)
        ),
        ALLOWED_ORIGINS
    ),
})
