"""Shared, transport-independent directory and credential operations."""

from django.db import transaction
from django.utils import timezone

from .access import AccessError, MEMBER_SCOPES, SCOPES, mint_credential
from .models import Credential, Membership, Participant, Team


def participant_record(member):
    person = member.participant
    return {"participant_id": str(person.id), "name": person.name,
            "kind": person.kind, "operator_id": str(person.operator_id) if person.operator_id else None,
            "role": member.role, "active": member.active}


def whoami(actor):
    member = Membership.objects.select_related("participant", "team").get(pk=actor.membership_id)
    return {**participant_record(member), "team_id": str(actor.team_id),
            "team_name": member.team.name, "scopes": sorted(actor.scopes)}


def list_participants(actor, query):
    actor.require("directory:read")
    members = Membership.objects.filter(team_id=actor.team_id).select_related("participant").order_by("id")
    page = list(members[query.offset:query.offset + query.limit + 1])
    return {"participants": [participant_record(m) for m in page[:query.limit]],
            "next_offset": query.offset + query.limit if len(page) > query.limit else None}


@transaction.atomic
def create_participant(actor, request):
    actor.require("directory:write", admin=True)
    if request.operator_id is not None:
        if request.kind != "ai" or not Membership.objects.filter(
            team_id=actor.team_id, participant_id=request.operator_id,
            participant__kind="human", active=True
        ).exists():
            raise AccessError("Operator must be an active human member associated with an AI", 400)
    participant = Participant.objects.create(name=request.name, kind=request.kind,
                                             operator_id=request.operator_id)
    member = Membership.objects.create(team_id=actor.team_id, participant=participant)
    return participant_record(member)


@transaction.atomic
def issue_credential(actor, request):
    actor.require("credentials:write", admin=True)
    scopes = set(request.scopes)
    if not scopes <= SCOPES or not scopes <= actor.scopes:
        raise AccessError("Requested scopes exceed the issuing credential", 400)
    if request.expires_at is not None and (
        timezone.is_naive(request.expires_at) or request.expires_at <= timezone.now()
    ):
        raise AccessError("Expiry must be a future timezone-aware timestamp", 400)
    member = Membership.objects.filter(team_id=actor.team_id,
                                       participant_id=request.participant_id, active=True).first()
    if member is None:
        raise AccessError("Member not found", 404)
    if member.role != "admin" and scopes - MEMBER_SCOPES:
        raise AccessError("Requested scopes require admin membership", 400)
    credential, token = mint_credential(member, scopes, request.expires_at)
    return {"credential_id": str(credential.id), "token": token,
            "participant_id": str(member.participant_id), "scopes": sorted(scopes)}


def revoke_credential(actor, request):
    actor.require("credentials:write", admin=True)
    changed = Credential.objects.filter(pk=request.credential_id,
        membership__team_id=actor.team_id).update(revoked_at=timezone.now())
    if not changed:
        raise AccessError("Credential not found", 404)
    return {"credential_id": str(request.credential_id), "revoked": True}


@transaction.atomic
def bootstrap(team_name, owner_name):
    """Local provisioning for an empty installation; singleton constraint serializes races."""
    if Team.objects.exists():
        raise ValueError("Installation is already initialized")
    team = Team.objects.create(name=team_name)
    owner = Participant.objects.create(name=owner_name, kind="human")
    member = Membership.objects.create(team=team, participant=owner, role="admin")
    credential, token = mint_credential(member, SCOPES)
    return {"team_id": str(team.id), "participant_id": str(owner.id),
            "credential_id": str(credential.id)}, token
