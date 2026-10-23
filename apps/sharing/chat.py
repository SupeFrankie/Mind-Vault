"""Member-only persisted study-group chat over Django Channels WebSockets."""
import uuid

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import GroupMessage, Membership, StudyGroup

MAX_MESSAGE_LENGTH = 2000
HISTORY_LIMIT = 100


def _serialize_message(message):
    return {
        "id": message.pk,
        "client_id": message.client_id,
        "sender_id": message.sender_id,
        "sender": message.sender.get_full_name() or message.sender.get_short_name(),
        "body": message.body,
        "created_at": timezone.localtime(message.created_at).isoformat(),
    }


@login_required
def chat_room(request, group_id):
    group = get_object_or_404(StudyGroup, pk=group_id)
    if not Membership.objects.filter(group=group, user=request.user).exists():
        raise PermissionDenied("You are not a member of this group.")
    return render(request, "sharing/chat_room.html", {"group": group})


class GroupChatConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=4401)
            return
        self.group_id = self.scope["url_route"]["kwargs"]["group_id"]
        self.group_name = f"group_chat_{self.group_id}"
        if not await self.is_member(user.pk):
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        history = await self.load_history(user.pk)
        if history is None:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
            await self.close(code=4403)
            return
        await self.send_json({"type": "history", **history})

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        user = self.scope["user"]
        if not await self.is_member(user.pk):
            await self.close(code=4403)
            return
        if not isinstance(content, dict):
            await self.send_json({"type": "error", "error": "Unsupported chat action."})
            return
        if content.get("type") == "history":
            try:
                before_id = int(content.get("before_id"))
            except (TypeError, ValueError):
                await self.send_json({"type": "error", "error": "Invalid history position."})
                return
            history = await self.load_older_history(user.pk, before_id)
            if history is None:
                await self.close(code=4403)
                return
            await self.send_json({"type": "history_page", **history})
            return
        if content.get("type") != "message":
            await self.send_json({"type": "error", "error": "Unsupported chat action."})
            return

        body = content.get("body")
        client_id = content.get("client_id")
        if not isinstance(body, str) or not isinstance(client_id, str):
            await self.send_json({"type": "error", "client_id": client_id, "error": "Message is invalid."})
            return
        body = body.strip()
        try:
            parsed_id = uuid.UUID(client_id)
        except (ValueError, AttributeError, TypeError):
            parsed_id = None
        if not body or len(body) > MAX_MESSAGE_LENGTH or parsed_id is None or str(parsed_id) != client_id.lower():
            await self.send_json({"type": "error", "client_id": client_id, "error": "Enter a message of up to 2,000 characters."})
            return

        try:
            message = await self.save_message(user.pk, body, client_id)
        except PermissionError:
            await self.close(code=4403)
            return
        except ValueError as exc:
            await self.send_json({"type": "error", "client_id": client_id, "error": str(exc)})
            return
        await self.channel_layer.group_send(
            self.group_name,
            {"type": "chat.message", "message": message, "sender_channel": self.channel_name},
        )

    async def chat_message(self, event):
        if not await self.is_member(self.scope["user"].pk):
            await self.close(code=4403)
            return
        await self.send_json({"type": "message", "message": event["message"]})

    @database_sync_to_async
    def is_member(self, user_id):
        return Membership.objects.filter(group_id=self.group_id, user_id=user_id).exists()

    @database_sync_to_async
    def load_history(self, user_id):
        if not Membership.objects.filter(group_id=self.group_id, user_id=user_id).exists():
            return None
        messages = list(
            GroupMessage.objects.filter(group_id=self.group_id)
            .select_related("sender")
            .order_by("-pk")[:HISTORY_LIMIT + 1]
        )
        has_older = len(messages) > HISTORY_LIMIT
        messages = messages[:HISTORY_LIMIT]
        return {
            "messages": [_serialize_message(message) for message in reversed(messages)],
            "has_older": has_older,
        }

    @database_sync_to_async
    def load_older_history(self, user_id, before_id):
        if not Membership.objects.filter(group_id=self.group_id, user_id=user_id).exists():
            return None
        messages = list(
            GroupMessage.objects.filter(group_id=self.group_id, pk__lt=before_id)
            .select_related("sender")
            .order_by("-pk")[:HISTORY_LIMIT + 1]
        )
        has_older = len(messages) > HISTORY_LIMIT
        messages = messages[:HISTORY_LIMIT]
        return {
            "messages": [_serialize_message(message) for message in reversed(messages)],
            "has_older": has_older,
        }

    @database_sync_to_async
    def save_message(self, user_id, body, client_id):
        # Group membership changes take the same group-row lock in sharing.services.
        with transaction.atomic():
            group = StudyGroup.objects.select_for_update().filter(pk=self.group_id).first()
            if group is None or not Membership.objects.filter(group=group, user_id=user_id).exists():
                raise PermissionError("Group membership is required to send messages.")
            message, created = GroupMessage.objects.get_or_create(
                group=group,
                sender_id=user_id,
                client_id=client_id,
                defaults={"body": body},
            )
            if not created and message.body != body:
                raise ValueError("That message could not be retried because its content changed.")
            message = GroupMessage.objects.select_related("sender").get(pk=message.pk)
            return _serialize_message(message)
