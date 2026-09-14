"""Authentication and request-local identity shared by HTTP and MCP."""

from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import secrets
from uuid import UUID

from django.utils import timezone

from .models import Credential

SCOPES = frozenset({"directory:read", "directory:write", "credentials:write", "entries:read", "entries:write", "sessions:write", "comms:read", "comms:write", "dialog:read", "dialog:write", "inflight:read", "inflight:write", "capcom:read", "capcom:write"})
MEMBER_SCOPES = frozenset({"directory:read", "entries:read", "entries:write", "sessions:write", "comms:read", "comms:write", "dialog:read", "dialog:write", "inflight:read", "inflight:write", "capcom:read", "capcom:write"})


class AccessError(Exception):
    def __init__(self, message, status=403):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Principal:
    participant_id: UUID
    team_id: UUID
    membership_id: UUID
    credential_id: UUID | None
    role: str
    scopes: frozenset[str]

    def require(self, scope, *, admin=False):
        if scope not in self.scopes or (admin and self.role != "admin"):
            raise AccessError("Insufficient permission")


current_principal: ContextVar[Principal] = ContextVar("teamcomms_principal")
current_authentication: ContextVar = ContextVar("teamcomms_authentication")


def mint_credential(membership, scopes, expires_at=None):
    token = "tc_" + secrets.token_urlsafe(32)
    credential = Credential.objects.create(
        membership=membership,
        digest=hashlib.sha256(token.encode()).hexdigest(),
        scopes=sorted(scopes), expires_at=expires_at,
    )
    return credential, token


def authenticate(token):
    if not token or len(token) > 512:
        raise AccessError("Invalid credential", 401)
    credential = Credential.objects.select_related("membership").filter(
        digest=hashlib.sha256(token.encode()).hexdigest()
    ).first()
    if (credential is None or credential.revoked_at is not None
            or not credential.membership.active
            or (credential.expires_at is not None and credential.expires_at <= timezone.now())):
        raise AccessError("Invalid credential", 401)
    member = credential.membership
    return Principal(member.participant_id, member.team_id, member.id,
                     credential.id, member.role, frozenset(credential.scopes))
