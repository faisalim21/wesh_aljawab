from django.urls import re_path
from .consumers import LettersGameConsumer, PicturesGameConsumer, TimeGameConsumer

websocket_urlpatterns = [
    re_path(r'^ws/letters/(?P<session_id>[0-9a-f-]+)/$', LettersGameConsumer.as_asgi()),
    re_path(r'^ws/pictures/(?P<session_id>[0-9a-f-]+)/$', PicturesGameConsumer.as_asgi()),
    re_path(r'^ws/time/(?P<session_id>[0-9a-f-]+)/$', TimeGameConsumer.as_asgi()),
]
