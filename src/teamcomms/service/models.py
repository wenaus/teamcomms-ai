"""Installation identity, membership, and credential records."""

import uuid

from django.db import models
from django.db.models import Q


class Team(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    singleton = models.BooleanField(default=True, unique=True, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(singleton=True), name="tc_one_team")]


class Participant(models.Model):
    class Kind(models.TextChoices):
        HUMAN = "human"
        AI = "ai"
        PROGRAM = "program"
        CONNECTOR = "connector"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    operator = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=Q(kind__in=["human", "ai", "program", "connector"]), name="tc_participant_kind"
        )]


class Membership(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin"
        MEMBER = "member"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey(Team, on_delete=models.PROTECT)
    participant = models.OneToOneField(Participant, on_delete=models.PROTECT)
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.MEMBER)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=Q(role__in=["admin", "member"]), name="tc_membership_role"
        )]


class Credential(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT)
    digest = models.CharField(max_length=64, unique=True)
    scopes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
