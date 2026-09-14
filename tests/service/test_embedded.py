"""Host identity, mounted HTTP/MCP access, and browser/token boundaries."""

from dataclasses import replace
import json

import pytest
from starlette.testclient import TestClient

from teamcomms.service.access import AccessError, MEMBER_SCOPES
from teamcomms.service.asgi import create_app
from teamcomms.service.embedded import HostAuthentication, HostIdentity
from teamcomms.service.models import Credential, HostIdentityBinding, Membership, Participant, Team

pytestmark = pytest.mark.django_db(transaction=True)
PREFIX = "/prod/teamcomms"


class Host:
    """Synthetic host accounts and sessions; no TC credential is issued."""

    def __init__(self):
        self.identities = {
            "host-token": HostIdentity("account-1", "Operator", scopes=MEMBER_SCOPES, session_authenticated=False),
            "host-cookie": HostIdentity("account-1", "Operator", scopes=MEMBER_SCOPES),
            "other-token": HostIdentity("account-2", "Other", scopes=MEMBER_SCOPES, session_authenticated=False),
        }

    async def resolve(self, scope):
        headers = dict(scope["headers"])
        authorization = headers.get(b"authorization", b"").decode()
        credential = authorization.removeprefix("Bearer ") if authorization else headers.get(b"cookie", b"").decode().removeprefix("session=")
        if credential not in self.identities:
            raise AccessError("Host login required", 401)
        return self.identities[credential]

    async def revalidate(self, scope, original):
        return await self.resolve(scope)

    async def csrf(self, scope, body, identity):
        return dict(scope["headers"]).get(b"x-host-csrf") == b"host-validated"

    def authentication(self, team, *, csrf=True):
        return HostAuthentication(provider="example-host", team_id=team.id, resolve=self.resolve,
            revalidate=self.revalidate, check_csrf=self.csrf if csrf else None)


@pytest.fixture
def embedded():
    team = Team.objects.create(name="Host team")
    host = Host()
    with TestClient(create_app(host_auth=host.authentication(team), mount_path=PREFIX),
                    base_url="https://testserver") as client:
        yield client, host, team


def bearer(value="host-token"):
    return {"Authorization": "Bearer " + value}


def rpc(client, method, params, headers=None):
    response = client.post(PREFIX + "/mcp/", headers={**(headers or bearer()),
        "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    assert response.status_code == 200, response.text
    return response.json()


def test_host_login_mapping_and_mounted_mcp(embedded):
    client, host, team = embedded
    assert client.get(PREFIX + "/health").status_code == 200
    assert client.get("/api/whoami", headers=bearer()).status_code == 404
    assert client.get(PREFIX + "/api/whoami").status_code == 401
    assert client.get(PREFIX + "/api/whoami", headers={"X-Remote-User": "account-1"}).status_code == 401
    token_user = client.get(PREFIX + "/api/whoami", headers=bearer()).json()
    cookie_user = client.get(PREFIX + "/api/whoami", headers={"Cookie": "session=host-cookie"}).json()
    assert token_user == cookie_user
    result = rpc(client, "tools/call", {"name": "whoami"})["result"]
    assert json.loads(result["content"][0]["text"]) == token_user
    assert HostIdentityBinding.objects.count() == Participant.objects.count() == 1
    assert Credential.objects.count() == 0
    host.identities["host-token"] = replace(host.identities["host-token"], name="Renamed")
    renamed = client.get(PREFIX + "/api/whoami", headers=bearer()).json()
    assert renamed["name"] == "Renamed" and renamed["participant_id"] == token_user["participant_id"]
    redirect = client.get(PREFIX + "/mcp", headers=bearer(), follow_redirects=False)
    assert redirect.headers["location"] == "https://testserver" + PREFIX + "/mcp/"


def test_host_session_csrf_and_local_enrollment_disabled(embedded):
    client, host, team = embedded
    data = {"native_id": "native-1", "host": "workstation", "client": "claude", "name": "Session"}
    for path, body in (("/api/comms/sessions", data), ("/mcp/", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})):
        assert client.post(PREFIX + path, headers={"Cookie": "session=host-cookie"}, json=body).status_code == 403
    accepted = client.post(PREFIX + "/api/comms/sessions", headers={"Cookie": "session=host-cookie", "X-Host-CSRF": "host-validated"}, json=data)
    assert accepted.status_code == 200
    assert client.post(PREFIX + "/api/comms/sessions", headers=bearer(), json=data).status_code == 200
    assert client.post(PREFIX + "/api/participants", headers=bearer(), json={}).status_code == 405
    assert client.post(PREFIX + "/api/credentials", headers=bearer(), json={}).status_code == 404
    names = {tool["name"] for tool in rpc(client, "tools/list", {})["result"]["tools"]}
    assert not names & {"create_participant", "issue_credential", "revoke_credential"}
    assert rpc(client, "tools/call", {"name": "issue_credential", "arguments": {"request": {}}})["result"]["isError"]
    assert Credential.objects.count() == 0
    with TestClient(create_app(host_auth=host.authentication(team, csrf=False))) as unprotected:
        assert unprotected.post("/api/comms/sessions", headers={"Cookie": "session=host-cookie", "X-Host-CSRF": "host-validated"}, json=data).status_code == 403


def test_host_permissions_revocation_and_authorship(embedded):
    client, host, team = embedded
    mine = client.post(PREFIX + "/api/comms/sessions", headers=bearer(), json={
        "native_id": "mine", "client": "test", "host": "host", "name": "Mine"}).json()
    attempt = client.get(PREFIX + "/api/comms/messages", headers=bearer("other-token"), params={"session_id": mine["session_id"]})
    assert attempt.status_code in {403, 404}
    host.identities["host-token"] = replace(host.identities["host-token"], scopes=frozenset())
    assert client.get(PREFIX + "/api/participants", headers=bearer()).status_code == 403
    host.identities["host-token"] = replace(host.identities["host-token"], active=False)
    assert client.get(PREFIX + "/api/whoami", headers=bearer()).status_code == 401
    assert not Membership.objects.get(participant_id=mine["participant_id"]).active
    del host.identities["host-token"]
    assert client.get(PREFIX + "/api/whoami", headers=bearer()).status_code == 401


def test_host_ai_identity_operator_and_fail_closed(embedded):
    client, host, team = embedded
    human = host.identities["host-token"]
    host.identities["ai-token"] = HostIdentity("account-1:assistant", "Assistant", kind="ai",
        scopes=MEMBER_SCOPES, session_authenticated=False, operator=human)
    person = client.get(PREFIX + "/api/whoami", headers=bearer()).json()
    ai = client.get(PREFIX + "/api/whoami", headers=bearer("ai-token")).json()
    assert ai["kind"] == "ai" and ai["operator_id"] == person["participant_id"]
    assert ai["participant_id"] != person["participant_id"]
    host.identities["ai-token"] = replace(host.identities["ai-token"], kind="program", operator=None)
    assert client.get(PREFIX + "/api/whoami", headers=bearer("ai-token")).status_code == 401
    assert Participant.objects.count() == 2
    host.identities["host-token"] = replace(human, scopes=frozenset({"credentials:write"}))
    assert client.get(PREFIX + "/api/whoami", headers=bearer()).status_code == 503


def test_concurrent_host_enrollment_is_one_identity(embedded):
    from concurrent.futures import ThreadPoolExecutor
    client, host, team = embedded
    def enroll(_):
        response = client.get(PREFIX + "/api/whoami", headers=bearer())
        assert response.status_code == 200
        return response.json()["participant_id"]
    with ThreadPoolExecutor(max_workers=3) as workers:
        identities = list(workers.map(enroll, range(6)))
    assert len(set(identities)) == HostIdentityBinding.objects.count() == Participant.objects.count() == 1


def test_host_middleware_mount_and_no_credential_fallback(embedded):
    from contextlib import asynccontextmanager
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from teamcomms.service.access import mint_credential

    _, host, team = embedded
    tc = create_app(host_auth=host.authentication(team))

    @asynccontextmanager
    async def lifespan(app):
        async with tc.router.lifespan_context(tc):
            yield

    parent = Starlette(routes=[Mount(PREFIX, tc)], lifespan=lifespan)
    with TestClient(parent, base_url="https://testserver") as client:
        assert client.get(PREFIX + "/health").status_code == 200
        who = client.get(PREFIX + "/api/whoami", headers=bearer()).json()
        member = Membership.objects.get(participant_id=who["participant_id"])
        _, native_token = mint_credential(member, MEMBER_SCOPES)
        assert client.get(PREFIX + "/api/whoami", headers=bearer(native_token)).status_code == 401
        assert client.get(PREFIX + "/api/whoami", headers=[("Authorization", "Bearer host-token"),
                          ("Authorization", "Bearer other-token")]).status_code == 401
        assert rpc(client, "tools/list", {})["result"]["tools"]
        redirected = client.get(PREFIX + "/mcp", headers=bearer(), follow_redirects=False)
        assert redirected.headers["location"] == "https://testserver" + PREFIX + "/mcp/"
