"""Real recordings with what a real model found in them.

The detector and tracker ran ahead of time (scripts/build_showcase.py), so any
laptop can play the result. Each clip is an MP4 beside a same-named .json.gz.
Nothing here runs a model or generates a detection.

Clips in app/assets/real are licensed for this repository. Footage that cannot be
redistributed stays in the git-ignored data/real-footage folder.
"""

import gzip
import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = (ROOT / "app/assets/real", ROOT / "data/real-footage")


def clips():
    found = {}
    for folder in FOLDERS:
        for video in sorted(folder.glob("*.mp4")) if folder.is_dir() else ():
            data = video.with_suffix(".json.gz")
            if data.is_file():
                found[video.stem] = (video, data)
    return found


def load(data):
    return json.loads(gzip.decompress(data.read_bytes()))


def import_footage(ops):
    """Add each clip as a recording with a finished run, so every view can open it."""
    from app.p1.geometry import save_geometry
    from app.p1.media import register
    from app.simulation import write_run

    added = []
    for name, (video_path, data_path) in clips().items():
        clip = load(data_path)
        video = register(
            ops.catalog,
            video_path,
            store_id=clip["store_id"],
            camera_id=clip["camera_id"],
            stream_session_id=name,
            collection_session=clip["store_id"],
            display_name=clip["name"],
        )
        key = hashlib.sha256((video["id"] + clip["model"]["detector"]).encode()).hexdigest()[:32]
        if (ops.live.root / key / "summary.json").is_file():
            continue
        geometry = save_geometry(ops.catalog, video["id"], clip["geometry"]) if clip.get("geometry") else None
        rows = [
            dict(
                frame_index=index,
                media_ms=float(frame["t"]),
                tracks=[
                    dict(
                        local_track_id=f"local:{box[0]}",
                        position_status="observed" if box[6] else "predicted",
                        box=box[1:5],
                        score=box[5],
                    )
                    for box in frame["boxes"]
                ],
            )
            for index, frame in enumerate(clip["frames"])
        ]
        write_run(ops.live.root, video["id"], (geometry or {}).get("id"), rows, [], key=key)
        summary = ops.live.root / key / "summary.json"
        saved = json.loads(summary.read_text(encoding="utf-8"))
        saved.update(
            simulated=False,
            scored_windows=0,
            threshold=None,
            elapsed_seconds=clip["duration_s"],
            model_id=clip["model"]["detector"],
            execution=f"{clip['model']['detector']} and {clip['model']['tracker']} ran on this recording ahead of time",
        )
        summary.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        added.append(name)
    return added


def router():
    r = APIRouter(prefix="/api/showcase")

    @r.get("")
    def listing():
        rows = []
        for name, (_, data) in clips().items():
            clip = load(data)
            rows.append(dict(id=name, name=clip["name"], store=clip.get("store_name"), duration_s=clip["duration_s"]))
        return sorted(rows, key=lambda row: -row["duration_s"])

    @r.get("/{name}")
    def tracks(name: str):
        clip = clips().get(name)
        if not clip:
            raise HTTPException(404, "No prepared recording")
        return Response(gzip.decompress(clip[1].read_bytes()), media_type="application/json")

    @r.get("/{name}/video.mp4")
    def video(name: str):
        clip = clips().get(name)
        if not clip:
            raise HTTPException(404, "No prepared recording")
        return FileResponse(clip[0], media_type="video/mp4")

    return r
