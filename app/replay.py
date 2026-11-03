"""Validate and atomically replay original v1 JSONL into SQLite analytics."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Sequence

from app.adapter import ContractAdapter
from app.config import load_replay_config
from app.contracts import ContractError, ContractValidator
from app.preflight import missing_p0_inputs
from app.storage import SQLiteStore


def replay(input_path: Path, config_path: Path, db_path: Path, *, finalize: bool = True) -> dict:
    """Reject invalid batches without committing partial inputs or events.

    Structural/local semantic validation precedes database creation. Stateful
    checks and all derived writes run in one transaction. Input order is kept;
    records are never sorted or assigned invented source timestamps.
    """
    validator = ContractValidator()
    config = load_replay_config(config_path)
    records = validator.read_jsonl(input_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteStore(db_path) as store:
        with store.transaction():
            adapter = ContractAdapter(store, config, validator)
            before = len(store.events())
            inserted = 0
            for number, row in records:
                try:
                    inserted += adapter.ingest(row)
                except (ValueError, TypeError, OverflowError) as error:
                    raise ContractError(f"{input_path.name}:{number} [{row['record_id']}]: {error}") from error
            if finalize:
                adapter.engine.finish()
            events, inputs = store.events(), store.inputs()
            summary = adapter.engine.summary()
            return {
                "schema_version": "1.0",
                "records_read": len(records),
                "records_inserted": inserted,
                "records_duplicate": len(records) - inserted,
                "database_records": len(inputs),
                "events_total": len(events),
                "events_added": len(events) - before,
                **summary,
                "complete_observed_dwell_seconds": summary["complete_observed_dwell_ms"] / 1000,
                "stored_action_results": sum(row["kind"] == "action.scored" for row in inputs),
                "confirmed_physical_actions": sum(e["kind"] == "action.confirmed" for e in events),
                "finalized": finalize,
            }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--no-finalize", action="store_true", help="Preserve open intervals for the next input batch.")
    args = parser.parse_args(argv)
    missing = missing_p0_inputs(Path(__file__).resolve().parent.parent)
    if missing:
        print("Replay blocked; original handoff files are missing:")
        for path in missing:
            print(f"  {path.as_posix()}")
        print("No input was consumed and no database was created.")
        return 2
    try:
        result = replay(args.input, args.config, args.db, finalize=not args.no_finalize)
    except (OSError, ValueError, TypeError, OverflowError, sqlite3.Error) as error:
        print(f"Replay rejected: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
