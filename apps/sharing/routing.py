from django.urls import path

from .chat import GroupChatConsumer

websocket_urlpatterns = [
    path("ws/groups/<int:group_id>/chat/", GroupChatConsumer.as_asgi()),
]
