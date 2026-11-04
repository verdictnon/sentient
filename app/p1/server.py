"""Sentient local video workspace. Launch: python -m app.p1.server."""

import argparse
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import tempfile

from fastapi import FastAPI, Form, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.p1.catalog import Catalog
from app.p1.geometry import load_geometry, save_geometry
from app.p1.media import available, frame_png, register
from app.p1.workspace import evidence, export_annotations, review_history, run_analysis, save_review

ROOT = Path(__file__).resolve().parents[2]


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    store_id: str = "demo-store"
    camera_id: str = "camera-a"
    stream_session_id: str = "session-a"
    collection_session: str = ""
    collection_day: str | None = None


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    analysis_id: str
    target_id: str
    request_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0, strict=True)
    reviewer: str = Field(min_length=1)
    decision: str = Field(pattern="^(accepted|rejected|corrected|pending)$")
    label: str
    visibility: str = Field(pattern="^(clear|partial|occluded|unknown)$")
    uncertainty: str = Field(pattern="^(low|medium|high)$")
    notes: str = ""
    start_media_ms: float
    end_media_ms: float


def create_app(data_root: Path | None = None, *, detector_factory=None, model_dir=None, include_generated=None):
    catalog = Catalog(data_root or Path(os.environ.get("SENTIENT_DATA_DIR", str(ROOT / ".local/workspace"))), include_generated=include_generated)
    from app.p2.jobs import JobManager
    from app.p2.api import router as tracking_router
    manager=JobManager(catalog,detector_factory,model_dir)
    from app.action_analysis import ActionAnalysisManager, router as action_router
    actions=ActionAnalysisManager(catalog,manager)
    @asynccontextmanager
    async def lifespan(application):
        operations.start()
        yield
        control_room.shutdown()
        operations.shutdown()
        live.shutdown()
        actions.shutdown()
        manager.shutdown()
    app = FastAPI(title="Sentient Operations", version="2.0.0",lifespan=lifespan)
    from app.live import LiveManager, router as live_router
    live=LiveManager(catalog,manager,actions)
    app.state.live=live
    app.include_router(live_router(live))
    from app.operations import Operations, router as operations_router
    operations=Operations(catalog,manager,actions,live)
    app.state.operations=operations
    app.include_router(operations_router(operations))
    from app.control_room import ControlRoom, router as control_room_router
    control_room=ControlRoom(operations)
    app.state.control_room=control_room
    app.include_router(control_room_router(control_room))
    from app.shelves import router as shelf_router
    app.include_router(shelf_router(manager))
    app.state.catalog = catalog
    app.state.tracking=manager
    app.include_router(tracking_router(manager))
    app.state.actions=actions
    app.include_router(action_router(actions))

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({"detail": str(error.args[0])}, status_code=404)

    @app.exception_handler(FileNotFoundError)
    async def missing_file(request, error):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.exception_handler(OSError)
    async def inaccessible(request, error):
        return JSONResponse({"detail": f"Local file access failed: {error}"}, status_code=422)

    @app.exception_handler(RuntimeError)
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.get("/api/status")
    def status():
        return {"status": "ready", "include_generated": catalog.include_generated, "mode": "local_recorded_video", "capture_utc_inferred": False, "isolated_replays": True, "demo": actions.demo_entry, "demo_library": actions.demo_library}

    @app.get("/api/videos")
    def videos():
        result = catalog.videos()
        for row in result:
            try:
                available(row)
                row["availability"] = "ready"
            except (OSError, ValueError):
                row["availability"] = "missing_or_changed"
        return result

    @app.post("/api/videos/import")
    def import_path(body: ImportRequest):
        fields = body.model_dump()
        path = Path(fields.pop("path"))
        return register(catalog, path, **fields)

    @app.post("/api/videos/upload")
    def upload(file: UploadFile, store_id: str = Form("demo-store"), camera_id: str = Form("camera-a"),
               stream_session_id: str = Form("session-a"), collection_session: str = Form(""),
               collection_day: str | None = Form(None)):
        if not file.filename or not file.filename.lower().endswith(".mp4"):
            raise ValueError("Upload an MP4 file")
        from app.p1.media import file_sha256
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=catalog.root / "uploads", suffix=".mp4", delete=False) as output:
                temp_path = Path(output.name)
                while chunk := file.file.read(1024 * 1024):
                    output.write(chunk)
            sha = file_sha256(temp_path)
            managed = catalog.root / "assets" / f"{sha}.mp4"
            # Validate before making an uploaded file a permanent managed asset.
            result = register(catalog, temp_path, store_id=store_id, camera_id=camera_id,
                              stream_session_id=stream_session_id, collection_session=collection_session,
                              collection_day=collection_day, display_name=Path(file.filename).name)
            if not managed.exists():
                temp_path.replace(managed)
            stat = managed.stat()
            with catalog.connect(write=True) as db:
                db.execute("UPDATE assets SET path=?,size=?,mtime_ns=? WHERE sha=?",
                           (str(managed), stat.st_size, stat.st_mtime_ns, sha))
            return catalog.video(result["id"])
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()
            file.file.close()

    @app.get("/api/videos/{video_id}")
    def video(video_id: str):
        return catalog.video(video_id)

    @app.get("/api/videos/{video_id}/manifest")
    def manifest(video_id: str):
        catalog.video(video_id)
        return FileResponse(catalog.root / "manifests" / f"{video_id}.json", media_type="application/json")

    @app.get("/api/videos/{video_id}/content")
    def content(video_id: str):
        row = catalog.video(video_id)
        return FileResponse(available(row), media_type="video/mp4")

    @app.get("/api/videos/{video_id}/frames")
    def frames(video_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
        video = catalog.video(video_id)
        with catalog.connect() as db:
            rows = db.execute("SELECT * FROM frames WHERE sha=? ORDER BY frame_index LIMIT ? OFFSET ?",
                              (video["media_sha256"], limit, offset)).fetchall()
        return [catalog.frame_value(video, row) for row in rows]

    @app.get("/api/videos/{video_id}/seek")
    def seek(video_id: str, media_ms: float = Query(allow_inf_nan=False)):
        return catalog.seek(catalog.video(video_id), media_ms)

    @app.get("/api/videos/{video_id}/frame/{index}")
    def frame(video_id: str, index: int):
        data, row = frame_png(catalog, catalog.video(video_id), index)
        return Response(data, media_type="image/png", headers={"X-Frame-Index": str(index),
                        "X-Media-PTS": str(row["pts"]), "X-Time-Base": f'{row["tb_num"]}/{row["tb_den"]}'})

    @app.get("/api/videos/{video_id}/geometry")
    def geometry(video_id: str, geometry_id: str | None = None):
        return load_geometry(catalog, video_id, geometry_id)

    @app.put("/api/videos/{video_id}/geometry")
    def put_geometry(video_id: str, body: dict):
        return save_geometry(catalog, video_id, body)

    @app.get("/api/videos/{video_id}/geometry/export")
    def export_geometry(video_id: str):
        saved = load_geometry(catalog, video_id)
        return Response(json.dumps(saved["geometry"], indent=2), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="sentient-geometry.json"'})

    @app.get("/api/videos/{video_id}/geometry/p0")
    def export_p0_geometry(video_id: str):
        return load_geometry(catalog, video_id)["p0_config"]

    @app.get("/api/analyses")
    def analyses(video_id: str | None = None):
        with catalog.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM analyses " +
                   ("WHERE video_id=? " if video_id else "") + "ORDER BY created_utc DESC,id",
                   (video_id,) if video_id else ())]
        return [catalog.analysis(key) for key in ids]

    @app.post("/api/replays")
    def import_records(file: UploadFile, video_id: str | None = Form(None), geometry_id: str | None = Form(None), isolated_run: bool = Form(False), tracking_job_id: str | None = Form(None)):
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=catalog.root / "uploads", suffix=".jsonl", delete=False) as output:
                temp_path = Path(output.name)
                total = 0
                while chunk := file.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > 64 * 1024 * 1024:
                        raise ValueError("Split tracking JSONL into batches smaller than 64 MiB")
                    output.write(chunk)
            tracking=manager.get(tracking_job_id) if tracking_job_id else None
            if tracking:
                if tracking['status']!='completed' or tracking['video_id']!=video_id or tracking['geometry_id']!=geometry_id:
                    raise ValueError('Tracking replay requires a completed job bound to this video and geometry')
            return run_analysis(catalog, temp_path, video_id, geometry_id, isolated_run,
                source_kind=tracking['config'].get('source_kind') if tracking else None,
                analytics_config_override=tracking['p0_config'] if tracking else None)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()
            file.file.close()

    @app.get("/api/analyses/{analysis_id}/events")
    def events(analysis_id: str):
        return evidence(catalog, analysis_id)

    @app.get("/api/analyses/{analysis_id}/reviews")
    def reviews(analysis_id: str, target_id: str):
        catalog.analysis(analysis_id)
        return review_history(catalog, analysis_id, target_id)

    @app.post("/api/videos/{video_id}/reviews")
    def review(video_id: str, body: ReviewRequest):
        if not body.reviewer.strip():
            raise ValueError("Reviewer name is required")
        return save_review(catalog, video_id, body.model_dump())

    @app.get("/api/annotations/export")
    def export(video_id: str | None = None, latest_only: bool = False):
        if video_id:
            catalog.video(video_id)
        return StreamingResponse(export_annotations(catalog, video_id, latest_only),
                                 media_type="application/x-ndjson",
                                 headers={"Content-Disposition": 'attachment; filename="sentient-annotations.jsonl"'})

    @app.get("/guide")
    def guide():
        return FileResponse(ROOT / "docs/DEMO_GUIDE.md", media_type="text/plain")

    app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".local/workspace")
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(args.data_dir), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
