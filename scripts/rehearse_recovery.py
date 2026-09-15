#!/usr/bin/env python3
"""Bounded wheel-installation upgrade/restore drill, with synthetic data only.

Uses two preinstalled isolated environments. No pytest, production configuration,
native client or connector is imported. Evidence and backups remain private.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


def run(argv, env, *, input=None):
    result = subprocess.run([str(x) for x in argv], env=env, input=input,
                            text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f"{Path(str(argv[0])).name} failed: {result.stderr[-3000:]}")
    return result.stdout


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    path.chmod(0o600)


@contextmanager
def service(python, env, root):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with (root / 'service.log').open('ab') as log:
        proc = subprocess.Popen([str(python.parent / 'teamcomms'), 'serve', '--port', str(port)],
                                env=env, cwd=root, stdout=log, stderr=log,
                                start_new_session=True)
        base = f'http://127.0.0.1:{port}'
        try:
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError(f'Service exited; inspect {root / "service.log"}')
                try:
                    with urlopen(base + '/health', timeout=1) as response:
                        if response.status == 200:
                            break
                except URLError:
                    time.sleep(.1)
            else:
                raise RuntimeError('Service readiness deadline exceeded')
            yield base
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)


def api(base, token, path, body=None):
    request = Request(base + path, headers={'Authorization': 'Bearer ' + token,
                                          'Content-Type': 'application/json'},
                      data=None if body is None else json.dumps(body).encode())
    try:
        with urlopen(request, timeout=5) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f'{path}: HTTP {error.code}: {error.read().decode()[:2000]}') from error


def seed(base, token):
    doc = api(base, token, '/api/entries', {'kind': 'document', 'state': {
        'title': 'Recovery record', 'content': 'Original retained text.\n'}})
    api(base, token, '/api/entries/update', {'entry_id': doc['entry_id'],
        'expected_revision': 1, 'changes': {'content': 'Current retained text.\n'}})
    pouch = api(base, token, '/api/pouch/initialize', {})
    work = api(base, token, '/api/inflight', {'operation_id': str(uuid4()),
        'state': {'title': 'Recovery coordination', 'relations': [{
            'entry_id': doc['entry_id'], 'revision': 1, 'relation': 'reviews'}]},
        'criteria': 'Retain the referenced version and pending delivery'})
    session = api(base, token, '/api/comms/sessions', {'native_id': str(uuid4()),
        'client': 'recovery-drill', 'host': 'isolated', 'name': 'Offline recipient',
        'state': 'offline', 'delivery_mode': 'pull'})
    message = api(base, token, '/api/comms/messages', {'message_id': str(uuid4()),
        'content': 'Pending across backup and restore',
        'audience': {'session_ids': [session['session_id']]}})
    return {'document': doc, 'pouch': pouch, 'work': work,
            'session': session, 'message': message}


def reads(base, token, records):
    def get(path, **query):
        return api(base, token, path + '?' + urlencode(query))
    return {
        'identity': api(base, token, '/api/whoami'),
        'original': get('/api/entries/read', entry_id=records['document']['entry_id'], revision=1),
        'current': get('/api/entries/read', entry_id=records['document']['entry_id']),
        'pouch': get('/api/pouch/export', revision=1),
        'work': get('/api/inflight/read', entry_id=records['work']['entry_id']),
        'pending': get('/api/comms/messages', session_id=records['session']['session_id']),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-python', required=True, type=Path)
    parser.add_argument('--candidate-python', required=True, type=Path)
    parser.add_argument('--evidence-dir', required=True, type=Path)
    args = parser.parse_args()
    if os.getuid() == 0:
        parser.error('Use an unprivileged OS account')
    root = args.evidence_dir.absolute()
    root.mkdir(mode=0o700)  # Existing paths are never reused or overwritten.
    os.umask(0o077)
    bindir = Path(subprocess.check_output(['pg_config', '--bindir'], text=True).strip())
    sock = root / 'socket'
    sock.mkdir()
    home = root / 'home'
    home.mkdir()
    # Explicit isolated environment: no deployment URLs, credentials or personal imports.
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'LANG': 'C.UTF-8',
           'TEAMCOMMS_ALLOWED_HOSTS': 'localhost,127.0.0.1',
           'TEAMCOMMS_SECRET_KEY': 'synthetic-recovery-only',
           'DJANGO_SETTINGS_MODULE': 'teamcomms.service.settings',
           'PGHOST': str(sock), 'PGPORT': '5432'}
    def database(name):
        return {**env, 'PGDATABASE': name,
                'TEAMCOMMS_DATABASE_URL': f'postgresql:///{name}?host={sock}'}
    def sql(name, statement):
        return run([bindir / 'psql', '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', statement], database(name))
    def snapshot(name):
        tables = sql(name, "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                     "AND tablename LIKE 'teamcomms_%' ORDER BY tablename").splitlines()
        result = {}
        for table in tables:
            rows = sql(name, f'SELECT row_to_json(t)::text FROM "{table}" t ORDER BY row_to_json(t)::text')
            result[table] = {'rows': len(rows.splitlines()),
                             'sha256': hashlib.sha256(rows.encode()).hexdigest()}
        return result
    def dump(name, filename):
        run([bindir / 'pg_dump', '--format=custom', '--file', root / filename], database(name))
    def restore(name, filename):
        run([bindir / 'createdb', name], env)
        run([bindir / 'pg_restore', '--exit-on-error', '--no-owner', '--no-privileges',
             '--dbname', name, root / filename], env)
    def migrate(python, name):
        return run([python.parent / 'teamcomms', 'migrate'], database(name))
    run([bindir / 'initdb', '-D', root / 'data', '--auth=trust', '--no-locale', '--encoding=UTF8'], env)
    run([bindir / 'pg_ctl', '-D', root / 'data', '-l', root / 'postgres.log', '-o',
         f"-k {sock} -c listen_addresses=''", '-w', 'start'], env)
    try:
        run([bindir / 'createdb', 'source'], env)
        migrate(args.old_python, 'source')
        old_token = root / 'owner-token'
        identity = json.loads(run([args.old_python.parent / 'teamcomms', 'bootstrap',
            '--team', 'Independent recovery drill', '--owner', 'Synthetic operator',
            '--token-file', old_token], database('source')))
        token = old_token.read_text().strip()
        with service(args.old_python, database('source'), root) as base:
            records = seed(base, token)
            before_reads = reads(base, token, records)
        before = snapshot('source')
        dump('source', 'before.dump')
        upgrade = migrate(args.candidate_python, 'source')
        after = snapshot('source')
        if any(after.get(table) != value for table, value in before.items()):
            raise AssertionError('Upgrade changed pre-existing component records')
        dump('source', 'candidate.dump')
        restore('restored', 'candidate.dump')
        if snapshot('restored') != after:
            raise AssertionError('Candidate restore differs from full component snapshot')
        with service(args.candidate_python, database('restored'), root) as base:
            restored_reads = reads(base, token, records)
        # Added current-state fields may differ across releases; history is exact.
        for field in ('identity', 'original', 'current', 'pouch', 'pending'):
            if restored_reads[field] != before_reads[field]:
                raise AssertionError(f'Restored API changed {field}')
        if restored_reads['work']['work'] != before_reads['work']['work']:
            raise AssertionError('Restored work identity, owner or state changed')
        immutable = subprocess.run([str(bindir / 'psql'), '-XAt', '-v', 'ON_ERROR_STOP=1', '-c',
            "UPDATE teamcomms_entries_revision SET state='{}'::jsonb"],
            env=database('restored'), text=True, capture_output=True, timeout=10)
        if immutable.returncode == 0 or 'immutable' not in immutable.stderr.lower():
            raise AssertionError('Restored revision immutability was not established: ' + immutable.stderr)
        binding = subprocess.run([str(bindir / 'psql'), '-XAt', '-v', 'ON_ERROR_STOP=1', '-c',
            "UPDATE teamcomms_pouch_pouch SET entry_id='" + records['document']['entry_id'] + "'"],
            env=database('restored'), text=True, capture_output=True, timeout=10)
        if binding.returncode == 0 or 'permanent' not in binding.stderr.lower():
            raise AssertionError('Restored Pouch binding guard was not established: ' + binding.stderr)
        if snapshot('restored') != after:
            raise AssertionError('Rejected update altered restored state')
        restore('rollback', 'before.dump')
        if snapshot('rollback') != before:
            raise AssertionError('Rollback snapshot differs')
        with service(args.old_python, database('rollback'), root) as base:
            if reads(base, token, records) != before_reads:
                raise AssertionError('Rollback API differs')
        inventories = {}
        for label, python in [('old', args.old_python), ('candidate', args.candidate_python)]:
            inventories[label] = json.loads(run([python, '-I', '-c',
                'import importlib.metadata as m,json,teamcomms;print(json.dumps({"module":teamcomms.__file__,"version":m.version("teamcomms-ai"),"packages":sorted((d.metadata["Name"],d.version) for d in m.distributions())}))'], env))
            if 'site-packages' not in inventories[label]['module']:
                raise AssertionError('Package did not resolve to the isolated installation')
        evidence = {'status': 'passed', 'at': datetime.now(timezone.utc).isoformat(),
            'identity': identity, 'records': records, 'component_tables_before': before,
            'component_tables_after': after, 'upgrade_output': upgrade,
            'restored_reads': restored_reads, 'immutable_revision_rejected': True,
            'permanent_binding_verified': True, 'installed_environments': inventories,
            'rollback_exact': True, 'production_access': False, 'suite_run': False}
        save(root / 'evidence.json', evidence)
        print(json.dumps({'status': 'passed', 'evidence': str(root / 'evidence.json')}))
    finally:
        run([bindir / 'pg_ctl', '-D', root / 'data', '-m', 'fast', '-w', 'stop'], env)


if __name__ == '__main__':
    main()
