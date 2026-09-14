"""Local migration, bootstrap, and service commands."""

import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(prog="teamcomms")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Apply database migrations")
    provision = commands.add_parser("bootstrap", help="Initialize an empty team database")
    provision.add_argument("--team", required=True)
    provision.add_argument("--owner", required=True)
    provision.add_argument("--token-file", required=True, type=Path)
    credential = commands.add_parser("issue-token", help="Provision a credential using local database access")
    credential.add_argument("--participant", required=True)
    credential.add_argument("--scope", action="append", required=True)
    credential.add_argument("--token-file", required=True, type=Path)
    serve = commands.add_parser("serve", help="Run the HTTP and MCP service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "teamcomms.service.settings")
    if args.command == "serve":
        import uvicorn
        uvicorn.run("teamcomms.service.asgi:create_app", factory=True,
                    host=args.host, port=args.port, proxy_headers=False)
        return

    import django
    django.setup()
    if args.command == "migrate":
        from django.core.management import call_command
        call_command("migrate", interactive=False)
        return

    if args.command == "bootstrap" and (
        not args.team.strip() or not args.owner.strip() or max(len(args.team), len(args.owner)) > 120
    ):
        parser.error("Team and owner must be nonempty names of at most 120 characters")
    from django.db import transaction
    from .operations import bootstrap
    try:
        fd = os.open(args.token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        parser.error("Token file already exists; choose a new path")
    try:
        with os.fdopen(fd, "w") as output, transaction.atomic():
            if args.command == "bootstrap":
                result, token = bootstrap(args.team.strip(), args.owner.strip())
            else:
                from .access import MEMBER_SCOPES, SCOPES, mint_credential
                from .models import Membership
                member = Membership.objects.get(participant_id=args.participant, active=True)
                scopes = set(args.scope)
                allowed = SCOPES if member.role == "admin" else MEMBER_SCOPES
                if not scopes <= allowed:
                    raise ValueError("Scopes are not valid for this membership")
                issued, token = mint_credential(member, scopes)
                result = {"credential_id": str(issued.id), "participant_id": str(member.participant_id)}
            output.write(token + "\n")
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        args.token_file.unlink()
        raise
    print(json.dumps({**result, "token_file": str(args.token_file)}))
