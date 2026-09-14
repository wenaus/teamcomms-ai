"""Standalone Django settings for the authoritative PostgreSQL store."""

import os

from teamcomms.config import allowed_hosts, database_config

SECRET_KEY = os.environ.get("TEAMCOMMS_SECRET_KEY", "")
DEBUG = False
USE_TZ = True
TIME_ZONE = "UTC"
INSTALLED_APPS = ["teamcomms.service.apps.ServiceConfig", "teamcomms.entries.apps.EntriesConfig", "teamcomms.comms.apps.CommsConfig", "teamcomms.dialog.apps.DialogConfig"]
DATABASES = {"default": database_config()}
ALLOWED_HOSTS = allowed_hosts()
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
