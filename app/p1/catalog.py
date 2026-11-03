"""Application-owned SQLite catalog, frame index and append-only review history."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from app.p1.source_profile import source_profile, interpret_summary


class Catalog:
    def __init__(self, root: Path):
        self.root = root.resolve()
        for name in ("assets", "uploads", "analyses", "manifests"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.path = self.root / "catalog.sqlite"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS assets (
                    sha TEXT PRIMARY KEY, path TEXT NOT NULL, metadata TEXT NOT NULL,
                    size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS frames (
                    sha TEXT NOT NULL REFERENCES assets(sha), frame_index INTEGER NOT NULL,
                    pts INTEGER NOT NULL, tb_num INTEGER NOT NULL, tb_den INTEGER NOT NULL,
                    media_ms REAL NOT NULL, keyframe INTEGER NOT NULL,
                    PRIMARY KEY(sha,frame_index)
                );
                CREATE INDEX IF NOT EXISTS frame_time ON frames(sha,media_ms,frame_index);
                CREATE TABLE IF NOT EXISTS videos (
                    id TEXT PRIMARY KEY, sha TEXT NOT NULL REFERENCES assets(sha),
                    metadata TEXT NOT NULL, created_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS geometry (
                    id TEXT PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(id),
                    revision INTEGER NOT NULL, payload TEXT NOT NULL, p0_config TEXT NOT NULL,
                    created_utc TEXT NOT NULL, UNIQUE(video_id,revision)
                );
                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY, video_id TEXT REFERENCES videos(id),
                    geometry_id TEXT REFERENCES geometry(id), path TEXT NOT NULL,
                    config TEXT NOT NULL, summary TEXT NOT NULL, created_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS annotations (
                    id TEXT PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(id),
                    analysis_id TEXT NOT NULL REFERENCES analyses(id), target_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, request_id TEXT NOT NULL UNIQUE,
                    request_payload TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(video_id,analysis_id,target_id,revision)
                );
            """)

    @contextmanager
    def connect(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def video(self, video_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT v.*,a.path,a.metadata AS asset_metadata,a.size,a.mtime_ns "
                             "FROM videos v JOIN assets a ON a.sha=v.sha WHERE v.id=?", (video_id,)).fetchone()
        if row is None:
            raise KeyError("Video not registered")
        result = json.loads(row["metadata"]) | json.loads(row["asset_metadata"])
        result = result | {"id": row["id"], "media_sha256": row["sha"], "path": row["path"],
                         "size_bytes": row["size"], "mtime_ns": row["mtime_ns"]}
        return result | {"source_profile": source_profile(result)}

    def videos(self):
        with self.connect() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM videos ORDER BY created_utc DESC,id")]
        return [self.video(key) for key in ids]

    def frame(self, video: dict, index: int) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM frames WHERE sha=? AND frame_index=?",
                             (video["media_sha256"], index)).fetchone()
        if row is None:
            raise KeyError("Frame index out of range")
        return self.frame_value(video, row)

    @staticmethod
    def frame_value(video, row):
        return dict(row) | {"time_base": [row["tb_num"], row["tb_den"]],
                            "playback_time_ms": row["media_ms"],
                            "relative_time_ms": row["media_ms"] - video["first_media_time_ms"],
                            "capture_time_utc": None}

    def seek(self, video, media_ms):
        with self.connect() as db:
            row = db.execute("SELECT * FROM frames WHERE sha=? AND media_ms<=? "
                             "ORDER BY media_ms DESC,frame_index DESC LIMIT 1",
                             (video["media_sha256"], media_ms)).fetchone()
            if row is None:
                row = db.execute("SELECT * FROM frames WHERE sha=? ORDER BY frame_index LIMIT 1",
                                 (video["media_sha256"],)).fetchone()
        return self.frame_value(video, row)

    def analysis(self, analysis_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone()
        if row is None:
            raise KeyError("Analysis not found")
        summary=json.loads(row["summary"])
        if row['video_id']:
            profile=source_profile(self.video(row['video_id']))
            if profile['source_kind']=='sparse_clip':summary=interpret_summary(summary,'sparse_clip')
        return dict(row) | {"summary": summary, "config": json.loads(row["config"])}
