"""Immutable attributed transcript events and replay identity."""

from django.db import models
from django.db.models import Q


class Event(models.Model):
    session = models.ForeignKey("teamcomms_comms.Session", on_delete=models.PROTECT)
    source_id = models.CharField(max_length=240)
    source_sequence = models.PositiveBigIntegerField()
    run_id = models.CharField(max_length=160, blank=True)
    occurred_at = models.DateTimeField()
    captured_at = models.DateTimeField(auto_now_add=True)
    role = models.CharField(max_length=16)
    phase = models.CharField(max_length=16)
    content = models.TextField()
    topic = models.CharField(max_length=160, blank=True)
    message = models.ForeignKey("teamcomms_comms.Message", null=True, on_delete=models.PROTECT)
    attribution = models.JSONField()
    request = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["session", "source_id"], name="tc_dialog_source")]
        indexes = [models.Index(fields=["session", "-id"], name="tc_dialog_session"),
                   models.Index(fields=["topic", "occurred_at"], name="tc_dialog_topic_time")]


class Sharing(models.Model):
    session = models.OneToOneField("teamcomms_comms.Session", primary_key=True, on_delete=models.PROTECT)
    notice_version = models.PositiveIntegerField(default=0)
    notice_at = models.DateTimeField(null=True)
    human_choice = models.CharField(max_length=12, default="pending")
    human_since = models.DateTimeField(null=True)
    human_changed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(human_choice__in=["pending", "share", "private"]),
                                              name="tc_dialog_sharing_choice")]


class SharingChoice(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    session = models.ForeignKey("teamcomms_comms.Session", on_delete=models.PROTECT)
    actor = models.ForeignKey("teamcomms_service.Participant", on_delete=models.PROTECT)
    request = models.JSONField()
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
