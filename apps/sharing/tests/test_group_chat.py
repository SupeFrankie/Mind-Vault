import json
import uuid

from asgiref.testing import ApplicationCommunicator
from asgiref.sync import async_to_sync
from django.conf import settings
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.sharing.models import GroupMessage, Membership, StudyGroup
from config.asgi import application


class WebsocketTestClient:
    def __init__(self, app, path, headers):
        self.communicator = ApplicationCommunicator(app, {
            "type": "websocket",
            "path": path,
            "query_string": b"",
            "headers": headers,
            "subprotocols": [],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
        })

    async def connect(self):
        await self.communicator.send_input({"type": "websocket.connect"})
        response = await self.communicator.receive_output(timeout=3)
        if response["type"] == "websocket.close":
            return False, response.get("code", 1000)
        return True, response.get("subprotocol")

    async def send_json_to(self, data):
        await self.communicator.send_input({"type": "websocket.receive", "text": json.dumps(data)})

    async def receive_json_from(self, timeout=3):
        response = await self.communicator.receive_output(timeout=timeout)
        if response["type"] != "websocket.send":
            raise AssertionError(f"Expected websocket.send, got {response!r}")
        return json.loads(response["text"])

    async def receive_output(self, timeout=3):
        return await self.communicator.receive_output(timeout=timeout)

    async def disconnect(self):
        if not self.communicator.future.done():
            await self.communicator.send_input({"type": "websocket.disconnect", "code": 1000})
            await self.communicator.wait(timeout=3)


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    ALLOWED_HOSTS=["testserver"],
)
class GroupChatTests(TransactionTestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="chat-owner@example.com", password="test-password", full_name="Chat Owner"
        )
        self.member = User.objects.create_user(
            email="chat-member@example.com", password="test-password", full_name="Chat Member"
        )
        self.outsider = User.objects.create_user(
            email="chat-outsider@example.com", password="test-password", full_name="Outside User"
        )
        self.group = StudyGroup.objects.create(name="Algorithms Study Group", owner=self.owner)
        Membership.objects.create(group=self.group, user=self.owner, role=Membership.Role.OWNER)
        Membership.objects.create(group=self.group, user=self.member)

    def make_client(self, user):
        client = Client()
        client.force_login(user)
        return client

    def websocket(self, client):
        session_cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
        headers = [
            (b"cookie", f"{settings.SESSION_COOKIE_NAME}={session_cookie}".encode()),
            (b"host", b"testserver"),
            (b"origin", b"http://testserver"),
        ]
        path = f"/ws/groups/{self.group.pk}/chat/"
        return WebsocketTestClient(application, path, headers)

    def test_member_messages_persist_deliver_live_and_return_in_history(self):
        sender = self.websocket(self.make_client(self.owner))
        receiver = self.websocket(self.make_client(self.member))
        reconnect_client = self.make_client(self.member)

        async def exchange_and_reconnect():
            self.assertEqual(await sender.connect(), (True, None))
            self.assertEqual(await receiver.connect(), (True, None))
            self.assertEqual((await sender.receive_json_from())['messages'], [])
            self.assertEqual((await receiver.receive_json_from())['messages'], [])
            client_id = str(uuid.uuid4())
            await sender.send_json_to({"type": "message", "client_id": client_id, "body": "  Is anyone reviewing graph traversal?  "})
            first_delivery = await sender.receive_json_from(timeout=3)
            second_delivery = await receiver.receive_json_from(timeout=3)
            self.assertEqual(first_delivery["type"], "message")
            self.assertEqual(second_delivery["message"]["body"], "Is anyone reviewing graph traversal?")
            self.assertEqual(first_delivery["message"]["id"], second_delivery["message"]["id"])
            self.assertEqual(second_delivery["message"]["sender"], "Chat Owner")
            self.assertTrue(second_delivery["message"]["created_at"])

            # A retry with the same client ID acknowledges the saved row instead of duplicating it.
            await sender.send_json_to({"type": "message", "client_id": client_id, "body": "Is anyone reviewing graph traversal?"})
            retry_sender_delivery = await sender.receive_json_from(timeout=3)
            retry_receiver_delivery = await receiver.receive_json_from(timeout=3)
            self.assertEqual(retry_sender_delivery["message"]["id"], first_delivery["message"]["id"])
            self.assertEqual(retry_receiver_delivery["message"]["id"], first_delivery["message"]["id"])

            await sender.disconnect()
            await receiver.disconnect()
            reconnect = self.websocket(reconnect_client)
            self.assertEqual(await reconnect.connect(), (True, None))
            history = await reconnect.receive_json_from()
            self.assertEqual(len(history["messages"]), 1)
            self.assertEqual(history["messages"][0]["body"], "Is anyone reviewing graph traversal?")
            await reconnect.disconnect()

        async_to_sync(exchange_and_reconnect)()
        self.assertEqual(GroupMessage.objects.filter(group=self.group).count(), 1)
        self.assertEqual(GroupMessage.objects.get(group=self.group).sender, self.owner)

    def test_nonmembers_cannot_open_chat_page_or_websocket(self):
        outsider_client = self.make_client(self.outsider)
        response = outsider_client.get(reverse("sharing:group_chat", args=[self.group.pk]))
        self.assertEqual(response.status_code, 403)

        communicator = self.websocket(outsider_client)
        connected, code = async_to_sync(communicator.connect)()
        self.assertFalse(connected)
        self.assertEqual(code, 4403)
        self.assertEqual(GroupMessage.objects.filter(group=self.group).count(), 0)

    def test_history_is_paginated_and_members_can_load_older_messages(self):
        GroupMessage.objects.bulk_create([
            GroupMessage(
                group=self.group,
                sender=self.owner,
                body=f"Message {index}",
                client_id=str(uuid.uuid4()),
            )
            for index in range(105)
        ])
        communicator = self.websocket(self.make_client(self.owner))

        async def load_history_pages():
            self.assertEqual(await communicator.connect(), (True, None))
            first_page = await communicator.receive_json_from()
            self.assertEqual(first_page["type"], "history")
            self.assertEqual(len(first_page["messages"]), 100)
            self.assertTrue(first_page["has_older"])
            self.assertEqual(first_page["messages"][0]["body"], "Message 5")
            await communicator.send_json_to({
                "type": "history",
                "before_id": first_page["messages"][0]["id"],
            })
            older_page = await communicator.receive_json_from()
            self.assertEqual(older_page["type"], "history_page")
            self.assertEqual([m["body"] for m in older_page["messages"]], [
                "Message 0", "Message 1", "Message 2", "Message 3", "Message 4",
            ])
            self.assertFalse(older_page["has_older"])
            await communicator.disconnect()

        async_to_sync(load_history_pages)()

    def test_membership_is_rechecked_before_broadcasting_to_connected_users(self):
        sender = self.websocket(self.make_client(self.owner))
        removed_member = self.websocket(self.make_client(self.member))

        async def send_after_removal():
            await sender.connect()
            await removed_member.connect()
            await sender.receive_json_from()
            await removed_member.receive_json_from()
            await database_delete_membership(self.group.pk, self.member.pk)

            await sender.send_json_to({"type": "message", "client_id": str(uuid.uuid4()), "body": "Membership is current."})
            delivered = await sender.receive_json_from(timeout=3)
            self.assertEqual(delivered["type"], "message")
            close_event = await removed_member.receive_output(timeout=3)
            self.assertEqual(close_event["type"], "websocket.close")
            self.assertEqual(close_event["code"], 4403)
            await sender.disconnect()

        async_to_sync(send_after_removal)()
        self.assertEqual(GroupMessage.objects.filter(group=self.group).count(), 1)


async def database_delete_membership(group_id, user_id):
    from channels.db import database_sync_to_async

    await database_sync_to_async(Membership.objects.filter(group_id=group_id, user_id=user_id).delete)()
