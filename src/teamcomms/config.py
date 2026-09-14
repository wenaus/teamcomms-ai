"""Explicit installation configuration; no personal configuration discovery."""

import os

from django.core.exceptions import ImproperlyConfigured
from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict


def database_config():
    value = os.environ.get("TEAMCOMMS_DATABASE_URL", "")
    if not value.startswith(("postgresql://", "postgres://")):
        raise ImproperlyConfigured("TEAMCOMMS_DATABASE_URL must be a PostgreSQL URL")
    try:
        parts = conninfo_to_dict(value)
    except ProgrammingError:
        raise ImproperlyConfigured("Invalid TEAMCOMMS_DATABASE_URL") from None
    if not parts.get("dbname"):
        raise ImproperlyConfigured("TEAMCOMMS_DATABASE_URL must name a database")
    result = {"ENGINE": "django.db.backends.postgresql", "CONN_MAX_AGE": 0}
    for source, target in (("dbname", "NAME"), ("user", "USER"),
                           ("password", "PASSWORD"), ("host", "HOST"), ("port", "PORT")):
        result[target] = parts.pop(source, "")
    result["OPTIONS"] = parts
    return result


def allowed_hosts():
    hosts = [v.strip() for v in os.environ.get(
        "TEAMCOMMS_ALLOWED_HOSTS", "localhost,127.0.0.1"
    ).split(",") if v.strip()]
    if not hosts or any("*" in h or "/" in h or ":" in h for h in hosts):
        raise ImproperlyConfigured("TEAMCOMMS_ALLOWED_HOSTS requires explicit hostnames")
    return hosts
