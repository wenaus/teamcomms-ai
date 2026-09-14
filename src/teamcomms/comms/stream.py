"""Finite server-sent event streams with PostgreSQL wake-up and durable replay."""

from contextlib import aclosing
import json
import logging
import time

from django.conf import settings
from django.utils import timezone
import psycopg
from psycopg import sql
from starlette.responses import JSONResponse, StreamingResponse

from teamcomms.service.access import AccessError, Principal, current_principal
from teamcomms.service.dispatch import database_call
from teamcomms.service.models import Credential
from . import operations
from .schemas import Stream

logger = logging.getLogger(__name__)
RECHECK_SECONDS = 5


def refresh(actor):
    credential = Credential.objects.select_related("membership").filter(pk=actor.credential_id).first()
    if (credential is None or credential.revoked_at is not None or not credential.membership.active
            or (credential.expires_at and credential.expires_at <= timezone.now())):
        raise AccessError("Credential no longer active", 401)
    member = credential.membership
    return Principal(member.participant_id, member.team_id, member.id, credential.id, member.role,
                     frozenset(credential.scopes))


def read_page(actor, query):
    return operations.get_messages(refresh(actor), query)


def listener_params():
    database = settings.DATABASES["default"]
    params = {key: database[source] for source, key in
        (("NAME", "dbname"), ("HOST", "host"), ("PORT", "port"), ("USER", "user"), ("PASSWORD", "password"))
        if database.get(source)}
    params.update(database.get("OPTIONS", {}))
    params.setdefault("connect_timeout", 5)
    return params


def event(name, data, cursor=None):
    prefix = f"id: {cursor}\n" if cursor is not None else ""
    return f"{prefix}event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_messages(request):
    actor = current_principal.get()
    listener = None
    try:
        values = dict(request.query_params)
        if request.headers.get("last-event-id"):
            values["after"] = request.headers["last-event-id"]
        query = Stream.model_validate(values)
        # Validate scope/session/cursor before opening a dedicated listener.
        await database_call(read_page, actor, query)
        listener = await psycopg.AsyncConnection.connect(**listener_params(), autocommit=True)
        await listener.execute(sql.SQL("LISTEN {}").format(sql.Identifier(operations.channel(query.session_id))))
        # LISTEN is committed before the authoritative read: closes the subscribe/read race.
        first_page = await database_call(read_page, actor, query)
    except (ValueError, AccessError, psycopg.Error) as error:
        if listener is not None:
            await listener.close()
        if isinstance(error, AccessError):
            message, status = str(error), error.status
        elif isinstance(error, ValueError):
            message, status = "Invalid stream parameters", 400
        else:
            logger.error("Comms listener setup failed")
            message, status = "Database unavailable", 503
        return JSONResponse({"error": message}, status_code=status)
    except BaseException:
        if listener is not None:
            await listener.close()
        raise

    async def generate():
        deadline = time.monotonic() + query.duration
        page = first_page
        try:
            yield event("ready", {"session_id": str(query.session_id), "after": query.after})
            while True:
                for row in page["messages"]:
                    yield event("message", row, row["sequence"])
                query.after = page["next_after"]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    yield event("reconnect", {"after": query.after})
                    break
                if not page["has_more"]:
                    async with aclosing(listener.notifies(timeout=min(RECHECK_SECONDS, remaining), stop_after=1)) as notices:
                        async for _ in notices:
                            break
                    # Includes receipt/retry changes to older deliveries; clients reconcile
                    # their durable pending queue without resetting the publication cursor.
                    yield event("refresh", {"session_id": str(query.session_id)})
                page = await database_call(read_page, actor, query)
        except AccessError as error:
            yield event("error", {"error": str(error), "status": error.status})
        except psycopg.Error:
            logger.error("Comms listener connection failed")
            yield event("error", {"error": "Database unavailable", "status": 503})
        finally:
            await listener.close()

    return StreamingResponse(generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
