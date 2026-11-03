"""Persistence invariants independent of the absent wire contract."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.analytics import Analytics
from app.storage import SQLiteStore
from test_queue_integration import CONFIG, completed_visit


class StorageRegressionTests(unittest.TestCase):
    def test_uncommitted_events_are_invisible_and_rollback_is_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.sqlite"
            with SQLiteStore(path) as writer, SQLiteStore(path) as reader:
                self.assertEqual(writer.connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                with self.assertRaisesRegex(RuntimeError, "abort"):
                    with writer.transaction():
                        writer.event("test.transition", ["s", "c", "a", "7"], 1000, "source-1", {})
                        writer.put_state("test", "key", {"value": 1})
                        self.assertEqual(reader.events(), [])
                        self.assertIsNone(reader.get_state("test", "key"))
                        raise RuntimeError("abort")
                self.assertEqual(writer.events(), [])
                self.assertIsNone(writer.get_state("test", "key"))

    def test_immutable_event_retry_preserves_processing_time_and_rejects_conflict(self):
        with SQLiteStore(":memory:") as store:
            key = ["s", "c", "a", "7"]
            with store.transaction():
                store.event("test.transition", key, 1000, "source-1", {"value": 1})
            before = store.events()
            with store.transaction():
                store.event("test.transition", key, 1000, "source-1", {"value": 1})
            self.assertEqual(store.events(), before)
            with self.assertRaisesRegex(ValueError, "Conflicting derived event"):
                with store.transaction():
                    store.put_state("test", "key", {"value": "must roll back"})
                    store.event("test.transition", key, 1000, "source-1", {"value": 2})
            self.assertEqual(store.events(), before)
            self.assertIsNone(store.get_state("test", "key"))

    def test_store_mutations_require_explicit_transaction(self):
        with SQLiteStore(":memory:") as store:
            for mutation in (
                lambda: store.put_state("test", "key", {}),
                lambda: store.event("test", [], 0, "source", {}),
                lambda: store.put_input({}, "observation", ("s", "c", "a"), "7"),
            ):
                with self.assertRaisesRegex(RuntimeError, "explicit transaction"):
                    mutation()

    def test_unsupported_database_version_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "future.sqlite"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("PRAGMA user_version=99")
                connection.execute("CREATE TABLE user_data(value TEXT)")
                connection.execute("INSERT INTO user_data VALUES ('preserve')")
            with self.assertRaisesRegex(ValueError, "Unsupported application database version"):
                SQLiteStore(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 99)
                self.assertEqual(connection.execute("SELECT value FROM user_data").fetchone()[0], "preserve")
                self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='events'").fetchone())

    def test_legacy_history_is_rejected_without_changing_events_or_config(self):
        with SQLiteStore(":memory:") as store:
            with store.transaction():
                store.put_state("config", "analytics", asdict(CONFIG))
                store.event("legacy.transition", ["s", "c", "a", "7"], 0, "old-source", {})
            before = store.events()
            with self.assertRaisesRegex(ValueError, "semantics changed"):
                Analytics(store, CONFIG)
            self.assertEqual(store.events(), before)
            self.assertIsNone(store.get_state("config", "engine_revision"))

    def test_two_connections_retry_same_inputs_without_duplicate_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.sqlite"
            with SQLiteStore(path) as store:
                Analytics(store, CONFIG)

            def replay_internal_objects():
                with SQLiteStore(path) as store:
                    engine = Analytics(store, CONFIG)
                    return sum(engine.ingest(item) for item in completed_visit())

            with ThreadPoolExecutor(max_workers=2) as workers:
                counts = list(workers.map(lambda _: replay_internal_objects(), range(2)))
            self.assertEqual(sum(counts), len(completed_visit()))
            with SQLiteStore(path) as store:
                engine = Analytics(store, CONFIG)
                self.assertEqual(engine.summary()["queue_completed_visits"], 1)
                self.assertEqual(len(store.events()), 3)

    def test_schema_setup_failure_does_not_leave_partial_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.sqlite"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("CREATE TABLE inputs(unrelated TEXT)")
            with self.assertRaises(sqlite3.OperationalError):
                SQLiteStore(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='events'").fetchone())
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
