"""Per-session notice and choices made only by the authenticated human operator."""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from teamcomms.comms.directory import lock, own_session
from teamcomms.comms.models import Session
from teamcomms.service.access import AccessError
from teamcomms.service.models import Participant
from .models import Sharing, SharingChoice

NOTICE_VERSION = 1
NOTICE = ("TeamComms records and shares this session's visible AI replies with team members "
          "and their AI sessions. Also share the messages you type in this session? "
          "AI replies may quote your input. Your typed messages stay private unless you choose "
          "Share my messages. You can stop future human sharing at any time; already shared "
          "records are retained.")


def accessible_session(actor, session_id):
    row = Session.objects.select_related("membership__participant__operator").filter(
        pk=session_id, membership__team_id=actor.team_id).filter(
        Q(membership__participant_id=actor.participant_id) |
        Q(membership__participant__operator_id=actor.participant_id)).first()
    if row is None:
        raise AccessError("Session not found for this participant or human operator", 404)
    return row


def sharing_record(session, state):
    def stamp(value):
        return value.isoformat() if value else None
    return {"session_id": str(session.id), "session_name": session.name,
            "notice_version": NOTICE_VERSION, "notice": NOTICE,
            "notice_at": stamp(state.notice_at) if state else None,
            "assistant_since": stamp(state.notice_at) if state else None,
            "human_choice": state.human_choice if state else "pending",
            "human_since": stamp(state.human_since) if state else None,
            "human_changed_at": stamp(state.human_changed_at) if state else None,
            "choice_path": f"/dialog/sharing/{session.id}"}


def get_dialog_sharing(actor, request):
    actor.require("dialog:read")
    session = accessible_session(actor, request.session_id)
    return sharing_record(session, Sharing.objects.filter(session=session).first())


@transaction.atomic
def record_sharing_notice(actor, request):
    actor.require("dialog:write")
    session = own_session(actor, request.session_id)
    lock(f"tc-dialog-sharing:{session.id}")
    state, _ = Sharing.objects.get_or_create(session=session)
    if state.notice_version < NOTICE_VERSION:
        state.notice_version, state.notice_at = NOTICE_VERSION, timezone.now()
        state.save(update_fields=["notice_version", "notice_at"])
    # Transport acceptance records an offered notice, never a human decision/read.
    return sharing_record(session, state)


@transaction.atomic
def choose_human_sharing(actor, request, *, human_interactive=False):
    actor.require("dialog:write")
    person = Participant.objects.get(pk=actor.participant_id)
    if not human_interactive or person.kind != "human":
        raise AccessError("Only the signed-in human operator can choose human sharing")
    session = accessible_session(actor, request.session_id)
    lock(f"tc-dialog-sharing:{session.id}")
    lock(f"tc-sharing-choice:{request.operation_id}")
    payload = request.model_dump(mode="json")
    prior = SharingChoice.objects.filter(pk=request.operation_id).first()
    if prior:
        if prior.actor_id != actor.participant_id or prior.request != payload:
            raise AccessError("Sharing choice ID already has a different request", 409)
        return prior.result
    state, _ = Sharing.objects.get_or_create(session=session)
    now = timezone.now()
    # The same notice appears immediately above both explicit browser choices.
    if state.notice_version < NOTICE_VERSION:
        state.notice_version, state.notice_at = NOTICE_VERSION, now
    state.human_choice = request.choice
    state.human_since = now if request.choice == "share" else None
    state.human_changed_at = now
    state.save()
    result = sharing_record(session, state)
    SharingChoice.objects.create(id=request.operation_id, session=session, actor=person,
                                request=payload, result=result)
    return result


def permits(state, role, occurred_at):
    if role not in {"human", "assistant"}:
        return True
    if not state or state.notice_version != NOTICE_VERSION or not state.notice_at:
        return False
    if role == "assistant":
        return occurred_at >= state.notice_at
    return (state.human_choice == "share" and state.human_since is not None
            and occurred_at >= state.human_since)
