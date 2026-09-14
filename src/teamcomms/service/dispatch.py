"""Shared operation dispatch with explicit database connection lifetime."""

import logging

from asgiref.sync import sync_to_async
from django.db import DatabaseError, close_old_connections

from .access import AccessError, current_principal

logger = logging.getLogger(__name__)


def _database_call(function, *args):
    close_old_connections()
    try:
        return function(*args)
    except DatabaseError:
        logger.error("TeamComms database operation failed")
        raise AccessError("Database unavailable", 503) from None
    finally:
        close_old_connections()


async def database_call(function, *args):
    return await sync_to_async(_database_call, thread_sensitive=True)(function, *args)


async def invoke(operation, request=None):
    actor = current_principal.get()
    args = (actor,) if request is None else (actor, request)
    return await database_call(operation, *args)
