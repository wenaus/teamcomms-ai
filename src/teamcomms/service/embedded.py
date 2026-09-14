"""Host-authenticated participant mapping and explicit browser request protection."""

from dataclasses import dataclass, field
import logging
from uuid import UUID

from django.db import transaction

from .access import AccessError, Principal, SCOPES
from .authentication import AuthenticationSession
from .dispatch import database_call
from .models import HostIdentityBinding, Membership, Participant, Team

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HostIdentity:
    subject: str
    name: str
    kind: str = "human"
    role: str = "member"
    scopes: frozenset[str] = field(default_factory=frozenset)
    active: bool = True
    session_authenticated: bool = True
    operator: "HostIdentity | None" = None

    def validate(self):
        if (not isinstance(self.subject, str) or not self.subject.strip() or len(self.subject) > 255
                or not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 120
                or self.kind not in Participant.Kind.values or self.role not in Membership.Role.values
                or not isinstance(self.active, bool) or not isinstance(self.session_authenticated, bool)
                or not set(self.scopes) <= SCOPES - {"credentials:write"}):
            raise ValueError("Invalid host identity or permissions")
        if self.operator is not None:
            if (self.kind != "ai" or not isinstance(self.operator, HostIdentity)
                    or self.operator.kind != "human" or self.operator.operator is not None
                    or self.operator.subject == self.subject):
                raise ValueError("AI operator must be a distinct human host identity")
            self.operator.validate()


@transaction.atomic
def map_identity(provider, team_id, identity):
    # The installation row serializes first-use enrollment across processes.
    # Subjects are stable host IDs; display names and token values never match accounts.
    Team.objects.select_for_update().get(pk=team_id)
    operator_id = None
    if identity.operator:
        operator = map_identity(provider, team_id, identity.operator)
        if not identity.operator.active:
            raise AccessError("Host operator is inactive", 401)
        operator_id = operator.participant_id
    binding = HostIdentityBinding.objects.select_related("membership__participant").filter(
        provider=provider, subject=identity.subject).first()
    if binding is None:
        person = Participant.objects.create(name=identity.name, kind=identity.kind, operator_id=operator_id)
        member = Membership.objects.create(team_id=team_id, participant=person,
                                            role=identity.role, active=identity.active)
        HostIdentityBinding.objects.create(provider=provider, subject=identity.subject, membership=member)
    else:
        member, person = binding.membership, binding.membership.participant
        if member.team_id != team_id or person.kind != identity.kind or person.operator_id != operator_id:
            raise AccessError("Host identity binding changed", 401)
        if person.name != identity.name:
            person.name = identity.name
            person.save(update_fields=["name"])
        if member.role != identity.role or member.active != identity.active:
            member.role, member.active = identity.role, identity.active
            member.save(update_fields=["role", "active"])
    return Principal(person.id, member.team_id, member.id, None, identity.role, frozenset(identity.scopes))


class HostAuthentication:
    """Host callbacks verify identity, revalidate access, and enforce host CSRF.

    All callbacks are async. resolve(scope) and revalidate(scope, original)
    return HostIdentity or raise AccessError. check_csrf(scope, body, identity)
    returns True only after the host has validated an unsafe session request.
    """

    def __init__(self, *, provider, team_id, resolve, revalidate, check_csrf=None):
        if not isinstance(provider, str) or not provider.strip() or len(provider) > 120:
            raise ValueError("Host identity provider must be a stable name of at most 120 characters")
        if not callable(resolve) or not callable(revalidate) or (check_csrf is not None and not callable(check_csrf)):
            raise ValueError("Host authentication requires resolve and revalidate callbacks")
        self.provider, self.team_id = provider, UUID(str(team_id))
        self.resolve, self.revalidate, self.check_csrf = resolve, revalidate, check_csrf

    async def _call(self, callback, *args):
        try:
            return await callback(*args)
        except AccessError:
            raise
        except Exception as error:
            logger.error("Host authentication callback failed (%s)", type(error).__name__)
            raise AccessError("Host authentication unavailable", 503) from None

    async def _actor(self, identity):
        if not isinstance(identity, HostIdentity):
            raise AccessError("Host authentication returned no identity", 401)
        try:
            identity.validate()
        except (TypeError, ValueError):
            raise AccessError("Invalid host authentication result", 503) from None
        try:
            actor = await database_call(map_identity, self.provider, self.team_id, identity)
        except Team.DoesNotExist:
            raise AccessError("Embedded team is not provisioned", 503) from None
        if not identity.active:
            raise AccessError("Host account is inactive", 401)
        return actor

    async def authenticate(self, scope, body):
        identity = await self._call(self.resolve, scope)
        if not isinstance(identity, HostIdentity):
            raise AccessError("Host authentication required", 401)
        if identity.session_authenticated and scope["method"] not in {"GET", "HEAD", "OPTIONS", "TRACE"}:
            if self.check_csrf is None or await self._call(self.check_csrf, scope, body, identity) is not True:
                raise AccessError("Host CSRF validation required", 403)
        actor = await self._actor(identity)

        async def refresh():
            current = await self._call(self.revalidate, scope, identity)
            if (not isinstance(current, HostIdentity) or current.subject != identity.subject
                    or current.kind != identity.kind
                    or current.session_authenticated != identity.session_authenticated):
                raise AccessError("Host authentication identity changed", 401)
            renewed = await self._actor(current)
            if (renewed.participant_id, renewed.team_id, renewed.membership_id) != (
                    actor.participant_id, actor.team_id, actor.membership_id):
                raise AccessError("Host authentication identity changed", 401)
            return renewed

        return AuthenticationSession(actor, refresh)
