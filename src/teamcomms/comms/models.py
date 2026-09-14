"""Durable directory, publication snapshots, and independent destination receipts."""

import uuid
from django.db import models
from django.db.models import Q


class Resource(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey("teamcomms_service.Team", on_delete=models.PROTECT)
    key = models.CharField(max_length=240)
    kind = models.CharField(max_length=20)
    name = models.CharField(max_length=160)
    host = models.CharField(max_length=160, blank=True)
    project = models.ForeignKey("self", null=True, on_delete=models.PROTECT)
    aliases = models.JSONField(default=list)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["team", "key"], name="tc_resource_key")]


class Group(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey("teamcomms_service.Team", on_delete=models.PROTECT)
    key = models.CharField(max_length=160)
    name = models.CharField(max_length=160)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["team", "key"], name="tc_group_key")]


class Session(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    membership = models.ForeignKey("teamcomms_service.Membership", on_delete=models.PROTECT)
    native_id = models.CharField(max_length=160)
    client = models.CharField(max_length=80)
    host = models.CharField(max_length=160)
    name = models.CharField(max_length=160)
    workspace = models.CharField(max_length=4096, blank=True)
    model = models.CharField(max_length=120, blank=True)
    effort = models.CharField(max_length=40, blank=True)
    capabilities = models.JSONField(default=list)
    delivery_mode = models.CharField(max_length=40, default="pull")
    state = models.CharField(max_length=16, default="unknown")
    last_seen = models.DateTimeField()
    resources = models.ManyToManyField(Resource)
    # Incremented under the session row lock; cursor order follows commit order.
    last_sequence = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["membership", "host", "client", "native_id"], name="tc_session_native")]
        indexes = [models.Index(fields=["host", "last_seen"], name="tc_session_host_seen")]


class Subscription(models.Model):
    session = models.ForeignKey(Session, on_delete=models.PROTECT)
    group = models.ForeignKey(Group, null=True, on_delete=models.PROTECT)
    topic = models.CharField(max_length=160, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["session", "group"], name="tc_subscription_group"),
            models.UniqueConstraint(fields=["session", "topic"], condition=Q(group__isnull=True), name="tc_subscription_topic"),
            models.CheckConstraint(condition=(Q(group__isnull=False, topic="") | (Q(group__isnull=True) & ~Q(topic=""))), name="tc_subscription_target"),
        ]


class Message(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    team = models.ForeignKey("teamcomms_service.Team", on_delete=models.PROTECT)
    author = models.ForeignKey("teamcomms_service.Participant", on_delete=models.PROTECT)
    sender_session = models.ForeignKey(Session, null=True, on_delete=models.PROTECT)
    reply_to = models.ForeignKey("self", null=True, on_delete=models.PROTECT)
    envelope = models.JSONField()
    author_snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["author", "-created_at"], name="tc_message_author")]


class MessageReference(models.Model):
    message = models.ForeignKey(Message, on_delete=models.PROTECT)
    entry = models.ForeignKey("teamcomms_entries.Entry", on_delete=models.PROTECT)
    revision = models.ForeignKey("teamcomms_entries.Revision", null=True, on_delete=models.PROTECT)


class Delivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(Message, related_name="deliveries", on_delete=models.PROTECT)
    session = models.ForeignKey(Session, on_delete=models.PROTECT)
    sequence = models.PositiveBigIntegerField()
    state = models.CharField(max_length=16, default="pending")
    revision = models.PositiveIntegerField(default=1)
    detail = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True)
    considered_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["message", "session"], name="tc_delivery_destination"),
            models.UniqueConstraint(fields=["session", "sequence"], name="tc_delivery_sequence"),
            models.CheckConstraint(condition=Q(state__in=["pending", "written", "accepted", "uncertain", "failed"]), name="tc_delivery_state"),
        ]


class Receipt(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    delivery = models.ForeignKey(Delivery, related_name="receipts", on_delete=models.PROTECT)
    author = models.ForeignKey("teamcomms_service.Participant", on_delete=models.PROTECT)
    request = models.JSONField()
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
