"""Streaming MP4 registration, exact source PTS indexing and frame decoding."""

from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path

import av

from app.p1.catalog import Catalog
from app.storage import canonical, processing_time, stable_id


class MediaError(ValueError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def available(video: dict) -> Path:
    path = Path(video["path"])
    if not path.is_file():
        raise FileNotFoundError("Registered video is missing; reimport the original file")
    stat = path.stat()
    if stat.st_size != video["size_bytes"] or stat.st_mtime_ns != video["mtime_ns"]:
        raise MediaError("Registered file changed after indexing; reimport before reviewing")
    return path


def register(catalog: Catalog, path: Path, *, store_id: str, camera_id: str, stream_session_id: str,
             collection_session: str = "", collection_day: str | None = None, display_name: str | None = None):
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError("Local video file does not exist")
    if path.suffix.lower() != ".mp4":
        raise MediaError("Select a local MP4 file")
    if any(not isinstance(s, str) or not s.strip() for s in (store_id, camera_id, stream_session_id)):
        raise MediaError("Store, camera and stream session IDs are required")
    if collection_day:
        from datetime import date
        date.fromisoformat(collection_day)
    initial_stat = path.stat()
    sha = file_sha256(path)
    if path.stat().st_size != initial_stat.st_size or path.stat().st_mtime_ns != initial_stat.st_mtime_ns:
        raise MediaError("File changed while hashing")
    with catalog.connect(write=True) as db:
        existing = db.execute("SELECT * FROM assets WHERE sha=?", (sha,)).fetchone()
        if existing:
            asset_metadata = json.loads(existing["metadata"])
            # Same content may have moved; update the authorized source path/stat.
            db.execute("UPDATE assets SET path=?,size=?,mtime_ns=? WHERE sha=?",
                       (str(path), initial_stat.st_size, initial_stat.st_mtime_ns, sha))
        else:
            db.execute("INSERT INTO assets VALUES (?,?,?,?,?)",
                       (sha, str(path), "{}", initial_stat.st_size, initial_stat.st_mtime_ns))
            try:
                with av.open(str(path)) as container:
                    if "mp4" not in container.format.name and "mov" not in container.format.name:
                        raise MediaError("Input is not an MP4 container")
                    if not container.streams.video:
                        raise MediaError("MP4 has no video stream")
                    stream = container.streams.video[0]
                    previous = first = last = None
                    delta = None
                    variable = False
                    count = 0
                    dimensions = None
                    for frame in container.decode(stream):
                        if frame.pts is None or frame.time_base is None:
                            raise MediaError("Decoded frame has no source PTS/time base; timing cannot be invented")
                        exact = frame.pts * frame.time_base
                        if previous is not None:
                            if exact <= previous:
                                raise MediaError("Non-increasing presentation timestamps are unsupported")
                            gap = exact - previous
                            if delta is not None and gap != delta:
                                variable = True
                            delta = gap
                        if dimensions is not None and dimensions != [frame.width, frame.height]:
                            raise MediaError("Changing frame dimensions require separate assets")
                        dimensions = [frame.width, frame.height]
                        if first is None:
                            first = exact
                        last, previous = exact, exact
                        db.execute("INSERT INTO frames VALUES (?,?,?,?,?,?,?)",
                                   (sha, count, frame.pts, frame.time_base.numerator,
                                    frame.time_base.denominator, float(exact * 1000), int(frame.key_frame)))
                        count += 1
                    if not count:
                        raise MediaError("No decodable video frames")
                    declared_duration = float(stream.duration * stream.time_base * 1000) if stream.duration is not None else None
                    asset_metadata = {
                        "frame_count": count, "frame_dims_px": dimensions,
                        "codec": stream.codec_context.name, "video_stream_index": stream.index,
                        "stream_time_base": [stream.time_base.numerator, stream.time_base.denominator],
                        "first_media_time_ms": float(first * 1000), "last_media_time_ms": float(last * 1000),
                        "first_media_time": [first.numerator, first.denominator],
                        "last_media_time": [last.numerator, last.denominator],
                        "declared_duration_ms": declared_duration,
                        "indexed_span_ms": float((last - first) * 1000),
                        "variable_frame_rate": variable, "timing_quality": "source_pts",
                        "capture_time_utc": None, "clock_status": "playback_only",
                    }
            except av.FFmpegError as error:
                raise MediaError(f"Video cannot be decoded: {error.strerror}") from error
            if path.stat().st_size != initial_stat.st_size or path.stat().st_mtime_ns != initial_stat.st_mtime_ns:
                raise MediaError("File changed while indexing")
            db.execute("UPDATE assets SET metadata=? WHERE sha=?", (canonical(asset_metadata), sha))
        identity = {"store_id": store_id, "camera_id": camera_id, "stream_session_id": stream_session_id,
                    "collection_session": collection_session, "collection_day": collection_day}
        video_id = stable_id([sha, identity])
        metadata = identity | {"display_name": display_name or path.name}
        db.execute("INSERT OR IGNORE INTO videos VALUES (?,?,?,?)",
                   (video_id, sha, canonical(metadata), processing_time()))
    video = catalog.video(video_id)
    manifest = {k: v for k, v in video.items() if k not in ("path", "mtime_ns")}
    manifest["manifest_version"] = "sentient.media/1"
    (catalog.root / "manifests" / f"{video_id}.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return video


def frame_png(catalog: Catalog, video: dict, index: int) -> tuple[bytes, dict]:
    path = available(video)
    target = catalog.frame(video, index)
    exact_target = target["pts"] * Fraction(target["tb_num"], target["tb_den"])
    with av.open(str(path)) as container:
        stream = container.streams[video["video_stream_index"]]
        offset = int(exact_target / stream.time_base)
        container.seek(offset, stream=stream, backward=True, any_frame=False)
        for attempt in range(2):
            for frame in container.decode(stream):
                if frame.pts is None:
                    continue
                exact = frame.pts * frame.time_base
                if exact == exact_target:
                    output = io.BytesIO()
                    frame.to_image().save(output, format="PNG")
                    return output.getvalue(), target
                if exact > exact_target:
                    break
            # Some formats seek beyond the desired keyframe. Decode from start
            # as a bounded-memory correctness fallback, never from assumed FPS.
            container.seek(stream.start_time or 0, stream=stream, backward=True)
    raise MediaError("Indexed frame could not be decoded from the source")
