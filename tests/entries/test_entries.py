from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
from uuid import uuid4

from django.db import close_old_connections, IntegrityError, transaction
import pytest
from starlette.testclient import TestClient

from teamcomms.entries import operations
from teamcomms.entries.models import Entry, Revision, RevisionReference
from teamcomms.entries.schemas import UpdateEntry
from teamcomms.service.access import AccessError, authenticate, mint_credential
from teamcomms.service.asgi import create_app
from teamcomms.service.models import Membership, Participant
from teamcomms.service.operations import bootstrap

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def team():
    record, token = bootstrap("Writers", "Editor")
    with TestClient(create_app()) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        yield client, token, record


def create(client, content="Initial content", **fields):
    response = client.post("/api/entries", json={"kind": "document", "state": {"content": content, **fields}})
    assert response.status_code == 200, response.text
    return response.json()


def read(client, entry, **parameters):
    response = client.get("/api/entries/read", params={"entry_id": entry["entry_id"], **parameters})
    assert response.status_code == 200, response.text
    return response.json()


def call(client, name, arguments):
    response = client.post("/mcp/", headers={"Accept": "application/json, text/event-stream"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def value(result):
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def test_create_and_bounded_read_across_interfaces(team):
    client, token, record = team
    content = "  # Heading\n\n    preserved indentation\nλ \n"
    entry = create(client, content, title="A document", metadata={"topic": "editing"}, tags=["draft"])
    first = value(call(client, "get_entry", {"entry_id": entry["entry_id"], "max_content_length": 12}))
    assert first["author_id"] == record["participant_id"]
    assert first["content_length"] == len(content)
    rest = read(client, entry, revision=1, content_offset=first["next_content_offset"])
    assert first["state"]["content"] + rest["state"]["content"] == content
    assert rest["next_content_offset"] is None
    assert rest["state"]["metadata"] == {"topic": "editing"}
    assert rest["state"]["tags"] == ["draft"]


def test_named_update_and_historical_reads(team):
    client, _, _ = team
    entry = create(client, "before\n", tags=["keep"], metadata={"nested": {"a": 1}})
    result = value(call(client, "update_entry", {"request": {"entry_id": entry["entry_id"],
        "expected_revision": 1, "changes": {"content": "after\n"}}}))
    assert result["revision"] == 2 and result["base_revision"] == 1
    current = read(client, entry)
    assert current["state"]["content"] == "after\n"
    assert current["state"]["tags"] == ["keep"]
    assert current["state"]["metadata"] == {"nested": {"a": 1}}
    assert read(client, entry, revision=1)["state"]["content"] == "before\n"
    versions = value(call(client, "get_entry_revisions", {"entry_id": entry["entry_id"], "limit": 1}))
    assert versions["revisions"][0]["revision"] == 2 and versions["next_offset"] == 1
    assert "state" not in versions["revisions"][0]


def test_stale_and_invalid_edits_leave_no_partial_revision(team):
    client, _, _ = team
    entry = create(client)
    request = {"entry_id": entry["entry_id"], "expected_revision": 1, "changes": {"title": "Changed"}}
    assert client.post("/api/entries/update", json=request).status_code == 200
    assert client.post("/api/entries/update", json=request).status_code == 409
    assert call(client, "update_entry", {"request": request})["isError"]
    for changes in ({"author_id": str(uuid4())}, {"kind": "dialog"}, {"content": None},
                    {"relations": [{"entry_id": str(uuid4())}]}, {"tags": ["same", "same"]}):
        response = client.post("/api/entries/update", json={**request, "expected_revision": 2, "changes": changes})
        assert response.status_code in (400, 404), response.text
    assert Revision.objects.count() == 2
    assert read(client, entry)["revision"] == 2


def test_concurrent_writers_have_one_winner_and_correct_author(team):
    client, token, record = team
    entry = create(client)
    person = Participant.objects.create(name="Other editor", kind="ai")
    member = Membership.objects.create(team_id=record["team_id"], participant=person)
    _, other_token = mint_credential(member, {"entries:read", "entries:write"})
    barrier = Barrier(2)

    def write(credentials):
        close_old_connections()
        try:
            actor = authenticate(credentials)
            barrier.wait(timeout=5)
            try:
                return operations.update_entry(actor, UpdateEntry(entry_id=entry["entry_id"],
                    expected_revision=1, changes={"content": str(actor.participant_id)}))
            except AccessError as error:
                assert error.status == 409
                return None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [token, other_token]))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert Revision.objects.count() == 2
    current = read(client, entry)
    assert current["author_id"] == current["state"]["content"] == winners[0]["author_id"]


def test_pinned_references_and_restore_preserve_saved_state(team):
    client, _, _ = team
    target = create(client, "Reviewed version")
    source = create(client, "Review", relations=[{"entry_id": target["entry_id"], "revision": 1, "relation": "reviews"}])
    assert client.post("/api/entries/update", json={"entry_id": target["entry_id"], "expected_revision": 1,
                                                  "changes": {"content": "Later version"}}).status_code == 200
    assert client.post("/api/entries/update", json={"entry_id": source["entry_id"], "expected_revision": 1,
                                                  "changes": {"content": "Later review"}}).status_code == 200
    result = value(call(client, "restore_entry", {"request": {"entry_id": source["entry_id"],
                                                              "expected_revision": 2, "revision": 1}}))
    assert result["revision"] == 3 and result["restored_from"] == source["revision_id"]
    assert read(client, source)["state"]["content"] == "Review"
    reference = read(client, source)["state"]["relations"][0]
    assert reference["revision"] == 1
    assert read(client, {"entry_id": reference["entry_id"]}, revision=reference["revision"])["state"]["content"] == "Reviewed version"
    assert RevisionReference.objects.filter(target_revision_id=target["revision_id"]).count() == 3


def test_immutable_revisions_and_reference_constraints(team):
    client, _, _ = team
    a, b = create(client, "A"), create(client, "B")
    for action in (
        lambda: Revision.objects.filter(pk=a["revision_id"]).update(state={}),
        lambda: Revision.objects.filter(pk=a["revision_id"]).delete(),
        lambda: RevisionReference.objects.create(source_id=a["revision_id"],
            target_entry_id=b["entry_id"], target_revision_id=a["revision_id"], relation="wrong"),
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            action()
    assert read(client, a, revision=1)["state"]["content"] == "A"


def test_search_text_filters_and_updated_index(team):
    client, _, _ = team
    target = create(client, "Target")
    entry = create(client, "Prod/testbed computation\n!!!", title="Worker plan", tags=["ops"],
        metadata={"queue": "test", "optional": None}, relations=[{"entry_id": target["entry_id"]}])
    for filters in ({"query": "testbed computing"}, {"query": "!!!"}, {"tag": "ops", "metadata": {"queue": "test"}},
                    {"metadata": {"optional": None}}, {"related_to": target["entry_id"], "relation": "related"}):
        result = value(call(client, "search_entries", {"request": filters}))
        assert [e["entry_id"] for e in result["entries"]] == [entry["entry_id"]]
    assert client.post("/api/entries/update", json={"entry_id": entry["entry_id"], "expected_revision": 1,
        "changes": {"content": "Replacement", "relations": []}}).status_code == 200
    for filters in ({"query": "testbed"}, {"related_to": target["entry_id"]}):
        assert client.post("/api/entries/search", json=filters).json()["entries"] == []
    page = client.get("/api/entries?limit=1").json()
    assert len(page["entries"]) == 1 and page["next_offset"] == 1


def test_scope_kind_identity_and_input_bounds(team):
    client, admin, record = team
    entry = create(client)
    member = Membership.objects.get(participant_id=record["participant_id"])
    _, read_only = mint_credential(member, {"entries:read"})
    client.headers["Authorization"] = f"Bearer {read_only}"
    assert client.get("/api/entries").status_code == 200
    request = {"kind": "document", "state": {"content": "Denied"}}
    assert client.post("/api/entries", json=request).status_code == 403
    assert call(client, "create_entry", {"request": request})["isError"]
    _, directory_only = mint_credential(member, {"directory:read"})
    client.headers["Authorization"] = f"Bearer {directory_only}"
    assert client.get("/api/entries").status_code == 403
    assert call(client, "get_entry", {"entry_id": entry["entry_id"]})["isError"]
    client.headers["Authorization"] = f"Bearer {admin}"
    for body in ({"kind": "dialog", "state": {}}, {"state": {}, "team_id": str(uuid4())},
                 {"state": {"content": "x" * 40001}}, {"state": {"metadata": {"large": "x" * 49000}}}):
        assert client.post("/api/entries", json=body).status_code == 400
    for query in ({"limit": 101}, {"modified_since": "2026-01-01"},
                  {"modified_since": "2026-02-01T00:00:00Z", "modified_before": "2026-01-01T00:00:00Z"}):
        assert client.post("/api/entries/search", json=query).status_code == 400
    assert Entry.objects.count() == 1


def test_create_slug_conflict_is_atomic(team):
    client, _, _ = team
    request = {"slug": "design", "kind": "document", "state": {"content": "Design"}}
    result = value(call(client, "create_entry", {"request": request}))
    assert client.post("/api/entries", json=request).status_code == 409
    assert Entry.objects.count() == Revision.objects.count() == 1
    assert client.get("/api/entries?slug=design").json()["entries"][0]["entry_id"] == result["entry_id"]
