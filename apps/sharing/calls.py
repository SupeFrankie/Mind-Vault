"""Authenticated WebRTC signaling and presence for small study-group calls.

Audio/video travels directly between participants. Django stores short signaling
messages and presence only; it is not a media server.
"""
import base64
import hashlib
import hmac
import json
import os
import time

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import GroupCall, GroupCallParticipant, GroupCallSignal, Membership, StudyGroup

PRESENCE_TTL_SECONDS = 25
IDLE_CALL_TTL_SECONDS = 300
MAX_SIGNAL_BYTES = 128 * 1024


def _member_group(request, group_id):
    group = get_object_or_404(StudyGroup, pk=group_id)
    if not Membership.objects.filter(group=group, user=request.user).exists():
        raise PermissionDenied("You are not a member of this group.")
    return group


def _active_call(call_id, user):
    call = get_object_or_404(GroupCall.objects.select_related("group"), pk=call_id)
    if not Membership.objects.filter(group_id=call.group_id, user=user).exists():
        raise PermissionDenied("You are not a member of this group.")
    if call.ended_at is not None:
        return call, False
    return call, True


def _json_body(request):
    if len(request.body) > MAX_SIGNAL_BYTES:
        return None
    try:
        value = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _participant(call, user, *, active=True):
    qs = GroupCallParticipant.objects.filter(call=call, user=user)
    if active:
        qs = qs.filter(left_at__isnull=True)
    return qs.first()


def _ice_servers(user):
    """Return public STUN and, when configured, expiring coturn REST credentials."""
    stun_urls = [url.strip() for url in os.environ.get(
        "STUDY_CALL_STUN_URLS", "stun:stun.l.google.com:19302"
    ).split(",") if url.strip()]
    servers = [{"urls": stun_urls}] if stun_urls else []
    turn_urls = [url.strip() for url in os.environ.get("STUDY_CALL_TURN_URLS", "").split(",") if url.strip()]
    secret = os.environ.get("STUDY_CALL_TURN_SECRET", "")
    if turn_urls and secret:
        try:
            ttl = max(300, min(86400, int(os.environ.get("STUDY_CALL_TURN_CREDENTIAL_TTL", "3600"))))
        except ValueError:
            ttl = 3600
        username = f"{int(time.time()) + ttl}:{user.pk}"
        digest = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
        servers.append({
            "urls": turn_urls,
            "username": username,
            "credential": base64.b64encode(digest).decode("ascii"),
        })
    return servers


@login_required
def call_lobby(request, group_id):
    group = _member_group(request, group_id)
    with transaction.atomic():
        call = GroupCall.objects.select_for_update().filter(
            group=group, ended_at__isnull=True
        ).order_by("-created_at").first()
        if call and call.created_at < timezone.now() - timezone.timedelta(seconds=IDLE_CALL_TTL_SECONDS):
            cutoff = timezone.now() - timezone.timedelta(seconds=PRESENCE_TTL_SECONDS)
            if not GroupCallParticipant.objects.filter(
                call=call, left_at__isnull=True, last_seen_at__gte=cutoff
            ).exists():
                call.ended_at = timezone.now()
                call.save(update_fields=["ended_at"])
                call.signals.all().delete()
                call = None
    return render(request, "sharing/call_lobby.html", {"group": group, "call": call})


@login_required
@require_POST
def call_start(request, group_id):
    # The group row serializes concurrent starts on the production MySQL backend.
    with transaction.atomic():
        group = get_object_or_404(StudyGroup.objects.select_for_update(), pk=group_id)
        if not Membership.objects.filter(group=group, user=request.user).exists():
            raise PermissionDenied("You are not a member of this group.")
        call = GroupCall.objects.filter(group=group, ended_at__isnull=True).order_by("-created_at").first()
        if call is None:
            call = GroupCall.objects.create(group=group, started_by=request.user)
    return JsonResponse({"url": reverse("sharing:call_room", args=[call.pk])})


@login_required
def call_room(request, call_id):
    call, active = _active_call(call_id, request.user)
    if not active:
        return redirect("sharing:call_lobby", group_id=call.group_id)
    return render(request, "sharing/call_room.html", {"call": call, "group": call.group})


@login_required
@require_POST
def call_join(request, call_id):
    with transaction.atomic():
        call = get_object_or_404(GroupCall.objects.select_for_update().select_related("group"), pk=call_id)
        if not Membership.objects.filter(group=call.group, user=request.user).exists():
            raise PermissionDenied("You are not a member of this group.")
        if call.ended_at is not None:
            return JsonResponse({"error": "This call has ended."}, status=410)
        now = timezone.now()
        participant, _ = GroupCallParticipant.objects.get_or_create(
            call=call,
            user=request.user,
            defaults={"last_seen_at": now},
        )
        participant.joined_at = now
        participant.last_seen_at = now
        participant.left_at = None
        participant.audio_muted = False
        participant.video_enabled = False
        participant.save(update_fields=["joined_at", "last_seen_at", "left_at", "audio_muted", "video_enabled"])
    return JsonResponse({
        "participant_id": request.user.pk,
        "name": request.user.get_full_name() or request.user.get_short_name(),
        "ice_servers": _ice_servers(request.user),
    })


@login_required
@require_POST
def call_poll(request, call_id):
    call, active = _active_call(call_id, request.user)
    if not active:
        return JsonResponse({"error": "This call has ended."}, status=410)
    participant = _participant(call, request.user)
    if participant is None:
        return JsonResponse({"error": "Join this call before polling."}, status=409)
    body = _json_body(request)
    if body is None:
        return JsonResponse({"error": "Invalid request body."}, status=400)
    now = timezone.now()
    participant.last_seen_at = now
    participant.audio_muted = bool(body.get("audio_muted", False))
    participant.video_enabled = bool(body.get("video_enabled", False))
    participant.save(update_fields=["last_seen_at", "audio_muted", "video_enabled"])

    cutoff = now - timezone.timedelta(seconds=PRESENCE_TTL_SECONDS)
    peers = GroupCallParticipant.objects.filter(
        call=call, left_at__isnull=True, last_seen_at__gte=cutoff,
        user__memberships__group_id=call.group_id,
    ).select_related("user").order_by("joined_at", "pk")
    participants = [{
        "id": peer.user_id,
        "name": peer.user.get_full_name() or peer.user.get_short_name(),
        "audio_muted": peer.audio_muted,
        "video_enabled": peer.video_enabled,
    } for peer in peers]
    try:
        after = max(0, int(request.GET.get("after", "0")))
    except ValueError:
        after = 0
    signals = GroupCallSignal.objects.filter(
        call=call, recipient=request.user, pk__gt=after
    ).order_by("pk")[:100]
    return JsonResponse({
        "participants": participants,
        "signals": [{"id": item.pk, "sender": item.sender_id, "kind": item.kind,
                     "payload": item.payload} for item in signals],
        "latest_signal_id": signals[len(signals) - 1].pk if signals else after,
    })


@login_required
@require_POST
def call_signal(request, call_id):
    call, active = _active_call(call_id, request.user)
    if not active:
        return JsonResponse({"error": "This call has ended."}, status=410)
    sender = _participant(call, request.user)
    body = _json_body(request)
    if sender is None:
        return JsonResponse({"error": "Join this call before sending signals."}, status=409)
    if body is None or len(request.body) > MAX_SIGNAL_BYTES:
        return JsonResponse({"error": "Invalid or oversized signal."}, status=400)
    kind, target_id, payload = body.get("kind"), body.get("to"), body.get("payload")
    if kind not in GroupCallSignal.Kind.values or not isinstance(payload, dict):
        return JsonResponse({"error": "Invalid signal."}, status=400)
    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid participant."}, status=400)
    if target_id == request.user.pk:
        return JsonResponse({"error": "Invalid participant."}, status=400)
    target = GroupCallParticipant.objects.filter(
        call=call, user_id=target_id, left_at__isnull=True,
        last_seen_at__gte=timezone.now() - timezone.timedelta(seconds=PRESENCE_TTL_SECONDS),
        user__memberships__group_id=call.group_id,
    ).first()
    if target is None:
        return JsonResponse({"error": "That participant is no longer in the call."}, status=404)
    signal = GroupCallSignal.objects.create(
        call=call, sender=request.user, recipient_id=target_id, kind=kind, payload=payload
    )
    return JsonResponse({"id": signal.pk}, status=201)


@login_required
@require_POST
def call_leave(request, call_id):
    with transaction.atomic():
        call = get_object_or_404(GroupCall.objects.select_for_update().select_related("group"), pk=call_id)
        if not Membership.objects.filter(group=call.group, user=request.user).exists():
            raise PermissionDenied("You are not a member of this group.")
        now = timezone.now()
        GroupCallParticipant.objects.filter(
            call=call, user=request.user, left_at__isnull=True
        ).update(left_at=now)
        cutoff = now - timezone.timedelta(seconds=PRESENCE_TTL_SECONDS)
        if not GroupCallParticipant.objects.filter(
            call=call, left_at__isnull=True, last_seen_at__gte=cutoff
        ).exists() and call.ended_at is None:
            call.ended_at = now
            call.save(update_fields=["ended_at"])
            call.signals.all().delete()
    return JsonResponse({"left": True})
