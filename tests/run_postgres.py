"""Run service tests against a temporary socket-only PostgreSQL cluster."""

import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import socket
import time

import httpx


def main():
    bindir = Path(subprocess.check_output(["pg_config", "--bindir"], text=True).strip())
    if not (bindir / "initdb").is_file():
        raise SystemExit("PostgreSQL server tools (initdb and pg_ctl) are required")
    with tempfile.TemporaryDirectory(prefix="tc-tests-") as directory:
        root = Path(directory)
        data, socket_dir = root / "data", root / "socket"
        socket_dir.mkdir()
        env = os.environ.copy()
        # Configuration always targets this temporary cluster, regardless of the caller's environment.
        env["TEAMCOMMS_DATABASE_URL"] = f"postgresql:///postgres?host={socket_dir}"
        env["TEAMCOMMS_SECRET_KEY"] = "isolated-test-environment-only"
        env["TEAMCOMMS_ALLOWED_HOSTS"] = "localhost,127.0.0.1,testserver"
        env["DJANGO_SETTINGS_MODULE"] = "teamcomms.service.settings"
        env.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        subprocess.run([str(bindir / "initdb"), "-D", str(data), "--auth=trust", "--no-locale", "--encoding=UTF8"],
                       env=env, check=True, stdout=subprocess.DEVNULL)
        subprocess.run([str(bindir / "pg_ctl"), "-D", str(data), "-l", str(root / "postgres.log"),
                        "-o", f"-k {socket_dir} -c listen_addresses=''", "-w", "start"], env=env, check=True,
                       stdout=subprocess.DEVNULL)
        try:
            # The CLI must migrate and bootstrap a fresh database before pytest creates its own test database.
            cli = [str(Path(sys.executable).parent / "teamcomms")]
            subprocess.run(cli + ["migrate"], env=env, check=True, stdout=subprocess.DEVNULL)
            bootstrap = subprocess.run(cli + ["bootstrap", "--team", "Example team", "--owner", "Operator",
                                             "--token-file", str(root / "owner-token")],
                                       env=env, check=True, capture_output=True, text=True)
            assert "tc_" not in bootstrap.stdout
            assert (root / "owner-token").stat().st_mode & 0o777 == 0o600
            operator = json.loads(bootstrap.stdout)["participant_id"]
            subprocess.run(cli + ["issue-token", "--participant", operator,
                "--scope", "entries:read", "--scope", "entries:write",
                "--token-file", str(root / "entries-token")], env=env, check=True, stdout=subprocess.DEVNULL)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            with (root / "server.log").open("w") as log:
                server = subprocess.Popen(cli + ["serve", "--port", str(port)], env=env,
                                          stdout=log, stderr=subprocess.STDOUT)
                try:
                    url = f"http://127.0.0.1:{port}"
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        if server.poll() is not None:
                            raise RuntimeError((root / "server.log").read_text())
                        try:
                            response = httpx.get(url + "/health", timeout=0.5)
                        except httpx.ConnectError:
                            time.sleep(0.05)
                            continue
                        if response.status_code == 200:
                            break
                    else:
                        raise RuntimeError("Service startup timed out")
                    subprocess.run([sys.executable, "tests/check_server.py", url, str(root / "entries-token")],
                                   env=env, check=True)
                finally:
                    server.terminate()
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()
            subprocess.run([sys.executable, "-m", "django", "makemigrations", "--check", "--dry-run"],
                           env=env, check=True)
            result = subprocess.run([sys.executable, "-m", "pytest", *sys.argv[1:]], env=env)
        finally:
            subprocess.run([str(bindir / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"],
                           env=env, check=True, stdout=subprocess.DEVNULL)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
