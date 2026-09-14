import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.utils import timezone
import pytest
from starlette.testclient import TestClient

from teamcomms.service.access import authenticate, mint_credential, SCOPES
from teamcomms.service.asgi import create_app
from teamcomms.service.models import Credential, Membership, Participant, Team
from teamcomms.service.operations import bootstrap

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def installation():
    record, token = bootstrap("Example team", "Operator")
    with TestClient(create_app()) as client:
        yield client, token, record


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def mcp_call(client, token, name, arguments=None):
    response = client.post("/mcp/", headers={**headers(token),
        "Accept": "application/json, text/event-stream"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    })
    assert response.status_code == 200, response.text
    return response.json()["result"]


def value(result):
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def add_member(client, token, kind="ai"):
    response = client.post("/api/participants", headers=headers(token),
                           json={"name": f"Example {kind}", "kind": kind})
    assert response.status_code == 200, response.text
    participant = response.json()
    response = client.post("/api/credentials", headers=headers(token), json={
        "participant_id": participant["participant_id"], "scopes": ["directory:read"]})
    assert response.status_code == 200, response.text
    return participant, response.json()


def test_bootstrap_and_schema(installation):
    client, token, record = installation
    assert Credential.objects.get().digest == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(Credential.objects.values().get())
    with pytest.raises(ValueError, match="already initialized"):
        bootstrap("Other", "Another operator")
    with pytest.raises(IntegrityError), transaction.atomic():
        Team.objects.create(name="Second team")
    assert Team.objects.count() == Participant.objects.count() == 1


def test_both_interfaces_authenticate_and_bind_identity(installation):
    client, token, record = installation
    assert client.get("/health").status_code == 200
    for path in ("/api/whoami", "/api/participants", "/mcp/"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers=headers("invalid")).status_code == 401
    response = client.get("/api/whoami", headers=headers(token))
    assert response.status_code == 200
    assert response.json()["participant_id"] == record["participant_id"]
    assert value(mcp_call(client, token, "whoami")) == response.json()
    assert "no-store" in response.headers["cache-control"]


@pytest.mark.parametrize("kind", ["human", "ai", "program", "connector"])
def test_participants_share_directory_with_distinct_identity(installation, kind):
    client, admin, record = installation
    person, issued = add_member(client, admin, kind)
    token = issued["token"]
    http = client.get("/api/participants", headers=headers(token)).json()
    assert http == value(mcp_call(client, token, "list_participants"))
    assert len(http["participants"]) == 2
    assert value(mcp_call(client, token, "whoami"))["participant_id"] == person["participant_id"]
    assert value(mcp_call(client, admin, "whoami"))["participant_id"] == record["participant_id"]


def test_write_scope_and_role_on_both_interfaces(installation):
    client, admin, record = installation
    person, issued = add_member(client, admin)
    token = issued["token"]
    request = {"name": "Unauthorized member", "kind": "human"}
    assert client.post("/api/participants", headers=headers(token), json=request).status_code == 403
    assert mcp_call(client, token, "create_participant", {"request": request})["isError"]
    assert client.post("/api/credentials", headers=headers(token), json={
        "participant_id": record["participant_id"], "scopes": ["credentials:write"]}).status_code == 403
    assert mcp_call(client, token, "issue_credential", {"request": {
        "participant_id": record["participant_id"], "scopes": ["credentials:write"]}})["isError"]
    assert Participant.objects.count() == 2


def test_spoofed_authorship_and_unrelated_ids(installation):
    client, admin, record = installation
    body = {"name": "Spoof", "kind": "ai", "sender_id": record["participant_id"]}
    assert client.post("/api/participants", headers=headers(admin), json=body).status_code == 400
    assert mcp_call(client, admin, "create_participant", {"request": body})["isError"]
    outsider = str(uuid4())
    assert client.get("/api/participants", headers=headers(admin), params={"team_id": outsider}).status_code == 400
    for request in ({"participant_id": outsider, "scopes": ["directory:read"]},
                    {"participant_id": record["participant_id"], "scopes": ["invented:scope"]}):
        assert client.post("/api/credentials", headers=headers(admin), json=request).status_code in (400, 404)
        assert mcp_call(client, admin, "issue_credential", {"request": request})["isError"]
    assert Participant.objects.count() == 1


def test_scope_attenuation_even_for_admin(installation):
    client, admin, record = installation
    member = Membership.objects.get(participant_id=record["participant_id"])
    _, limited = mint_credential(member, {"credentials:write"})
    request = {"participant_id": record["participant_id"], "scopes": ["directory:write"]}
    assert client.post("/api/credentials", headers=headers(limited), json=request).status_code == 400
    assert mcp_call(client, limited, "issue_credential", {"request": request})["isError"]
    assert client.get("/api/participants", headers=headers(limited)).status_code == 403


def test_revocation_and_membership_deactivation(installation):
    client, admin, record = installation
    person, issued = add_member(client, admin)
    token = issued["token"]
    result = value(mcp_call(client, admin, "revoke_credential", {"request": {"credential_id": issued["credential_id"]}}))
    assert result["revoked"]
    for path in ("/api/whoami", "/mcp/"):
        assert client.get(path, headers=headers(token)).status_code == 401
    person, second = add_member(client, admin, "program")
    Membership.objects.filter(participant_id=person["participant_id"]).update(active=False)
    for path in ("/api/whoami", "/mcp/"):
        assert client.get(path, headers=headers(second["token"])).status_code == 401
    assert client.get("/api/whoami", headers=headers(admin)).status_code == 200


def test_expiry_and_bounded_queries(installation):
    client, admin, record = installation
    member = Membership.objects.get(participant_id=record["participant_id"])
    _, expired = mint_credential(member, SCOPES, timezone.now() - timedelta(seconds=1))
    assert client.get("/api/whoami", headers=headers(expired)).status_code == 401
    assert client.post("/mcp/", headers=headers(expired), json={}).status_code == 401
    for query in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
        assert client.get("/api/participants", headers=headers(admin), params=query).status_code == 400
        assert mcp_call(client, admin, "list_participants", query)["isError"]
    add_member(client, admin)
    page = client.get("/api/participants?limit=1", headers=headers(admin)).json()
    assert len(page["participants"]) == page["next_offset"] == 1


def test_transport_guards(installation):
    client, admin, _ = installation
    assert client.get("/api/whoami", headers={**headers(admin), "Host": "outside.example"}).status_code == 400
    assert client.get("/api/whoami", headers={**headers(admin), "Origin": "https://outside.example"}).status_code == 403
    assert client.get("/api/whoami", headers=[("Authorization", f"Bearer {admin}"),
        ("Authorization", f"Bearer {admin}")]).status_code == 401
    assert client.post("/mcp/", headers=headers(admin), content="x" * 65537).status_code == 413
    assert client.head("/api/whoami", headers=headers(admin)).status_code == 200
    assert client.get("/api/whoami", headers={**headers(admin), "Origin": "http://["}).status_code == 403


def test_mcp_creation_and_operator_validation(installation):
    client, admin, record = installation
    person = value(mcp_call(client, admin, "create_participant", {"request": {
        "name": "Assistant", "kind": "ai", "operator_id": record["participant_id"]}}))
    assert person["operator_id"] == record["participant_id"]
    credential = value(mcp_call(client, admin, "issue_credential", {"request": {
        "participant_id": person["participant_id"], "scopes": ["directory:read"]}}))
    assert client.get("/api/whoami", headers=headers(credential["token"])).json()["participant_id"] == person["participant_id"]
    for request in ({"name": "Bad operator", "kind": "ai", "operator_id": person["participant_id"]},
                    {"name": "Bad kind", "kind": "human", "operator_id": record["participant_id"]}):
        assert mcp_call(client, admin, "create_participant", {"request": request})["isError"]
    revoked = client.post("/api/credentials/revoke", headers=headers(admin),
                          json={"credential_id": credential["credential_id"]})
    assert revoked.status_code == 200
    assert client.get("/api/whoami", headers=headers(credential["token"])).status_code == 401


def test_concurrent_request_identity_isolation(installation):
    from concurrent.futures import ThreadPoolExecutor

    client, admin, record = installation
    person, credential = add_member(client, admin)
    def identity(token):
        return value(mcp_call(client, token, "whoami"))["participant_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(identity, [admin, credential["token"]] * 3))
    assert results == [record["participant_id"], person["participant_id"]] * 3
