"""Verify a running service with HTTP and the official MCP client."""

import asyncio
import json
from pathlib import Path
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def check(url, token_file):
    token = Path(token_file).read_text().strip()
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=10) as client:
        response = await client.get(url + "/api/whoami")
        response.raise_for_status()
        expected = response.json()
        async with streamable_http_client(url + "/mcp/", http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} >= {
                    "whoami", "list_participants", "create_participant", "issue_credential", "revoke_credential",
                    "create_entry", "get_entry", "update_entry", "restore_entry", "get_entry_revisions", "search_entries"
                }
                result = await session.call_tool("whoami")
                assert not result.isError
                assert json.loads(result.content[0].text) == expected
                created = await session.call_tool("create_entry", {"request": {
                    "kind": "note", "state": {"content": "Standalone entry\n"}}})
                assert not created.isError
                entry = json.loads(created.content[0].text)
                saved = await client.get(url + "/api/entries/read", params={"entry_id": entry["entry_id"]})
                saved.raise_for_status()
                assert saved.json()["state"]["content"] == "Standalone entry\n"
    print("Standalone startup, scoped provisioning, MCP handshake, and HTTP/MCP entry roundtrip passed.")


if __name__ == "__main__":
    asyncio.run(check(*sys.argv[1:]))
