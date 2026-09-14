"""Private durable cursors, outbox, and per-session dispatch state."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import stat


def private_directory(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Connector state directory must be private and owned by this user")
    return path


@contextmanager
def session_lock(directory):
    directory = private_directory(directory)
    fd = os.open(directory / "receiver.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


class Store:
    def __init__(self, directory):
        directory = private_directory(directory)
        path = directory / "state.sqlite3"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inbox (id TEXT PRIMARY KEY, payload TEXT NOT NULL, phase TEXT NOT NULL,
                claim TEXT, report TEXT, error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, payload TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0);
        ''')

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (key, json.dumps(value)))

    def ingest(self, delivery, *, advance=True):
        with self.db:
            prior = self.db.execute("SELECT payload,phase FROM inbox WHERE id=?", (delivery["delivery_id"],)).fetchone()
            if (prior and prior[1] in {"done", "uncertain"} and delivery["state"] == "pending"
                    and delivery["revision"] > json.loads(prior[0])["revision"]):
                self.db.execute("UPDATE inbox SET phase='new', claim=NULL, report=NULL, error='' WHERE id=?", (delivery["delivery_id"],))
            self.db.execute("INSERT INTO inbox(id,payload,phase) VALUES (?,?, 'new') ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                            (delivery["delivery_id"], json.dumps(delivery)))
            if advance:
                cursor = max(self.get("cursor", 0), delivery["sequence"])
                self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('cursor', ?)", (json.dumps(cursor),))

    def pending(self):
        self.db.row_factory = sqlite3.Row
        return [dict(row) for row in self.db.execute("SELECT * FROM inbox WHERE phase != 'done' ORDER BY rowid")]

    def update(self, delivery_id, phase, *, claim=None, report=None, error=""):
        with self.db:
            self.db.execute("UPDATE inbox SET phase=?, claim=COALESCE(?,claim), report=COALESCE(?,report), error=? WHERE id=?",
                            (phase, json.dumps(claim) if claim else None, json.dumps(report) if report else None, error, delivery_id))

    def enqueue(self, body):
        with self.db:
            prior = self.db.execute("SELECT payload FROM outbox WHERE id=?", (body["message_id"],)).fetchone()
            payload = json.dumps(body, sort_keys=True)
            if prior and prior[0] != payload:
                raise ValueError("Outgoing message ID already has different content")
            self.db.execute("INSERT OR IGNORE INTO outbox(id,payload) VALUES (?,?)", (body["message_id"], payload))

    def outgoing(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM outbox WHERE sent=0 ORDER BY rowid")]

    def sent(self, message_id):
        with self.db:
            self.db.execute("UPDATE outbox SET sent=1 WHERE id=?", (message_id,))
