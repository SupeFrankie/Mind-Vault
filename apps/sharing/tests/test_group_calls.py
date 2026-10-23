import base64
import hashlib
import hmac
import json
import os
from unittest.mock import patch

from django.urls import reverse
from apps.core.testing import BaseTestCase
from apps.sharing.models import GroupCall, GroupCallParticipant, GroupCallSignal, Membership, StudyGroup


class GroupCallTests(BaseTestCase):
    def setUp(self):
        self.owner = self.make_user("call-owner@example.com", "Call Owner")
        self.member = self.make_user("call-member@example.com", "Call Member")
        self.outsider = self.make_user("call-outsider@example.com", "Outside Member")
        self.group = StudyGroup.objects.create(name="Evening Study Group", owner=self.owner)
        Membership.objects.create(group=self.group, user=self.owner, role=Membership.Role.OWNER)
        Membership.objects.create(group=self.group, user=self.member)

    def post_json(self, url, data=None):
        return self.client.post(url, data or {}, content_type="application/json")

    def test_only_group_members_can_open_and_start_calls(self):
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("sharing:call_lobby", args=[self.group.pk])).status_code, 403)
        self.assertEqual(self.post_json(reverse("sharing:call_start", args=[self.group.pk])).status_code, 403)
        self.assertEqual(GroupCall.objects.count(), 0)

        self.login(self.owner)
        self.assertContains(self.client.get(reverse("sharing:call_lobby", args=[self.group.pk])), "Start a group call")
        started = self.post_json(reverse("sharing:call_start", args=[self.group.pk]))
        repeated = self.post_json(reverse("sharing:call_start", args=[self.group.pk]))
        self.assertEqual(started.status_code, 200)
        self.assertEqual(json.loads(started.content), json.loads(repeated.content))
        self.assertEqual(GroupCall.objects.count(), 1)

    def test_members_exchange_signals_and_presence_only_within_their_call(self):
        call = GroupCall.objects.create(group=self.group, started_by=self.owner)
        self.login(self.owner)
        owner_room = self.client.get(reverse("sharing:call_room", args=[call.pk]))
        self.assertEqual(owner_room.status_code, 200)
        owner_join = self.post_json(reverse("sharing:call_join", args=[call.pk]))
        self.assertEqual(owner_join.status_code, 200)

        self.login(self.member)
        member_join = self.post_json(reverse("sharing:call_join", args=[call.pk]))
        self.assertEqual(member_join.status_code, 200)
        self.assertEqual(GroupCallParticipant.objects.filter(call=call, left_at__isnull=True).count(), 2)
        member_state = self.post_json(reverse("sharing:call_poll", args=[call.pk]), {
            "audio_muted": True, "video_enabled": False,
        })
        roster = json.loads(member_state.content)["participants"]
        self.assertEqual(len(roster), 2)
        member_status = next(person for person in roster if person["id"] == self.member.pk)
        self.assertTrue(member_status["audio_muted"])
        self.assertFalse(member_status["video_enabled"])

        signal_response = self.post_json(reverse("sharing:call_signal", args=[call.pk]), {
            "to": self.owner.pk, "kind": "offer", "payload": {"type": "offer", "sdp": "test-sdp"},
        })
        self.assertEqual(signal_response.status_code, 201)
        signal = GroupCallSignal.objects.get()
        self.assertEqual((signal.sender_id, signal.recipient_id), (self.member.pk, self.owner.pk))

        self.login(self.owner)
        owner_poll = self.post_json(reverse("sharing:call_poll", args=[call.pk]))
        delivered = json.loads(owner_poll.content)["signals"]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["payload"]["sdp"], "test-sdp")
        self.assertEqual(json.loads(owner_join.content)["name"], "Call Owner")

    def test_outsiders_cannot_join_or_read_an_existing_call(self):
        call = GroupCall.objects.create(group=self.group, started_by=self.owner)
        self.login(self.owner)
        self.post_json(reverse("sharing:call_join", args=[call.pk]))
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("sharing:call_room", args=[call.pk])).status_code, 403)
        self.assertEqual(self.post_json(reverse("sharing:call_join", args=[call.pk])).status_code, 403)
        self.assertEqual(self.post_json(reverse("sharing:call_poll", args=[call.pk])).status_code, 403)
        self.assertEqual(self.post_json(reverse("sharing:call_signal", args=[call.pk]), {
            "to": self.owner.pk, "kind": "offer", "payload": {"sdp": "forged"},
        }).status_code, 403)

    def test_signal_cannot_be_sent_to_a_nonparticipant_and_payload_is_bounded(self):
        call = GroupCall.objects.create(group=self.group, started_by=self.member)
        self.login(self.member)
        self.post_json(reverse("sharing:call_join", args=[call.pk]))
        invalid_target = self.post_json(reverse("sharing:call_signal", args=[call.pk]), {
            "to": self.outsider.pk, "kind": "offer", "payload": {"sdp": "forged"},
        })
        self.assertEqual(invalid_target.status_code, 404)
        response = self.client.post(
            reverse("sharing:call_signal", args=[call.pk]),
            data=b"{" + b" " * (128 * 1024) + b"}", content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(GroupCallSignal.objects.count(), 0)

    def test_removed_member_stops_appearing_and_cannot_receive_signals(self):
        call = GroupCall.objects.create(group=self.group, started_by=self.owner)
        self.login(self.owner)
        self.post_json(reverse("sharing:call_join", args=[call.pk]))
        self.login(self.member)
        self.post_json(reverse("sharing:call_join", args=[call.pk]))
        Membership.objects.filter(group=self.group, user=self.member).delete()

        self.login(self.owner)
        poll = self.post_json(reverse("sharing:call_poll", args=[call.pk]))
        participants = json.loads(poll.content)["participants"]
        self.assertEqual([item["id"] for item in participants], [self.owner.pk])
        response = self.post_json(reverse("sharing:call_signal", args=[call.pk]), {
            "to": self.member.pk, "kind": "offer", "payload": {"sdp": "no longer authorized"},
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(GroupCallSignal.objects.count(), 0)

    def test_last_participant_leaving_ends_the_call(self):
        call = GroupCall.objects.create(group=self.group, started_by=self.owner)
        self.login(self.owner)
        self.post_json(reverse("sharing:call_join", args=[call.pk]))
        response = self.post_json(reverse("sharing:call_leave", args=[call.pk]))
        self.assertEqual(response.status_code, 200)
        call.refresh_from_db()
        self.assertIsNotNone(call.ended_at)
        self.assertEqual(self.post_json(reverse("sharing:call_poll", args=[call.pk])).status_code, 410)

    @patch.dict(os.environ, {
        "STUDY_CALL_STUN_URLS": "stun:test.example:3478",
        "STUDY_CALL_TURN_URLS": "turn:turn.example:3478?transport=udp,turns:turn.example:5349",
        "STUDY_CALL_TURN_SECRET": "private-turn-secret",
        "STUDY_CALL_TURN_CREDENTIAL_TTL": "3600",
    })
    def test_turn_secret_stays_server_side_and_credentials_are_short_lived(self):
        call = GroupCall.objects.create(group=self.group, started_by=self.member)
        self.login(self.member)
        result = json.loads(self.post_json(reverse("sharing:call_join", args=[call.pk])).content)
        self.assertNotIn("private-turn-secret", json.dumps(result))
        self.assertEqual(result["ice_servers"][0]["urls"], ["stun:test.example:3478"])
        turn = result["ice_servers"][1]
        username = turn["username"]
        self.assertTrue(username.endswith(f":{self.member.pk}"))
        expected = base64.b64encode(hmac.new(
            b"private-turn-secret", username.encode(), hashlib.sha1
        ).digest()).decode()
        self.assertEqual(turn["credential"], expected)

    def test_unauthorized_group_link_does_not_become_an_entry_point(self):
        call = GroupCall.objects.create(group=self.group, started_by=self.owner)
        self.login(self.outsider)
        self.assertEqual(self.client.get(reverse("sharing:call_room", args=[call.pk])).status_code, 403)
        self.assertEqual(GroupCallSignal.objects.filter(call=call, recipient=self.outsider).count(), 0)
