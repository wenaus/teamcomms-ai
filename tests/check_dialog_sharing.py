"""Focused per-session privacy checks, called by check_dialog.py's private DB run."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from uuid import uuid4


async def recorder_privacy(root):
    from teamcomms.connectors.dialog import Recorder, offer_native_notice
    from teamcomms.connectors.state import Store

    class Service:
        def __init__(self):
            self.policy = {"assistant_since": "2000-01-01T00:00:00+00:00",
                           "human_choice": "pending", "human_since": None,
                           "notice_version": 1, "notice_at": None, "notice": "Visible AI replies are shared.",
                           "choice_path": "/dialog/sharing/synthetic"}
            self.events = []
            from types import SimpleNamespace
            self.config = SimpleNamespace(url="http://localhost")

        async def request(self, method, path, body):
            if method == "GET":
                assert path == "/api/dialog/sharing"
                return dict(self.policy)
            if path == "/api/dialog/sharing/notice":
                self.policy["notice_at"] = datetime.now(timezone.utc).isoformat()
                return dict(self.policy)
            assert path == "/api/dialog/events"
            self.events.append(dict(body))
            return {"event_id": len(self.events)}

    def line(role, text, timestamp=None):
        return json.dumps({"timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "type": "response_item", "payload": {"type": "message", "role": role,
                "channel": "final", "content": [{"type": "input_text", "text": text}]}}) + "\n"

    path = root / "sharing-transcript.jsonl"
    path.write_text(line("user", "private before any choice"))
    store = Store(root / "sharing-recorder")
    service = Service()
    class Adapter:
        def __init__(self, fail=False):
            self.calls, self.fail = 0, fail

        async def send(self, text, sender, message_id):
            self.calls += 1
            assert "Do not choose for them" in text
            if self.fail:
                raise RuntimeError("Ambiguous native outcome")
            return {"state": "accepted"}

    adapter = Adapter()
    assert await offer_native_notice(service, store, adapter, str(uuid4()))
    assert await offer_native_notice(service, store, adapter, str(uuid4()))
    assert adapter.calls == 1 and service.policy["human_choice"] == "pending"
    uncertain_store = Store(root / "sharing-uncertain")
    uncertain_service, uncertain_adapter = Service(), Adapter(fail=True)
    assert not await offer_native_notice(uncertain_service, uncertain_store, uncertain_adapter, str(uuid4()))
    assert not await offer_native_notice(uncertain_service, uncertain_store, uncertain_adapter, str(uuid4()))
    assert uncertain_adapter.calls == 1  # No automatic repeat of ambiguous injection.
    uncertain_store.close()
    recorder = Recorder(service, store, str(uuid4()), "codex", path, from_start=True)
    await recorder.once()
    assert not any(e["role"] == "human" for e in service.events)
    with path.open("a") as handle:
        handle.write(line("user", "queued before opt-in, even with future clock", "2099-01-01T00:00:00Z"))
    service.policy.update(human_choice="share", human_since=datetime.now(timezone.utc).isoformat())
    await recorder.once()
    assert not any(e["role"] == "human" for e in service.events)
    with path.open("a") as handle:
        handle.write(line("user", "human after explicit choice") + line("assistant", "visible AI reply"))
    await recorder.once()
    assert [e["content"] for e in service.events if e["role"] == "human"] == ["human after explicit choice"]
    service.policy.update(human_choice="private", human_since=None)
    with path.open("a") as handle:
        handle.write(line("user", "private after stopping") + line("assistant", "AI continues"))
    await recorder.once()
    assert not any(e["content"] == "private after stopping" for e in service.events)
    assert any(e["content"] == "AI continues" for e in service.events)
    service.policy.update(human_choice="share", human_since=datetime.now(timezone.utc).isoformat())
    await recorder.once()
    with path.open("a") as handle:
        handle.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "human without a reliable source time"}]}}) + "\n")
    await recorder.once()
    assert not any(e["content"] == "human without a reliable source time" for e in service.events)
    store.close()


async def detach_check(root):
    from types import SimpleNamespace
    from unittest.mock import patch
    from teamcomms.connectors import cli
    session, native, participant = map(str, (uuid4(), uuid4(), uuid4()))
    config = SimpleNamespace(host="synthetic", state_dir=root / "detached",
                             url="http://localhost", token_file=root / "unused-token")
    args = SimpleNamespace(session_id=session, native_id=native, client="codex",
        transcript=str(root / "selected.jsonl"), config=str(root / "synthetic-connector.json"),
        pid=424242, socket="selected-owning.sock", detach=True, once=False, from_start=False)
    class Service:
        async def get(self, path, **query):
            return {"sessions": [{"session_id": session, "native_id": native, "client": "codex",
                                  "participant_id": participant}], "next_offset": None}

        async def request(self, method, path):
            assert method == "GET" and path == "/api/whoami"
            return {"participant_id": participant}

        async def close(self):
            pass

    launched = []
    def spawn(command, **options):
        import sys
        assert command[:3] == [sys.executable, "-m", "teamcomms.connectors.cli"]
        assert "record" in command and "receive" not in command and "--detach" not in command
        assert command[command.index("--pid")+1] == str(args.pid)
        assert command[command.index("--native-id")+1] == native
        assert options["start_new_session"] is True
        launched.append(command)
        return SimpleNamespace(pid=123456)
    with patch.object(cli, "ServiceClient", return_value=Service()), patch.object(cli.subprocess, "Popen", side_effect=spawn):
        await cli.record_dialog(args, config)
    assert len(launched) == 1


def check(root, operator, operator_token, ai, ai_session):
    from django.db import IntegrityError, transaction
    from django.utils import timezone as django_timezone
    from starlette.testclient import TestClient
    from teamcomms.dialog.models import Event, Sharing, SharingChoice
    from teamcomms.dialog.operations import record_dialog
    from teamcomms.dialog.schemas import RecordDialog, SharingQuery, SharingNotice, HumanSharingChoice
    from teamcomms.dialog.sharing import get_dialog_sharing, record_sharing_notice, choose_human_sharing
    from teamcomms.service.access import AccessError, MEMBER_SCOPES
    from teamcomms.service.asgi import create_app
    from teamcomms.service.embedded import HostAuthentication, HostIdentity
    from teamcomms.service.models import HostIdentityBinding

    def event(role, text, **changes):
        return RecordDialog(session_id=ai_session, source_id=str(uuid4()), source_sequence=1,
            occurred_at=changes.pop("occurred_at", django_timezone.now()), role=role, content=text, **changes)

    before = Event.objects.count()
    assert get_dialog_sharing(ai, SharingQuery(session_id=ai_session))["human_choice"] == "pending"
    assert not record_dialog(ai, event("human", "Share my messages"))["recorded"]
    assert not record_dialog(ai, event("assistant", "Before notice"))["recorded"]
    assert Event.objects.count() == before
    for role in ("human", "assistant"):
        try:
            with transaction.atomic():
                Event.objects.create(session_id=ai_session, source_id=str(uuid4()), source_sequence=1,
                    occurred_at=django_timezone.now(), role=role, phase="message", content="bypass attempt",
                    attribution={}, request={})
        except IntegrityError:
            pass
        else:
            raise AssertionError("Database must protect sharing even from an older writer")
    notice = record_sharing_notice(ai, SharingNotice(session_id=ai_session, transport="accepted"))
    assert notice["notice_at"] and notice["human_choice"] == "pending"
    assert record_dialog(ai, event("assistant", "After offered notice"))["event_id"]
    assert not record_dialog(ai, event("human", "AI or peer cannot grant human consent"))["recorded"]
    choice = HumanSharingChoice(session_id=ai_session, operation_id=uuid4(), choice="share")
    for actor, interactive in ((ai, False), (ai, True), (operator, False)):
        try:
            choose_human_sharing(actor, choice, human_interactive=interactive)
        except AccessError as error:
            assert error.status == 403
        else:
            raise AssertionError("AI/tool/credential consent must be refused")

    # Host fixture binds to these existing synthetic participants. Native client
    # text or an AI credential cannot impersonate the host's human browser session.
    HostIdentityBinding.objects.create(provider="sharing-check", subject="operator", membership_id=operator.membership_id)
    HostIdentityBinding.objects.create(provider="sharing-check", subject="ai", membership_id=ai.membership_id)
    person = HostIdentity("operator", "Operator", scopes=MEMBER_SCOPES)
    identities = {"human": person, "human-token": replace(person, session_authenticated=False),
        "ai": HostIdentity("ai", "Sender", kind="ai", scopes=MEMBER_SCOPES, operator=person, session_authenticated=False),
        "other": HostIdentity("other", "Unrelated human", scopes=MEMBER_SCOPES)}

    async def resolve(scope):
        value = dict(scope["headers"]).get(b"x-check-identity", b"").decode()
        if value not in identities:
            raise AccessError("Host login required", 401)
        return identities[value]

    async def revalidate(scope, original):
        return await resolve(scope)

    async def csrf(scope, body, identity):
        return dict(scope["headers"]).get(b"x-check-csrf") == b"valid"

    authentication = HostAuthentication(provider="sharing-check", team_id=operator.team_id,
        resolve=resolve, revalidate=revalidate, check_csrf=csrf)
    with TestClient(create_app(host_auth=authentication, browser_csrf_url="/csrf")) as client:
        path = "/api/dialog/sharing/choice"
        body = choice.model_dump(mode="json")
        assert client.post(path, headers={"x-check-identity": "ai"}, json=body).status_code == 403
        assert client.post(path, headers={"x-check-identity": "human-token"}, json=body).status_code == 403
        assert client.post(path, headers={"x-check-identity": "human"}, json=body).status_code == 403
        headers = {"x-check-identity": "human", "x-check-csrf": "valid"}
        assert client.post(path, headers={**headers, "x-check-identity": "other"}, json=body).status_code == 404
        assert client.get(f"/dialog/sharing/{ai_session}", headers=headers).status_code == 200
        response = client.post(path, headers=headers, json=body)
        assert response.status_code == 200, response.text
        granted = response.json()
        assert granted["human_choice"] == "share"
        assert client.post(path, headers=headers, json=body).json() == granted
        since = datetime.fromisoformat(granted["human_since"])
        from datetime import timedelta
        assert not record_dialog(ai, event("human", "old human input", occurred_at=since-timedelta(seconds=1)))["recorded"]
        accepted = event("human", "new human input")
        saved = record_dialog(ai, accepted)
        assert saved["event_id"]
        assert Event.objects.get(pk=saved["event_id"]).attribution["authority"] == "recorder-reported"
        stop = {**body, "operation_id": str(uuid4()), "choice": "private"}
        assert client.post(path, headers=headers, json=stop).json()["human_choice"] == "private"
        assert not record_dialog(ai, event("human", "after stop"))["recorded"]
        assert record_dialog(ai, event("assistant", "AI after stop"))["event_id"]
        assert record_dialog(ai, accepted)["event_id"] == saved["event_id"]  # Immutable existing retry.
        client.post(path, headers=headers, json=body)  # Old grant retry never re-enables sharing.
        assert Sharing.objects.get(session_id=ai_session).human_choice == "private"
    assert SharingChoice.objects.filter(session_id=ai_session).count() == 2
    try:
        with transaction.atomic():
            SharingChoice.objects.filter(session_id=ai_session).update(result={})
    except IntegrityError:
        pass
    else:
        raise AssertionError("Human sharing evidence must be immutable")
    asyncio.run(recorder_privacy(root))
    asyncio.run(detach_check(root))
    print("Sharing privacy checks passed: default off, AI/browser/token/CSRF boundaries, explicit operator choice, forward-only input, stop/retry, retained provenance, immutable audit, local pre-upload filtering.")


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    import tempfile
    parser = argparse.ArgumentParser(description="Only the detached-recorder command boundary; no DB or native runtime")
    parser.add_argument("--detach-only", action="store_true", required=True)
    parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="tc-detach-check-") as directory:
        asyncio.run(detach_check(Path(directory)))
    print("PASS: only the recorder companion is launched; native/receiver identity and owner stay unchanged")
