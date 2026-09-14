"""Request authentication and stream revalidation for standalone and host modes."""

from dataclasses import dataclass
from typing import Awaitable, Callable

from django.utils import timezone

from .access import AccessError, Principal, authenticate
from .dispatch import database_call
from .models import Credential


@dataclass(frozen=True)
class AuthenticationSession:
    actor: Principal
    refresh: Callable[[], Awaitable[Principal]]


def refresh_credential(actor):
    credential = Credential.objects.select_related("membership").filter(pk=actor.credential_id).first()
    if (credential is None or credential.revoked_at is not None or not credential.membership.active
            or (credential.expires_at and credential.expires_at <= timezone.now())):
        raise AccessError("Credential no longer active", 401)
    member = credential.membership
    return Principal(member.participant_id, member.team_id, member.id, credential.id, member.role,
                     frozenset(credential.scopes))


async def standalone_authentication(scope, body):
    values = [v.decode("latin1") for k, v in scope.get("headers", []) if k.lower() == b"authorization"]
    if len(values) != 1 or not values[0].startswith("Bearer "):
        raise AccessError("Bearer credential required", 401)
    actor = await database_call(authenticate, values[0][7:])

    async def refresh():
        return await database_call(refresh_credential, actor)

    return AuthenticationSession(actor, refresh)
