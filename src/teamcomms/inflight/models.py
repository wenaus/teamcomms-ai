"""Current work control state; immutable history lives in Entries revisions."""

from django.db import models
from django.db.models import Q


class Work(models.Model):
    entry = models.OneToOneField("teamcomms_entries.Entry", primary_key=True, on_delete=models.PROTECT)
    owner = models.ForeignKey("teamcomms_service.Participant", on_delete=models.PROTECT, related_name="owned_work")
    generation = models.PositiveIntegerField(default=1)
    executor = models.ForeignKey("teamcomms_service.Participant", null=True, on_delete=models.PROTECT, related_name="executed_work")
    session = models.ForeignKey("teamcomms_comms.Session", null=True, on_delete=models.PROTECT)
    state = models.CharField(max_length=16, default="planned")
    form = models.CharField(max_length=16, default="work")
    visibility = models.CharField(max_length=16, default="operator")
    parent = models.ForeignKey("self", null=True, on_delete=models.PROTECT, related_name="children")
    detail = models.JSONField(default=dict)
    handoff = models.JSONField(null=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(generation__gte=1), name="tc_work_generation"),
                       models.CheckConstraint(condition=Q(state__in=["planned", "active", "blocked", "completed", "failed", "canceled"]), name="tc_work_state")]
        indexes = [models.Index(fields=["state", "visibility"], name="tc_work_view")]


class Dependency(models.Model):
    work = models.ForeignKey(Work, on_delete=models.PROTECT, related_name="dependencies")
    prerequisite = models.ForeignKey(Work, on_delete=models.PROTECT, related_name="dependents")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["work", "prerequisite"], name="tc_work_dependency")]


class WorkReceipt(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    entry = models.ForeignKey("teamcomms_entries.Entry", on_delete=models.PROTECT)
    author = models.ForeignKey("teamcomms_service.Participant", on_delete=models.PROTECT)
    request = models.JSONField()
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
