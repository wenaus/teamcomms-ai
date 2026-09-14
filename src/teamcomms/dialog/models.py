"""Immutable attributed transcript events and replay identity."""

from django.db import models


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
