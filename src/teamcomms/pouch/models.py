"""The permanent team-to-document binding; content lives in Entries."""

from django.db import models


class Pouch(models.Model):
    team = models.OneToOneField("teamcomms_service.Team", on_delete=models.PROTECT, primary_key=True)
    entry = models.OneToOneField("teamcomms_entries.Entry", on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
