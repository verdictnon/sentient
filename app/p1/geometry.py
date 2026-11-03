"""Named image-space geometry and an explicit mapping to the P0 configuration."""

from app.geometry import Line, Polygon
from app.storage import canonical, processing_time, stable_id
import math


def default_geometry(dimensions):
    return {"geometry_version": 2, "frame_dims_px": dimensions, "max_gap_ms": 1500,
            "shapes": [], "active": {"line": None, "zone": None, "queue": None, "checkout": None}}


def validate_geometry(value: dict, dimensions: list) -> dict:
    if set(value) != {"geometry_version", "frame_dims_px", "max_gap_ms", "shapes", "active"}:
        raise ValueError("Unexpected geometry fields")
    if type(value["geometry_version"]) is not int or value["geometry_version"] not in (1,2):
        raise ValueError("Unsupported geometry version")
    if value["frame_dims_px"] != dimensions:
        raise ValueError("Geometry dimensions must match the original video frame")
    if type(value["max_gap_ms"]) is not int or value["max_gap_ms"] <= 0:
        raise ValueError("Maximum observation gap must be positive integer milliseconds")
    if not isinstance(value["shapes"], list):
        raise ValueError("Shapes must be an array")
    shapes = {}
    width, height = dimensions
    for shape in value["shapes"]:
        if not isinstance(shape, dict):
            raise ValueError("Every shape must be an object")
        fields = {"id", "name", "kind", "points"}
        if shape.get("kind") == "line":
            fields.add("hysteresis_px")
        if set(shape) != fields or shape["kind"] not in ("line", "zone", "queue", "checkout", "shelf", "exit"):
            raise ValueError("Invalid shape fields or kind")
        if any(not isinstance(shape[k], str) or not shape[k].strip() for k in ("id", "name")):
            raise ValueError("Each shape requires an ID and name")
        if shape["id"] in shapes:
            raise ValueError("Shape IDs must be unique")
        if not isinstance(shape["points"], list) or any(
            not isinstance(p, list) or len(p) != 2 for p in shape["points"]
        ):
            raise ValueError("Shape points must be pairs of image coordinates")
        points = tuple(tuple(p) for p in shape["points"])
        if shape["kind"] == "line":
            if type(shape["hysteresis_px"]) not in (int, float) or not math.isfinite(shape["hysteresis_px"]):
                raise ValueError("Line hysteresis must be a finite number")
            if len(points) != 2:
                raise ValueError("Directional lines require two endpoints")
            Line(*points, shape["hysteresis_px"])
        else:
            Polygon(points)
        if any(not (0 <= x <= width and 0 <= y <= height) for x, y in points):
            raise ValueError("Shape coordinates must be inside the original image dimensions")
        shapes[shape["id"]] = shape
    active = value["active"]
    if not isinstance(active, dict) or set(active) != {"line", "zone", "queue", "checkout"}:
        raise ValueError("Specify the active entrance, dwell, queue and checkout shapes")
    for kind, key in active.items():
        if key is None and (kind in ("queue", "checkout") or value["geometry_version"]==2):
            continue
        if not isinstance(key, str) or key not in shapes or shapes[key]["kind"] != kind:
            raise ValueError(f"Select an active {kind} shape")
    if (active["queue"] is None) != (active["checkout"] is None):
        raise ValueError("Queue and checkout service areas must be enabled together")
    # Unconfigured legacy kernel inputs stay outside the image. New live/shelf
    # analytics use only explicitly drawn shapes; missing entrances stay unknown.
    line = shapes[active["line"]] if active["line"] else {"points":[[-20,-20],[-20,-10]],"hysteresis_px":5}
    zone = shapes[active["zone"]] if active["zone"] else {"points":[[-20,-20],[-10,-20],[-10,-10],[-20,-10]]}
    result = {"application_config_version": 1, "line": {
        "start": line["points"][0], "end": line["points"][1], "hysteresis_px": line["hysteresis_px"],
    }, "zone": zone["points"], "max_gap_ms": value["max_gap_ms"]}
    if active["queue"] is not None:
        result["queue"] = {"waiting_zone": shapes[active["queue"]]["points"],
                           "service_zone": shapes[active["checkout"]]["points"]}
    return result


def save_geometry(catalog, video_id, payload):
    video = catalog.video(video_id)
    p0 = validate_geometry(payload, video["frame_dims_px"])
    geometry_id = stable_id([video_id, payload])
    with catalog.connect(write=True) as db:
        row = db.execute("SELECT * FROM geometry WHERE id=?", (geometry_id,)).fetchone()
        if row is None:
            revision = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM geometry WHERE video_id=?", (video_id,)).fetchone()[0]
            db.execute("INSERT INTO geometry VALUES (?,?,?,?,?,?)",
                       (geometry_id, video_id, revision, canonical(payload), canonical(p0), processing_time()))
        else:
            revision = row["revision"]
    return {"id": geometry_id, "video_id": video_id, "revision": revision, "geometry": payload, "p0_config": p0}


def load_geometry(catalog, video_id, geometry_id=None):
    import json
    catalog.video(video_id)
    with catalog.connect() as db:
        row = db.execute("SELECT * FROM geometry WHERE video_id=? " +
                         ("AND id=?" if geometry_id else "ORDER BY revision DESC LIMIT 1"),
                         (video_id, geometry_id) if geometry_id else (video_id,)).fetchone()
    if row is None:
        if geometry_id:
            raise KeyError("Geometry revision not found for this video")
        return save_geometry(catalog, video_id, default_geometry(catalog.video(video_id)["frame_dims_px"]))
    return {"id": row["id"], "video_id": video_id, "revision": row["revision"],
            "geometry": json.loads(row["payload"]), "p0_config": json.loads(row["p0_config"])}
