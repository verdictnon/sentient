"""SQLite storage of internal records, analytics state and immutable events."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def stable_id(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def processing_time() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SQLiteStore:
    def __init__(self, path: str | Path):
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA busy_timeout=5000")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported application database version: {version}")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS inputs (
                record_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                store TEXT NOT NULL, camera TEXT NOT NULL, session TEXT NOT NULL,
                track TEXT, media_time_ms INTEGER NOT NULL, capture_time_utc TEXT,
                processed_at_utc TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS input_time ON inputs(
                store, camera, session, track, kind, media_time_ms
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                track_key TEXT NOT NULL, media_time_ms INTEGER NOT NULL,
                processed_at_utc TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS state (
                namespace TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(namespace, key)
            );
            PRAGMA user_version=1;
            COMMIT;
            """)
        except BaseException:
            self.connection.rollback()
            self.connection.close()
            raise

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def transaction(self, *, write: bool = True):
        if self.connection.in_transaction:
            raise RuntimeError("Nested store transactions are not supported")
        self.connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    @contextmanager
    def atomic(self, *, write: bool = True):
        """Join a caller-owned transaction or create one for a standalone call.

        This does not swallow errors or commit a caller's transaction. The replay
        adapter uses it to compose existing analytics within an atomic batch.
        """
        if self.connection.in_transaction:
            yield
        else:
            with self.transaction(write=write):
                yield

    def _require_transaction(self):
        if not self.connection.in_transaction:
            raise RuntimeError("Store writes require an explicit transaction")

    def has_history(self) -> bool:
        return any(self.connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                   is not None for table in ("inputs", "events"))

    def has_input_at(self, kind: str, source: tuple, time_ms: int) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM inputs WHERE kind=? AND store=? AND camera=? "
            "AND session=? AND media_time_ms=? LIMIT 1", (kind, *source, time_ms),
        ).fetchone() is not None

    def existing_input(self, record_id: str, payload: dict) -> bool:
        row = self.connection.execute(
            "SELECT payload FROM inputs WHERE record_id=?", (record_id,)
        ).fetchone()
        if row is None:
            return False
        if row[0] != canonical(payload):
            raise ValueError(f"Conflicting payload for record ID {record_id}")
        return True

    def inputs(self) -> list[dict]:
        return [dict(row) | {"payload": json.loads(row["payload"])}
                for row in self.connection.execute("SELECT * FROM inputs ORDER BY rowid")]

    def put_input(self, payload: dict, kind: str, source: tuple, track: str | None):
        self._require_transaction()
        self.connection.execute(
            "INSERT INTO inputs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (payload["record_id"], kind, *source, track, payload["media_time_ms"],
             payload["capture_time_utc"], processing_time(), canonical(payload)),
        )

    def get_state(self, namespace: str, key, default=None):
        row = self.connection.execute(
            "SELECT payload FROM state WHERE namespace=? AND key=?",
            (namespace, canonical(key)),
        ).fetchone()
        return default if row is None else json.loads(row[0])

    def put_state(self, namespace: str, key, value):
        self._require_transaction()
        self.connection.execute(
            "INSERT INTO state VALUES (?, ?, ?) ON CONFLICT(namespace, key) "
            "DO UPDATE SET payload=excluded.payload",
            (namespace, canonical(key), canonical(value)),
        )

    def iter_states(self, namespace: str):
        rows = self.connection.execute(
            "SELECT key, payload FROM state WHERE namespace=? ORDER BY key", (namespace,)
        )
        for row in rows:
            yield json.loads(row[0]), json.loads(row[1])

    def states(self, namespace: str):
        return list(self.iter_states(namespace))

    def track_states(self, source: tuple):
        return ((key, state) for key, state in self.iter_states("track") if tuple(key[:3]) == source)

    def event(self, kind: str, key, time_ms: int, cause: str, payload: dict):
        self._require_transaction()
        event_id = stable_id([kind, key, cause])
        values = (event_id, kind, canonical(key), time_ms, processing_time(), canonical(payload))
        previous = self.connection.execute(
            "SELECT kind, track_key, media_time_ms, payload FROM events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if previous is not None:
            if tuple(previous) != (kind, canonical(key), time_ms, canonical(payload)):
                raise ValueError("Conflicting derived event identity")
            return
        self.connection.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", values)

    def events(self) -> list[dict]:
        return [dict(row) | {"payload": json.loads(row["payload"]),
                             "track_key": json.loads(row["track_key"])}
                for row in self.connection.execute("SELECT * FROM events ORDER BY rowid")]
