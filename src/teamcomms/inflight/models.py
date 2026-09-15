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


class ResourceCustody(models.Model):
    resource = models.OneToOneField("teamcomms_comms.Resource", primary_key=True, on_delete=models.PROTECT)
    custodian = models.ForeignKey("teamcomms_service.Participant", on_delete=models.PROTECT)
    generation = models.PositiveIntegerField(default=1)
    protection = models.CharField(max_length=20, default="advisory")
    transfer = models.JSONField(null=True)


class WorkOffer(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    work = models.ForeignKey(Work, on_delete=models.PROTECT, related_name="offers")
    generation = models.PositiveIntegerField()
    specification = models.JSONField()
    expires_at = models.DateTimeField()
    state = models.CharField(max_length=12, default="open")
    created_at = models.DateTimeField(auto_now_add=True)


class Claim(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    offer = models.OneToOneField(WorkOffer, on_delete=models.PROTECT)
    work = models.ForeignKey(Work, on_delete=models.PROTECT, related_name="claims")
    holder = models.ForeignKey("teamcomms_service.Participant", on_delete=models.PROTECT)
    session = models.ForeignKey("teamcomms_comms.Session", null=True, on_delete=models.PROTECT)
    generation = models.PositiveIntegerField()
    state = models.CharField(max_length=12, default="active")
    deadline = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["work"], condition=Q(state="active"), name="tc_claim_one_active")]


class Reservation(models.Model):
    claim = models.ForeignKey(Claim, on_delete=models.PROTECT, related_name="reservations")
    resource = models.ForeignKey(ResourceCustody, on_delete=models.PROTECT, related_name="reservations")
    generation = models.PositiveIntegerField()
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["resource"], condition=Q(active=True), name="tc_reservation_exclusive"),
                       models.UniqueConstraint(fields=["claim", "resource"], name="tc_claim_resource")]


class CoordinationReceipt(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    team = models.ForeignKey("teamcomms_service.Team", on_delete=models.PROTECT)
    author = models.ForeignKey("teamcomms_service.Participant", on_delete=models.PROTECT)
    request = models.JSONField()
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class GuardRun(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    claim = models.ForeignKey(Claim, on_delete=models.PROTECT, related_name="guard_runs")
    resource_ids = models.JSONField()
    command_sha256 = models.CharField(max_length=64)
    state = models.CharField(max_length=12, default="active")
    outcome = models.JSONField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["claim"], condition=Q(state="active"), name="tc_guard_one_active")]


class ExecutionRun(models.Model):
    id = models.UUIDField(primary_key=True,editable=False)
    claim = models.OneToOneField(Claim,on_delete=models.PROTECT,related_name='execution_run')
    command_sha256 = models.CharField(max_length=64)
    specification = models.JSONField()
    state = models.CharField(max_length=12,default='active')
    result = models.JSONField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
