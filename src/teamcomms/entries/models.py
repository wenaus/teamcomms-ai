"""Stable entries, immutable revision snapshots, and revision references."""

import uuid

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.db.models import Q


class Entry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey("teamcomms_service.Team", on_delete=models.PROTECT)
    slug = models.CharField(max_length=120, null=True, blank=True)
    kind = models.CharField(max_length=32)
    revision = models.PositiveIntegerField(default=1)
    state = models.JSONField()
    search_vector = SearchVectorField(null=True)
    created_by = models.ForeignKey("teamcomms_service.Participant", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["team", "slug"], name="tc_entry_slug"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="tc_entry_revision_positive"),
        ]
        indexes = [
            models.Index(fields=["team", "-modified_at", "id"], name="tc_entry_modified"),
            models.Index(fields=["team", "kind"], name="tc_entry_kind"),
            GinIndex(fields=["state"], name="tc_entry_state"),
            GinIndex(fields=["search_vector"], name="tc_entry_search"),
        ]


class Revision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey(Entry, related_name="versions", on_delete=models.PROTECT)
    number = models.PositiveIntegerField()
    base_revision = models.PositiveIntegerField()
    state = models.JSONField()
    author = models.ForeignKey("teamcomms_service.Participant", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    restored_from = models.ForeignKey("self", null=True, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["entry", "number"], name="tc_revision_number"),
            models.UniqueConstraint(fields=["entry", "id"], name="tc_revision_entry_id"),
            models.CheckConstraint(condition=Q(number__gte=1), name="tc_revision_positive"),
        ]


class RevisionReference(models.Model):
    source = models.ForeignKey(Revision, related_name="references", on_delete=models.PROTECT)
    target_entry = models.ForeignKey(Entry, on_delete=models.PROTECT)
    target_revision = models.ForeignKey(Revision, null=True, on_delete=models.PROTECT)
    relation = models.CharField(max_length=60)
